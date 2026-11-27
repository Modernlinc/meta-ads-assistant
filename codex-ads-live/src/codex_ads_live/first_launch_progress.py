"""Resume one planned launch with operator notes or trusted host readback.

Local declarations never confer Meta access or approval. Preserve the original
operation while its outcome is unknown and correlate observations by its digest.
"""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .client_workspace import journal_for, load, workspace_identity
from .first_launch_activation_host import activation_operation, validate_activation_attempt
from .first_launch_host import CREATION_READBACK_SCHEMA as READBACK
from .first_launch_host import CreationBinding, dispatch_creation, graph_post_request, recover_creation, validate_attempt
from .first_launch_plan import OPERATIONS, _goal_conflicts, _read_plan_json, current_plan, plan_directory, scoped_draft, stage_specs
from .first_launch_preview import PREVIEW_FORMAT
from .journal import Journal, canonical, digest
from .local_paths import checked_path
from .meta_write_mapping import advanced_targeting, mapped_request, review_draft, utcnow
from .validation import InputError, aware_time, fail, object_only


SCHEMA = 'first-launch-progress-v1'
PREVIEW_OBSERVATION = 'first-launch-preview-observation-v1'
LEVELS = tuple(operation for operation, _, _ in OPERATIONS)
HEX = re.compile('[a-f0-9]{64}')
REMOTE_ID = re.compile('[1-9][0-9]{0,39}')


def _sealed(row):
    return digest({key: value for key, value in row.items() if key != 'stage_sha256'})


def _ready(item):
    record = item['readback']
    return record is not None and record['state'] in ('PAUSED', 'CREATIVE_OBSERVED')


def _validate_record(record, reservation):
    keys = ('schema', 'stage_sha256', 'state', 'evidence_ref', 'observed_at', 'object')
    object_only(record, keys, keys)
    if record['schema'] != READBACK or record['stage_sha256'] != reservation['stage_sha256']:
        fail('FIRST_LAUNCH_READBACK_SCOPE_MISMATCH')
    reference = record['evidence_ref']
    if type(reference) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', reference):
        fail('FIRST_LAUNCH_READBACK_REFERENCE_REQUIRED')
    if aware_time(record['observed_at']) < aware_time(reservation['reserved_at']):
        fail('FIRST_LAUNCH_READBACK_PRECEDES_RESERVATION')
    expected = reservation['expected_object']
    allowed = ('PAUSED', 'DRAFT') if 'configured_status' in expected else ('CREATIVE_OBSERVED',)
    state, observed = record['state'], record['object']
    if state not in allowed:
        fail('FIRST_LAUNCH_READBACK_STATUS_UNSUPPORTED')
    fields = {'id', *expected}
    object_only(observed, fields, fields)
    if type(observed['id']) is not str or not REMOTE_ID.fullmatch(observed['id']):
        fail('FIRST_LAUNCH_READBACK_OBJECT_ID_REQUIRED')
    compare = expected | {'configured_status': state} if 'configured_status' in expected else expected
    if any(type(observed[key]) is not type(value) or observed[key] != value for key,value in compare.items()):
        fail('FIRST_LAUNCH_READBACK_OBJECT_MISMATCH')


def _selected_rows(rows, ad_id, external=None):
    if type(ad_id) is not str or not REMOTE_ID.fullmatch(ad_id):
        fail('FIRST_LAUNCH_BATCH_AD_NOT_RECORDED')
    by_id = {row['readback']['object']['id']: row for row in rows if _ready(row)}
    chosen = by_id.get(ad_id)
    if chosen is None or chosen['reservation']['operation'] != 'CREATE_AD':
        fail('FIRST_LAUNCH_BATCH_AD_NOT_RECORDED')
    ad = chosen['readback']['object']
    chain = []
    for field, kind in (('campaign_id', 'CAMPAIGN'), ('adset_id', 'ADSET'), ('creative_id', 'CREATIVE')):
        row = by_id.get(ad[field])
        if row is None and external and ad[field] in external.get(kind, ()):
            continue
        if row is None or row['reservation']['operation'] != 'CREATE_' + kind:
            fail('FIRST_LAUNCH_BATCH_AD_DEPENDENCY_NOT_RECORDED')
        chain.append(row)
    return [*chain, chosen]


def _validate_selected_attempt(activation, rows, plan_sha):
    validate_activation_attempt(activation, digest(rows))
    observed = activation['observation']
    ad = rows[-1]['readback']['object']
    if observed is not None and (observed['ad_id'], observed['account_id']) != (ad['id'], ad['account_id']):
        fail('FIRST_LAUNCH_ACTIVATION_READBACK_MISMATCH')
    effect = activation.get('guard_effect')
    if effect is not None and any(effect[key] != ad[field] for key, field in (
        ('ad_id', 'id'), ('account_id', 'account_id'), ('campaign_id', 'campaign_id'),
        ('adset_id', 'adset_id'), ('creative_id', 'creative_id')
    )):
        fail('FIRST_LAUNCH_ACTIVATION_EFFECT_MISMATCH')
    frozen = activation.get('frozen_review')
    if frozen is not None and any(frozen['binding'].get(key) != value for key, value in (
        ('plan_sha256', plan_sha), ('ad_id', ad['id']), ('account_id', ad['account_id']),
        ('campaign_id', ad['campaign_id']), ('adset_id', ad['adset_id']), ('creative_id', ad['creative_id'])
    )):
        fail('FIRST_LAUNCH_ACTIVATION_FROZEN_REVIEW_MISMATCH')


def _validate_state(value, plan_sha, levels=LEVELS):
    keys = ('schema', 'plan_sha256', 'stages', 'checkpoint')
    object_only(value, (*keys, 'activation_attempt', 'manual_activation_attempt', 'batch_configuration_sha256',
                        'batch_activation_attempts'), keys)
    if value['schema'] != SCHEMA or value['plan_sha256'] != plan_sha:
        fail('FIRST_LAUNCH_PROGRESS_PLAN_CHANGED')
    rows = value['stages']
    if type(rows) is not list or len(rows) > len(levels):
        fail('FIRST_LAUNCH_PROGRESS_SEQUENCE')
    if len(levels) > len(LEVELS) and rows and not HEX.fullmatch(value.get('batch_configuration_sha256', '')):
        fail('FIRST_LAUNCH_BATCH_CONFIGURATION_REQUIRED')
    ids = set()
    configurations = set()
    for index, row in enumerate(rows):
        object_only(row, ('reservation', 'readback', 'host_attempt'), ('reservation', 'readback'))
        reservation = row['reservation']
        keys = ('plan_sha256', 'operation', 'draft_sha256', 'request_sha256',
                'expected_object', 'reserved_at', 'stage_sha256')
        object_only(reservation, keys, keys)
        if (reservation['plan_sha256'] != plan_sha or reservation['operation'] != levels[index]
                or type(reservation['draft_sha256']) is not str or not HEX.fullmatch(reservation['draft_sha256'])
                or type(reservation['request_sha256']) is not str or not HEX.fullmatch(reservation['request_sha256'])
                or reservation['stage_sha256'] != _sealed(reservation)
                or type(reservation['expected_object']) is not dict):
            fail('FIRST_LAUNCH_PROGRESS_RESERVATION_INVALID')
        aware_time(reservation['reserved_at'])
        record = row['readback']
        attempt = row.get('host_attempt')
        if attempt is not None:
            binding = validate_attempt(attempt)
            if aware_time(attempt['reserved_at']) < aware_time(reservation['reserved_at']):
                fail('FIRST_LAUNCH_HOST_ATTEMPT_PRECEDES_RESERVATION')
            if binding is not None:
                if (any(getattr(binding,key) != reservation[key] for key in (
                        'plan_sha256','stage_sha256','draft_sha256','request_sha256','operation'))
                        or binding.account_id != reservation['expected_object'].get('account_id')
                        or binding.dependency_sha256 != digest(rows[:index])):
                    fail('FIRST_LAUNCH_HOST_BINDING_INVALID')
                configurations.add(binding.configuration_sha256)
        if record is not None:
            _validate_record(record, reservation)
            object_id = record['object']['id']
            if attempt is not None and (attempt['acknowledged_object_id'] not in (None,object_id)
                    or aware_time(record['observed_at']) < aware_time(attempt['reserved_at'])):
                fail('FIRST_LAUNCH_HOST_READBACK_MISMATCH')
            if object_id in ids:
                fail('FIRST_LAUNCH_READBACK_OBJECT_REUSED')
            ids.add(object_id)
        if index < len(rows)-1 and not _ready(row):
            fail('FIRST_LAUNCH_PROGRESS_SEQUENCE')
    if len(configurations)>1:
        fail('FIRST_LAUNCH_HOST_CONFIGURATION_CHANGED')
    if 'manual_activation_attempt' in value:
        if 'activation_attempt' in value or len(rows) != len(levels) or not _ready(rows[-1]):
            fail('FIRST_LAUNCH_ACTIVATION_SOURCE_CONFLICT')
        from .first_launch_handoff import validate_manual_attempt
        validate_manual_attempt(value['manual_activation_attempt'], rows, plan_sha)
    if 'activation_attempt' in value:
        if len(rows) != len(levels) or not _ready(rows[-1]):
            fail('FIRST_LAUNCH_ACTIVATION_CHAIN_REQUIRED')
        _validate_selected_attempt(value['activation_attempt'], rows, plan_sha)
    if 'batch_activation_attempts' in value:
        attempts = value['batch_activation_attempts']
        if type(attempts) is not dict or len(levels) <= len(LEVELS) or len(rows) != len(levels) or not _ready(rows[-1]):
            fail('FIRST_LAUNCH_ACTIVATION_CHAIN_REQUIRED')
        if rows[-1]['readback']['object']['id'] in attempts:
            fail('FIRST_LAUNCH_ACTIVATION_SOURCE_CONFLICT')
        for ad_id, attempt in attempts.items():
            _validate_selected_attempt(attempt, _selected_rows(rows, ad_id), plan_sha)
    checkpoint = value['checkpoint']
    if checkpoint is not None:
        keys = ('decision', 'evidence_ref', 'next_review_on')
        object_only(checkpoint, keys, keys)
        if checkpoint['decision'] not in ('KEEP','HOLD','REVIEW_CHANGE'):
            fail('FIRST_LAUNCH_CHECKPOINT_DECISION')
        reference = checkpoint['evidence_ref']
        if type(reference) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', reference):
            fail('FIRST_LAUNCH_CHECKPOINT_REFERENCE_REQUIRED')
        try:
            iso_day = checkpoint['next_review_on']
            if type(iso_day) is not str or date.fromisoformat(iso_day).isoformat() != iso_day:
                fail('FIRST_LAUNCH_CHECKPOINT_DATE')
        except ValueError:
            fail('FIRST_LAUNCH_CHECKPOINT_DATE')


def _external_parents(plan):
    strategy = (plan.get('operator_intake') or {}).get('managed_strategy', {})
    result = {}
    for node in strategy.get('nodes', ()):
        if node.get('remote_id') is not None:
            result.setdefault(node['kind'], []).append(node['remote_id'])
    return result


def _stage(root, plan, position, prior):
    operation, filename, dependencies, selection = stage_specs(plan)[position]
    draft = scoped_draft(root, plan, operation, filename, selection)
    params = draft['parameters']
    ids = [row['readback']['object']['id'] for row in prior]
    for key, predecessor in dependencies.items():
        resolved = predecessor if type(predecessor) is str else ids[predecessor]
        bound = {'creative_id': resolved} if key == 'creative' else resolved
        if params[key] is not None and params[key] != bound:
            fail('FIRST_LAUNCH_DEPENDENCY_CONFLICT')
        params[key] = bound
    expected = {'account_id':plan['account_id']}
    if operation == 'CREATE_CAMPAIGN':
        expected.update(objective=params['objective'], configured_status='PAUSED')
        for field in ('spend_cap', 'daily_budget', 'lifetime_budget'):
            if params.get(field) is not None:
                expected[field + '_minor'] = params[field]
    elif operation == 'CREATE_ADSET':
        expected.update(campaign_id=params['campaign_id'], optimization_goal=params['optimization_goal'],
                        configured_status='PAUSED')
        if not draft['context'].get('parents_hold_budget'):
            by_lifetime = params.get('lifetime_budget') is not None
            budget = 'lifetime_budget' if by_lifetime else 'daily_budget'
            expected[budget + '_minor'] = params.get(budget)
        if params.get('promoted_object') is not None:
            expected['promoted_object'] = params['promoted_object']
        if params.get('targeting') is not None:
            selected_targeting = advanced_targeting(params['targeting'])
            if selected_targeting:
                expected['selected_targeting'] = selected_targeting
        if params.get('is_dynamic_creative') is True:
            expected['is_dynamic_creative'] = True
        if params.get('start_time') is not None:
            from datetime import timezone
            expected.update(start_time=aware_time(params['start_time']).astimezone(timezone.utc).isoformat(),
                            end_time=aware_time(params['end_time']).astimezone(timezone.utc).isoformat())
    elif operation == 'CREATE_CREATIVE':
        expected.update(page_id=plan['page_id'], destination_url=params['destination_url'])
        if params.get('url_tags') is not None:
            expected['url_tags'] = params['url_tags']
        if params.get('dynamic_media') is not None:
            from .dynamic_creative import build

            if all(params.get(key) is not None for key in ('destination_url','primary_text','headline','description','call_to_action')):
                expected['asset_feed_sha256'] = digest(build(params))
        elif params.get('video') is not None:
            expected.update(video_id=params['video']['video_id'], thumbnail_hash=params['video']['thumbnail_hash'])
        elif params.get('carousel') is not None:
            expected['carousel'] = params['carousel']
        else:
            expected['image_hash'] = params['image_hash']
    else:
        parents = _external_parents(plan).get('CAMPAIGN', [])
        expected.update(campaign_id=parents[0] if parents else ids[0], adset_id=params['adset_id'],
                        creative_id=params['creative']['creative_id'], configured_status='PAUSED')
    return draft, expected


def _draft_identity(draft):
    return digest(dict(operation=draft['operation'], account_id=draft['account_id'], api_version=draft['api_version'],
                       currency=draft['context']['currency'], timezone=draft['context']['timezone'],
                       page_id=draft['context']['page_id'], material_sha256=draft['context']['material_sha256'],
                       parameters=draft['parameters'],
                       **{key: draft['context'][key] for key in ('source_campaign_id', 'parents_hold_budget')
                          if key in draft['context']}))


def _request_preview(draft):
    # Port the upstream form convention for nested values. Authentication is
    # supplied by an independently approved provider and is absent here.
    fields = {name: value for name, value in draft['parameters'].items() if value is not None}
    request = mapped_request(draft, fields)
    form = {key: value if type(value) is str else canonical(value)
            for key, value in sorted(request['parameters'].items())}
    proposal = graph_post_request(request['path'], urlencode(form))
    return dict(**proposal, request_sha256=digest(proposal), dispatchable=False,
                required_gate='TRUSTED_APPROVAL_AND_SCOPED_PROVIDER_REQUIRED')


def _tracking_review(destination):
    if destination is None:
        return 'DESTINATION_NOT_PROVIDED'
    keys = [key.lower() for key, _ in parse_qsl(urlsplit(destination).query, keep_blank_values=True)
            if key.lower().startswith('utm_')]
    if len(set(keys)) != len(keys):
        return 'DUPLICATE_UTM_KEYS_REVIEW'
    return 'DECLARED_UTM_PRESERVED' if keys else 'UTM_NOT_PRESENT'


def _budget_review(plan, draft):
    declared = plan['business_budget']
    currency = draft['context']['currency']
    minor = draft['parameters'].get('daily_budget')
    cap = declared['maximum_test_spend']
    reason = None
    if declared['currency'] is not None and currency is not None and declared['currency'] != currency:
        reason = 'FIRST_LAUNCH_BUSINESS_BUDGET_CURRENCY_MISMATCH'
    elif cap is not None and minor is not None and currency == declared['currency']:
        daily = Decimal(minor).scaleb(0 if currency == 'JPY' else -2)
        if daily > Decimal(cap):
            reason = 'FIRST_LAUNCH_DAILY_BUDGET_EXCEEDS_DECLARED_TEST_TOTAL'
    return dict(currency=currency, daily_budget_minor=minor, declared_test_cap=cap,
                entitlement_and_approved_window='REQUIRES_SEPARATE_EVIDENCE', conflict=reason)


def _launch_configuration(root, plan, rows, clock):
    """Preflight every known input without inventing the IDs of future objects."""
    configuration = []
    issues = {}
    checked_at = clock()
    specs = stage_specs(plan)
    ids = {index: row['readback']['object']['id'] for index, row in enumerate(rows) if _ready(row)}
    for operation, filename, dependencies, selection in specs:
        draft = scoped_draft(root,plan,operation,filename,selection)
        report = review_draft(draft,clock=lambda:checked_at)
        missing = [key for key in report['missing_fields'] if key.startswith('parameters.')
                   and key.removeprefix('parameters.') not in dependencies]
        if operation != 'CREATE_AD':
            missing += [key for key in report['missing_fields'] if
                        key in ('context.currency','context.timezone','context.material_sha256')]
        conflicts = _goal_conflicts(plan,operation,draft['parameters'])
        for field, predecessor in dependencies.items():
            known = predecessor if type(predecessor) is str else ids.get(predecessor)
            expected = {'creative_id':known} if known is not None and field=='creative' else known
            if draft['parameters'][field] not in (None,expected):
                conflicts.append('FIRST_LAUNCH_DEPENDENCY_CONFLICT')
        if operation == 'CREATE_ADSET':
            budget = _budget_review(plan,draft)
            if budget['conflict']:
                conflicts.append(budget['conflict'])
        if missing or conflicts:
            issues[operation if len(specs) == 4 else filename] = dict(missing_fields=missing,conflicts=conflicts)
        context = draft['context']
        configuration.append(dict(operation=operation, currency=context['currency'] if operation!='CREATE_AD' else None,
                                  timezone=context['timezone'] if operation!='CREATE_AD' else None,
                                  source_material_sha256=context['material_sha256'] if operation=='CREATE_CREATIVE' else None,
                                  parameters={key:value for key,value in draft['parameters'].items() if key not in dependencies},
                                  depends_on={field: (parent if type(parent) is str else specs[parent][0])
                                              for field, parent in dependencies.items()} if len(specs)==4
                                  else dependencies))
    identity = digest(dict(account_id=plan['account_id'],page_id=plan['page_id'],configuration=configuration))
    saved = next((row['host_attempt']['binding']['configuration_sha256'] for row in rows
                  if (row.get('host_attempt') or {}).get('binding') is not None),None)
    return dict(configuration_sha256=identity,issues=issues,changed_since_authorization=saved not in (None,identity))


def _activation_budget(chain, context):
    settings = context.get('selected_budget')
    if not settings:
        adset = next((item['readback']['object'] for item in chain
                      if item['reservation']['operation'] == 'CREATE_ADSET'), {})
        return dict(context, daily_budget_minor=adset.get('daily_budget_minor'))
    owner, schedule = settings['owner'], settings['schedule']
    operation = 'CREATE_' + owner
    selected = next((item['readback']['object'] for item in chain
                    if item['reservation']['operation'] == operation), {})
    field = 'daily_budget_minor' if schedule == 'DAILY' else 'lifetime_budget_minor'
    return dict(context, daily_budget_minor=selected.get(field) if schedule == 'DAILY' else None,
                selected_budget=dict(settings, budget_minor=selected.get(field)))


def _base_view(plan,state,workspace_sha):
    return dict(account_id=plan['account_id'], page_id=plan['page_id'], plan_sha256=state['plan_sha256'],
                app_id=plan['app_id'], business_id=plan['business_id'],
                workspace_identity_sha256=workspace_sha, declared_goal=plan['declared_goal'],
                declared_result_type=plan['declared_result_type'], stages=state['stages'],
                checkpoint=state['checkpoint'], preview=None, network_used=False, write_authorized=False,
                manual_activation_attempt=state.get('manual_activation_attempt'))


def _view(root, plan, state, workspace_sha, clock):
    rows = state['stages']
    response = _base_view(plan,state,workspace_sha)
    if 'creative_research' in plan:
        response['creative_research'] = plan['creative_research']
    specs = stage_specs(plan)
    external = _external_parents(plan)
    if external:
        response['named_existing_parents'] = external
    if 'creative_research_batch' in plan:
        response['creative_research_batch'] = plan['creative_research_batch']
        response['batch_items'] = [
            dict(item_id=digest([state['plan_sha256'], index, operation, selection]),
                 operation=operation, file=filename, depends_on=dependencies,
                 material_sha256=selection['asset_sha256'] if selection else None,
                 object_id=(rows[index]['readback']['object']['id']
                            if index < len(rows) and _ready(rows[index]) else None),
                 state=('READBACK_MATCHED' if _ready(rows[index]) else
                        'OUTCOME_UNKNOWN' if rows[index].get('host_attempt') is not None else
                        'AWAITING_VERIFIED_OBJECT') if index < len(rows) else 'NOT_SENT')
            for index, (operation, filename, dependencies, selection) in enumerate(specs)
        ]
        frozen = state.get('batch_configuration_sha256')
        if frozen is not None and _launch_configuration(root, plan, rows, clock)['configuration_sha256'] != frozen:
            response.update(status='FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED', changed_stage='FROZEN_BATCH')
            return response
    for position, row in enumerate(rows):
        draft, expected = _stage(root, plan, position, rows[:position])
        if (_draft_identity(draft) != row['reservation']['draft_sha256']
                or expected != row['reservation']['expected_object']
                or _request_preview(draft)['request_sha256'] != row['reservation']['request_sha256']):
            response.update(status='FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED', changed_stage=draft['operation'])
            return response
    if rows and not _ready(rows[-1]):
        latest = rows[-1]
        reservation = latest['reservation']
        status = ('FIRST_LAUNCH_PLATFORM_DRAFT_AWAITS_SEPARATE_PUBLISH'
                  if latest['readback'] is not None else 'FIRST_LAUNCH_OUTCOME_UNKNOWN')
        expected = reservation['expected_object']
        response.update(status=status, readback_template=dict(
            schema=READBACK, stage_sha256=reservation['stage_sha256'],
            state=None, evidence_ref=None, observed_at=None, object=dict.fromkeys(('id', *expected))),
            expected_object=expected, external_attempt_observed=latest['readback'] is not None,
            next_action='READ_BACK_ORIGINAL_STAGE_ONLY')
        if latest.get('host_attempt') is not None:
            response.update(readback_template=None, next_action='RECOVER_WITH_ORIGINAL_TRUSTED_HOST')
        return response
    if len(rows) == len(specs):
        if specs[-1][0] == 'CREATE_ADSET':
            observed = [
                dict(operation=row['reservation']['operation'], **row['readback']['object'],
                     readback_sha256=digest(row['readback']),
                     basis='TRUSTED_HOST' if row.get('host_attempt') else 'OPERATOR_OBSERVATION')
                for row in rows
            ]
            response.update(status='FIRST_LAUNCH_STRUCTURE_RECORDED', structure=observed,
                            next_action='PREPARE_CREATIVE_FOR_EXISTING_PAUSED_STRUCTURE')
            return response
        ad = rows[-1]['readback']['object']
        chain = _selected_rows(rows, ad['id'], external)
        adset = next((row['readback']['object'] for row in chain if row['reservation']['operation'] == 'CREATE_ADSET'), {})
        context_operation = 'CREATE_ADSET' if adset else 'CREATE_CREATIVE'
        context = next(scoped_draft(root, plan, operation, filename, selection)['context']
                       for operation, filename, _, selection in specs if operation == context_operation)
        chain_sha256 = digest(rows)
        activation_context = dict(currency=context['currency'], timezone=context['timezone'],
                                  declared_budget=plan['business_budget'])
        configured = (plan.get('operator_intake') or {}).get('managed_strategy')
        if configured and not external:
            activation_context['selected_budget'] = dict(
                owner='CAMPAIGN' if configured['budget_owner'] == 'CBO' else 'ADSET',
                schedule=configured['budget_schedule'])
        activation_context = _activation_budget(chain, activation_context)
        if external:
            activation_context['daily_budget_minor'] = None
            activation_context['existing_budget_policy'] = dict(
                owner='CAMPAIGN' if configured['budget_owner'] == 'CBO' else 'ADSET',
                schedule=configured['budget_schedule'])
        response.update(status='FIRST_LAUNCH_OBJECT_CHAIN_RECORDED',
                        ad_id=ad['id'],
                        object_chain_sha256=chain_sha256,
                        activation_context=activation_context,
                        next_action='REVIEW_THIS_AD_PREVIEW_BEFORE_SEPARATE_ACTIVATION_APPROVAL',
                        preview_target=dict(ad_id=ad['id'],
                                            fallback_creative_id=ad['creative_id'],
                                            ad_format=PREVIEW_FORMAT),
                        preview_observation_template=dict(schema=PREVIEW_OBSERVATION,
                                                          object_chain_sha256=chain_sha256, kind='AD',
                                                          object_id=ad['id'], ad_format=PREVIEW_FORMAT,
                                                          observed_at=None, evidence_ref=None),
                        separate_activation_scope=dict(target_ad_id=ad['id'], campaign_id=ad['campaign_id'],
                                                       adset_id=ad['adset_id'], approved=False,
                                                       parent_activation_included=False),
                        activation_attempt=state.get('activation_attempt'),
                        delivery_state='NOT_OBSERVED')
        return response
    draft, expected = _stage(root, plan, len(rows), rows)
    review = review_draft(draft, clock=clock)
    missing = [key for key in review['missing_fields'] if key.startswith('parameters.') or
               key in ('context.currency','context.timezone','context.material_sha256')]
    conflicts = _goal_conflicts(plan, draft['operation'], draft['parameters'])
    if draft['operation'] == 'CREATE_ADSET':
        budget = _budget_review(plan, draft)
        response['budget_review'] = budget
        if budget['conflict']:
            conflicts.append(budget['conflict'])
    response.update(status='FIRST_LAUNCH_NEEDS_INPUT' if missing or conflicts else 'FIRST_LAUNCH_STAGE_FOR_REVIEW',
                    missing_fields=missing, goal_conflicts=conflicts, expected_object=expected, preview=review,
                    creation_request=_request_preview(draft) if not missing and not conflicts else None,
                    next_action='REVIEW_ONE_PAUSED_CREATION_AND_BIND_SEPARATE_APPROVAL')
    if draft['operation'] == 'CREATE_CREATIVE':
        response['destination_tracking'] = _tracking_review(draft['parameters']['destination_url'])
    return response


def _host_request(root, profile, plan, rows, target, configuration_sha=None):
    if not rows or target != rows[-1]['reservation']['stage_sha256']:
        fail('FIRST_LAUNCH_HOST_SELECTED_STAGE_MISMATCH')
    stage = rows[-1]['reservation']
    draft, expected = _stage(root,plan,len(rows)-1,rows[:-1])
    request = _request_preview(draft)
    if (_draft_identity(draft) != stage['draft_sha256']
            or expected != stage['expected_object'] or request['request_sha256'] != stage['request_sha256']):
        fail('FIRST_LAUNCH_HOST_SAVED_SCOPE_CHANGED')
    binding = CreationBinding(
        workspace_sha256=workspace_identity(profile), app_id=profile['app_id'], business_id=profile['business_id'],
        account_id=plan['account_id'], page_id=plan['page_id'], plan_sha256=stage['plan_sha256'], stage_sha256=target,
        draft_sha256=stage['draft_sha256'], request_sha256=request['request_sha256'],
        material_sha256=draft['context']['material_sha256'], dependency_sha256=digest(rows[:-1]),
        operation=draft['operation'], path=request['path'], currency=draft['context']['currency'],
        configuration_sha256=configuration_sha,
    )
    return binding, request['body']


def _accept_readback(latest, record, save, clock):
    # Graph creation reads request stable control fields. Old drafts did not
    # bind them, so retain exactly the evidence frozen in that reservation.
    expected = latest['reservation']['expected_object']
    optional = {'spend_cap_minor', 'start_time', 'end_time', 'url_tags', 'selected_targeting',
                'is_dynamic_creative'} - expected.keys()
    budget_fields = {'daily_budget_minor', 'lifetime_budget_minor'}
    if isinstance(record.get('object'), dict) and (record['object'].keys() & budget_fields) - expected.keys():
        fail('FIRST_LAUNCH_READBACK_BUDGET_LAYER_MISMATCH')
    if isinstance(record.get('object'), dict) and optional & record['object'].keys():
        record = dict(record, object={key: value for key, value in record['object'].items() if key not in optional})
    _validate_record(record,latest['reservation'])
    if aware_time(record['observed_at']) > clock():
        fail('FIRST_LAUNCH_READBACK_FUTURE')
    previous = latest['readback']
    if record == previous:
        return
    if previous is not None and (_ready(latest) or previous['object']['id'] != record['object']['id']
            or aware_time(record['observed_at']) < aware_time(previous['observed_at'])):
        fail('FIRST_LAUNCH_READBACK_ALREADY_RECORDED')
    latest['readback'] = record
    save()


def _recover_host_stage(provider,root,profile,plan,state,target,save,clock):
    rows = state['stages']
    if not rows or rows[-1]['reservation']['stage_sha256'] != target:
        fail('FIRST_LAUNCH_HOST_SELECTED_STAGE_MISMATCH')
    latest = rows[-1]
    attempt = latest.get('host_attempt')
    if attempt is None:
        fail('FIRST_LAUNCH_HOST_ATTEMPT_REQUIRED')
    binding = validate_attempt(attempt)
    workspace_sha = workspace_identity(profile)
    if binding is None:  # Earlier candidates saved the digest only.
        binding, _ = _host_request(root,profile,plan,rows,target)
    if (binding.workspace_sha256 != workspace_sha or binding.account_id != plan['account_id']
            or binding.page_id != plan['page_id'] or binding.app_id != profile['app_id']
            or binding.business_id != profile['business_id'] or binding.plan_sha256 != state['plan_sha256']
            or binding.sha256 != attempt['binding_sha256']):
        fail('FIRST_LAUNCH_HOST_RECOVERY_SCOPE_CHANGED')
    if not _ready(latest):
        record = recover_creation(provider,binding,latest)
        if record is not None:
            _accept_readback(latest,record,save,clock)
    try:
        result = _view(root,plan,state,workspace_sha,clock)
        if _launch_configuration(root,plan,rows,clock)['changed_since_authorization']:
            result = dict(_base_view(plan,state,workspace_sha),status='FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED')
    except (InputError,OSError,UnicodeError):
        result = dict(_base_view(plan,state,workspace_sha),status='FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED')
    return dict(result,network_used=None,transport_mode='TRUSTED_HOST_MANAGED')


def _creation_target_index(workspace_journal, account, operation):
    return Journal(Path(workspace_journal.directory) / 'first-launch-targets' / account / operation,
                   stage_snapshots=True)


def _validate_target_index(row):
    object_only(row, ('pending',), ('pending',))
    pending = row['pending']
    if pending is not None:
        fields = ('plan_sha256', 'stage_sha256')
        object_only(pending, fields, fields)
        if not all(type(pending[key]) is str and HEX.fullmatch(pending[key]) for key in fields):
            fail('FIRST_LAUNCH_TARGET_SCOPE_INVALID')


def _target_pending(index):
    row, damaged = index.load(_validate_target_index)
    if damaged or index.unpublished_snapshot_count():
        fail('FIRST_LAUNCH_TARGET_INDEX_REVIEW_REQUIRED')
    return row['pending'] if row else None


def select_ad(view, state, ad_id):
    if ad_id is None:
        return view
    if view['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    if ad_id == view['ad_id']:
        return view
    external = view.get('named_existing_parents')
    rows = _selected_rows(state['stages'], ad_id, external)
    identity = rows[-1]['readback']['object']
    chain = digest(rows)
    target = dict(view['preview_target'], ad_id=ad_id, fallback_creative_id=identity['creative_id'])
    observation = dict(view['preview_observation_template'], object_id=ad_id, object_chain_sha256=chain)
    scope = dict(view['separate_activation_scope'], target_ad_id=ad_id,
                 campaign_id=identity['campaign_id'], adset_id=identity['adset_id'])
    context = _activation_budget(rows, view['activation_context'])
    if external:
        context['daily_budget_minor'] = None
    return dict(view, stages=rows, ad_id=ad_id, object_chain_sha256=chain, preview_target=target,
                activation_context=context,
                preview_observation_template=observation, separate_activation_scope=scope,
                activation_attempt=state.get('batch_activation_attempts', {}).get(ad_id),
                manual_activation_attempt=None)


def progress(workspace, directory=None, *, reserve=False, readback=None, checkpoint=None, dispatch_stage=None,
             recover_stage=None, provider=None, activation_review=None, activation_mode='plan',
             activation_provider=None, manual_inputs=None, manual_reserve=False, ad_id=None, clock=utcnow):
    if activation_mode not in ('plan', 'dispatch', 'recover') or (activation_mode == 'dispatch' and activation_review is None):
        fail('FIRST_LAUNCH_ACTIVATION_ACTION_INVALID')
    if manual_reserve and manual_inputs is None:
        fail('FIRST_LAUNCH_MANUAL_PREACTION_RESERVATION_REQUIRED')
    activation_action = activation_review is not None or activation_mode == 'recover'
    if (sum(value is not None for value in (readback,checkpoint,dispatch_stage,recover_stage,manual_inputs))
            + bool(reserve) + activation_action > 1):
        fail('FIRST_LAUNCH_PROGRESS_ACTION_CONFLICT')
    host_action = dispatch_stage is not None or recover_stage is not None
    if ad_id is not None and (reserve or readback is not None or host_action or manual_inputs is not None
                              or checkpoint is not None):
        fail('FIRST_LAUNCH_BATCH_SELECTION_REQUIRES_RECORDED_AD')
    if host_action and provider is None:
        fail('FIRST_LAUNCH_TRUSTED_HOST_NOT_CONFIGURED')
    if activation_mode != 'plan' and activation_provider is None:
        fail('FIRST_LAUNCH_ACTIVATION_TRUSTED_HOST_NOT_CONFIGURED')
    root = checked_path(plan_directory(workspace, directory), link_error='FIRST_LAUNCH_PLAN_PATH_LINK',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    input_path = _read_plan_json(readback) if readback is not None else None
    workspace_journal = journal_for(workspace, existing_only=True)
    with workspace_journal.locked(existing_only=True):
        profile = load(workspace_journal)
        plan, plan_sha = current_plan(profile, root)
        workspace_sha = workspace_identity(profile)
        empty = dict(schema=SCHEMA, plan_sha256=plan_sha, stages=[], checkpoint=None)
        if plan.get('local_draft_only'):
            if any(value is not None for value in (readback,checkpoint,manual_inputs,ad_id)) or reserve or host_action or activation_action:
                fail('FIRST_LAUNCH_HOST_CONNECTOR_DRAFT_ONLY')
            return dict(_base_view(plan,empty,workspace_sha), status='FIRST_LAUNCH_HOST_CONNECTOR_DRAFT_ONLY',
                        next_action='USE_ORIGINAL_HOST_CONNECTOR_FOR_APPROVED_READS')
        levels = tuple(spec[0] for spec in stage_specs(plan))
        external = _external_parents(plan)
        if levels[-1] == 'CREATE_ADSET' and (activation_action or manual_inputs is not None):
            fail('FIRST_LAUNCH_STRUCTURE_HAS_NO_AD_TO_ACTIVATE')
        if external and manual_inputs is not None:
            fail('FIRST_LAUNCH_EXISTING_PARENT_MANUAL_ACTIVATION_REQUIRES_SEPARATE_GUARD')
        location = root/'first-launch-progress'
        existing = location.exists() or location.is_symlink()
        if not existing and not reserve:
            if manual_inputs is not None or readback is not None or checkpoint is not None or activation_action or host_action:
                fail('FIRST_LAUNCH_RESERVATION_REQUIRED')
            return select_ad(_view(root, plan, empty, workspace_sha, clock), empty, ad_id)
        journal = Journal(location, existing_only=existing, stage_snapshots=True)
        with journal.locked(wait_seconds=.5, existing_only=existing and not reserve):
            validate = lambda value: _validate_state(value, plan_sha, levels)
            stored, damaged = journal.load(validate)
            if damaged or journal.unpublished_snapshot_count():
                fail('FIRST_LAUNCH_PROGRESS_REVIEW_REQUIRED')
            state = stored or empty
            rows = state['stages']

            def save():
                journal.save(state,validate)

            if recover_stage is not None:
                if not rows or rows[-1]['reservation']['stage_sha256'] != recover_stage:
                    fail('FIRST_LAUNCH_HOST_SELECTED_STAGE_MISMATCH')
                latest = rows[-1]
                operation = latest['reservation']['operation']
                index = _creation_target_index(workspace_journal, plan['account_id'], operation)
                with index.locked():
                    expected = dict(plan_sha256=plan_sha, stage_sha256=recover_stage)
                    pending = _target_pending(index)
                    if pending not in (None, expected):
                        fail('FIRST_LAUNCH_ACCOUNT_TARGET_HAS_UNRESOLVED_ATTEMPT')
                    if latest.get('host_attempt') is None and pending == expected:
                        # An intent indexed before the plan journal committed cannot have sent.
                        index.save(dict(pending=None), _validate_target_index)
                        return dict(_view(root,plan,state,workspace_sha,clock), recovered_target_status='NOT_SENT')
                    result = _recover_host_stage(provider,root,profile,plan,state,recover_stage,save,clock)
                    if _ready(latest) and pending == expected:
                        index.save(dict(pending=None), _validate_target_index)
                    return result

            activation_state, save_activation = state, save
            if ad_id is not None:
                if len(rows) != len(levels) or not _ready(rows[-1]):
                    fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
                _selected_rows(rows, ad_id, external)
            if ad_id is not None and ad_id != rows[-1]['readback']['object']['id']:
                activation_state = {'activation_attempt': state.get('batch_activation_attempts', {}).get(ad_id)}

                def save_activation():
                    state.setdefault('batch_activation_attempts', {})[ad_id] = activation_state['activation_attempt']
                    save()

            if activation_mode == 'dispatch':
                # The manual import and remote host reservation share this journal lock.
                # An A1/A2 default declaration must be reconciled even before its first import.
                legacy_ad = root / 'ad-observation.json'
                if state.get('manual_activation_attempt') is not None or legacy_ad.exists() or legacy_ad.is_symlink():
                    fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')

            if activation_mode == 'recover':
                if state.get('manual_activation_attempt') is not None:
                    fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')
                attempt = activation_state.get('activation_attempt')
                if attempt is None:
                    fail('FIRST_LAUNCH_ACTIVATION_ATTEMPT_REQUIRED')
                frozen = attempt.get('frozen_review')
                if frozen is not None:
                    if activation_review is not None and _read_plan_json(activation_review) != frozen:
                        fail('FIRST_LAUNCH_ACTIVATION_ORIGINAL_REVIEW_REQUIRED')

                    def assert_original_activation():
                        current_profile = load(workspace_journal)
                        current, current_sha = current_plan(current_profile, root)
                        scope = frozen['binding']
                        if (current_sha != scope['plan_sha256'] or workspace_identity(current_profile) != scope['workspace_identity_sha256']
                                or any(current[key] != scope[key] for key in ('account_id', 'page_id', 'app_id', 'business_id'))):
                            fail('FIRST_LAUNCH_ACTIVATION_RECOVERY_SCOPE_CHANGED')

                    assert_original_activation()
                    return activation_operation(frozen, 'recover', activation_state, save_activation, activation_provider,
                                                assert_original_activation, clock)
                if activation_review is None:
                    fail('FIRST_LAUNCH_ACTIVATION_ORIGINAL_REVIEW_REQUIRED')

            view = select_ad(_view(root, plan, state, workspace_sha, clock), state, ad_id)
            if manual_inputs is not None:
                from .first_launch_handoff import record_manual_attempt
                registration = record_manual_attempt(view, state, manual_inputs, root / 'ad-observation.json',
                                                     save, clock, reserve=manual_reserve)
                return dict(_view(root, plan, state, workspace_sha, clock), manual_registration=registration)
            if activation_review is not None:
                from .first_launch_activation import read_review

                activation = read_review(view, activation_review)

                def assert_current_activation():
                    fresh_profile = load(workspace_journal)
                    refreshed, refreshed_sha = current_plan(fresh_profile,root)
                    current = select_ad(_view(root,refreshed,state,workspace_identity(fresh_profile),clock), state, ad_id)
                    if refreshed_sha != plan_sha or read_review(current, activation_review) != activation:
                        fail('FIRST_LAUNCH_ACTIVATION_REVIEW_CHANGED')

                return activation_operation(activation, activation_mode, activation_state, save_activation, activation_provider,
                                            assert_current_activation, clock)
            if reserve and view['status'] == 'FIRST_LAUNCH_STAGE_FOR_REVIEW':
                if len(levels) > len(LEVELS) and 'batch_configuration_sha256' not in state:
                    frozen = _launch_configuration(root, plan, rows, clock)
                    if frozen['issues']:
                        fail('FIRST_LAUNCH_BATCH_COMPLETE_CONFIGURATION_REQUIRED')
                    state['batch_configuration_sha256'] = frozen['configuration_sha256']
                draft = view['preview']['draft']
                item = dict(plan_sha256=plan_sha, operation=draft['operation'], draft_sha256=_draft_identity(draft),
                            request_sha256=view['creation_request']['request_sha256'],
                            expected_object=view['expected_object'], reserved_at=clock().isoformat())
                item['stage_sha256'] = _sealed(item)
                rows.append(dict(reservation=item, readback=None))
                save()
            elif readback is not None:
                if not rows:
                    fail('FIRST_LAUNCH_RESERVATION_REQUIRED')
                latest = rows[-1]
                if latest.get('host_attempt') is not None:
                    fail('FIRST_LAUNCH_TRUSTED_HOST_READBACK_REQUIRED')
                _accept_readback(latest,input_path,save,clock)
            elif checkpoint is not None:
                if not rows or view['status'] == 'FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED':
                    fail('FIRST_LAUNCH_CHECKPOINT_SCOPE_REQUIRED')
                decision = _read_plan_json(checkpoint)
                if decision is None:
                    fail('FIRST_LAUNCH_CHECKPOINT_REQUIRED')
                if decision != state['checkpoint']:
                    state['checkpoint'] = decision
                    save()
            elif dispatch_stage is not None:
                if view['status'] == 'FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED':
                    fail('FIRST_LAUNCH_HOST_SAVED_SCOPE_CHANGED')
                configuration = _launch_configuration(root,plan,rows,clock)
                if configuration['issues']:
                    fail('FIRST_LAUNCH_FULL_CONFIGURATION_REQUIRED')
                if configuration['changed_since_authorization']:
                    fail('FIRST_LAUNCH_HOST_CONFIGURATION_CHANGED')
                configuration_sha = configuration['configuration_sha256']
                binding, body = _host_request(root,profile,plan,rows,dispatch_stage,configuration_sha)

                def assert_current():
                    fresh_profile = load(workspace_journal)
                    refreshed, refreshed_sha = current_plan(fresh_profile,root)
                    current = _view(root,refreshed,state,workspace_identity(fresh_profile),clock)
                    if (refreshed_sha != plan_sha or current['status'] == 'FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED'
                            or _launch_configuration(root,refreshed,rows,clock) != configuration
                            or _host_request(root,fresh_profile,refreshed,rows,dispatch_stage,configuration_sha) != (binding,body)):
                        fail('FIRST_LAUNCH_HOST_SAVED_SCOPE_CHANGED')

                index = _creation_target_index(workspace_journal, plan['account_id'], binding.operation)
                expected = dict(plan_sha256=plan_sha, stage_sha256=dispatch_stage)
                with index.locked():
                    pending = _target_pending(index)
                    if pending not in (None, expected):
                        fail('FIRST_LAUNCH_ACCOUNT_TARGET_HAS_UNRESOLVED_ATTEMPT')

                    def save_target_attempt():
                        if _target_pending(index) is None:
                            index.save(dict(pending=expected), _validate_target_index)
                        save()

                    dispatch_creation(provider,binding,body,rows[-1],save_target_attempt,assert_current,clock)
            result = select_ad(_view(root, plan, state, workspace_sha, clock), state, ad_id)
            if result['status'] == 'FIRST_LAUNCH_OUTCOME_UNKNOWN' and rows[-1].get('host_attempt') is None:
                reserved_sha = rows[-1]['reservation']['stage_sha256']
                configuration = _launch_configuration(root,plan,rows,clock)
                result['configuration_preflight'] = configuration
                if not configuration['issues'] and not configuration['changed_since_authorization']:
                    binding, form = _host_request(root,profile,plan,rows,reserved_sha,configuration['configuration_sha256'])
                    result['host_approval_scope'] = dict(binding=asdict(binding), binding_sha256=binding.sha256,
                                                         form=form, expected_object=rows[-1]['reservation']['expected_object'],
                                                         dispatchable=False)
                    if binding.operation == 'CREATE_CREATIVE':
                        draft, _ = _stage(root, plan, len(rows)-1, rows[:-1])
                        media_scope = draft['parameters'].get('media_scope_sha256')
                        if media_scope is not None:
                            result['host_approval_scope']['media_scope_sha256'] = media_scope
                    if binding.operation == 'CREATE_AD':
                        original_creative = rows[-1]['reservation']['expected_object']['creative_id']
                        feed = next((row['readback']['object'].get('asset_feed_sha256') for row in rows[:-1]
                                     if _ready(row) and row['readback']['object']['id'] == original_creative), None)
                        if feed:
                            result['host_approval_scope']['creative_asset_feed_sha256'] = feed
                    source_campaign = _stage(root, plan, len(rows)-1, rows[:-1])[0]['context'].get('source_campaign_id')
                    if source_campaign is not None:
                        result['host_approval_scope']['source_campaign_id'] = source_campaign
            if host_action:
                result.update(network_used=None, transport_mode='TRUSTED_HOST_MANAGED')
            return result
