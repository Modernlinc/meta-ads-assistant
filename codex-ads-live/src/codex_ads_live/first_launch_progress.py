"""Resume one planned launch with operator notes or trusted host readback.

Local declarations never confer Meta access or approval. Preserve the original
operation while its outcome is unknown and correlate observations by its digest.
"""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .client_workspace import journal_for, load, workspace_identity
from .first_launch_activation_host import activation_operation, validate_activation_attempt
from .first_launch_host import CREATION_READBACK_SCHEMA as READBACK
from .first_launch_host import CreationBinding, dispatch_creation, graph_post_request, recover_creation, validate_attempt
from .first_launch_plan import OPERATIONS, _goal_conflicts, _read_plan_json, current_plan, plan_directory, scoped_draft
from .first_launch_preview import PREVIEW_FORMAT
from .journal import Journal, canonical, digest
from .local_paths import checked_path
from .meta_write_mapping import mapped_request, review_draft, utcnow
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


def _validate_state(value, plan_sha):
    keys = ('schema', 'plan_sha256', 'stages', 'checkpoint')
    object_only(value, (*keys, 'activation_attempt', 'manual_activation_attempt'), keys)
    if value['schema'] != SCHEMA or value['plan_sha256'] != plan_sha:
        fail('FIRST_LAUNCH_PROGRESS_PLAN_CHANGED')
    rows = value['stages']
    if type(rows) is not list or len(rows) > len(LEVELS):
        fail('FIRST_LAUNCH_PROGRESS_SEQUENCE')
    ids = set()
    configurations = set()
    for index, row in enumerate(rows):
        object_only(row, ('reservation', 'readback', 'host_attempt'), ('reservation', 'readback'))
        reservation = row['reservation']
        keys = ('plan_sha256', 'operation', 'draft_sha256', 'request_sha256',
                'expected_object', 'reserved_at', 'stage_sha256')
        object_only(reservation, keys, keys)
        if (reservation['plan_sha256'] != plan_sha or reservation['operation'] != LEVELS[index]
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
        if 'activation_attempt' in value or len(rows) != len(LEVELS) or not _ready(rows[-1]):
            fail('FIRST_LAUNCH_ACTIVATION_SOURCE_CONFLICT')
        from .first_launch_handoff import validate_manual_attempt
        validate_manual_attempt(value['manual_activation_attempt'], rows, plan_sha)
    if 'activation_attempt' in value:
        if len(rows) != len(LEVELS) or not _ready(rows[-1]):
            fail('FIRST_LAUNCH_ACTIVATION_CHAIN_REQUIRED')
        activation = value['activation_attempt']
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


def _stage(root, plan, position, prior):
    operation, filename, dependencies = OPERATIONS[position]
    draft = scoped_draft(root, plan, operation, filename)
    params = draft['parameters']
    ids = {row['reservation']['operation']:row['readback']['object']['id'] for row in prior}
    for key, predecessor in dependencies.items():
        bound = {'creative_id':ids[predecessor]} if key == 'creative' else ids[predecessor]
        if params[key] is not None and params[key] != bound:
            fail('FIRST_LAUNCH_DEPENDENCY_CONFLICT')
        params[key] = bound
    expected = {'account_id':plan['account_id']}
    if operation == 'CREATE_CAMPAIGN':
        expected.update(objective=params['objective'], configured_status='PAUSED')
    elif operation == 'CREATE_ADSET':
        expected.update(campaign_id=params['campaign_id'], daily_budget_minor=params['daily_budget'],
                        optimization_goal=params['optimization_goal'], configured_status='PAUSED')
        if params.get('promoted_object') is not None:
            expected['promoted_object'] = params['promoted_object']
    elif operation == 'CREATE_CREATIVE':
        expected.update(page_id=plan['page_id'], image_hash=params['image_hash'],
                        destination_url=params['destination_url'])
    else:
        expected.update(campaign_id=ids['CREATE_CAMPAIGN'], adset_id=params['adset_id'],
                        creative_id=params['creative']['creative_id'], configured_status='PAUSED')
    return draft, expected


def _draft_identity(draft):
    return digest(dict(operation=draft['operation'], account_id=draft['account_id'], api_version=draft['api_version'],
                       currency=draft['context']['currency'], timezone=draft['context']['timezone'],
                       page_id=draft['context']['page_id'], material_sha256=draft['context']['material_sha256'],
                       parameters=draft['parameters']))


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
    minor = draft['parameters']['daily_budget']
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
    ids = {row['reservation']['operation']:row['readback']['object']['id'] for row in rows if _ready(row)}
    for operation, filename, dependencies in OPERATIONS:
        draft = scoped_draft(root,plan,operation,filename)
        report = review_draft(draft,clock=lambda:checked_at)
        missing = [key for key in report['missing_fields'] if key.startswith('parameters.')
                   and key.removeprefix('parameters.') not in dependencies]
        if operation != 'CREATE_AD':
            missing += [key for key in report['missing_fields'] if
                        key in ('context.currency','context.timezone','context.material_sha256')]
        conflicts = _goal_conflicts(plan,operation,draft['parameters'])
        for field, predecessor in dependencies.items():
            known = ids.get(predecessor)
            expected = {'creative_id':known} if known is not None and field=='creative' else known
            if draft['parameters'][field] not in (None,expected):
                conflicts.append('FIRST_LAUNCH_DEPENDENCY_CONFLICT')
        if operation == 'CREATE_ADSET':
            budget = _budget_review(plan,draft)
            if budget['conflict']:
                conflicts.append(budget['conflict'])
        if missing or conflicts:
            issues[operation] = dict(missing_fields=missing,conflicts=conflicts)
        context = draft['context']
        configuration.append(dict(operation=operation, currency=context['currency'] if operation!='CREATE_AD' else None,
                                  timezone=context['timezone'] if operation!='CREATE_AD' else None,
                                  source_material_sha256=context['material_sha256'] if operation=='CREATE_CREATIVE' else None,
                                  parameters={key:value for key,value in draft['parameters'].items() if key not in dependencies},
                                  depends_on=dependencies))
    identity = digest(dict(account_id=plan['account_id'],page_id=plan['page_id'],configuration=configuration))
    saved = next((row['host_attempt']['binding']['configuration_sha256'] for row in rows
                  if (row.get('host_attempt') or {}).get('binding') is not None),None)
    return dict(configuration_sha256=identity,issues=issues,changed_since_authorization=saved not in (None,identity))


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
    if len(rows) == len(LEVELS):
        ad = rows[-1]['readback']['object']
        adset_draft = scoped_draft(root,plan,'CREATE_ADSET','adset-draft.json')
        context = adset_draft['context']
        chain_sha256 = digest(rows)
        response.update(status='FIRST_LAUNCH_OBJECT_CHAIN_RECORDED',
                        ad_id=ad['id'],
                        object_chain_sha256=chain_sha256,
                        activation_context=dict(currency=context['currency'], timezone=context['timezone'],
                                                declared_budget=plan['business_budget'],
                                                daily_budget_minor=rows[1]['readback']['object']['daily_budget_minor']),
                        next_action='REVIEW_THIS_AD_PREVIEW_BEFORE_SEPARATE_ACTIVATION_APPROVAL',
                        preview_target=dict(ad_id=ad['id'],
                                            fallback_creative_id=rows[2]['readback']['object']['id'],
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


def progress(workspace, directory=None, *, reserve=False, readback=None, checkpoint=None, dispatch_stage=None,
             recover_stage=None, provider=None, activation_review=None, activation_mode='plan',
             activation_provider=None, manual_inputs=None, manual_reserve=False, clock=utcnow):
    if activation_mode not in ('plan', 'dispatch', 'recover') or (activation_mode == 'dispatch' and activation_review is None):
        fail('FIRST_LAUNCH_ACTIVATION_ACTION_INVALID')
    if manual_reserve and manual_inputs is None:
        fail('FIRST_LAUNCH_MANUAL_PREACTION_RESERVATION_REQUIRED')
    activation_action = activation_review is not None or activation_mode == 'recover'
    if (sum(value is not None for value in (readback,checkpoint,dispatch_stage,recover_stage,manual_inputs))
            + bool(reserve) + activation_action > 1):
        fail('FIRST_LAUNCH_PROGRESS_ACTION_CONFLICT')
    host_action = dispatch_stage is not None or recover_stage is not None
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
        location = root/'first-launch-progress'
        existing = location.exists() or location.is_symlink()
        if not existing and not reserve:
            if manual_inputs is not None or readback is not None or checkpoint is not None or activation_action or host_action:
                fail('FIRST_LAUNCH_RESERVATION_REQUIRED')
            return _view(root, plan, empty, workspace_sha, clock)
        journal = Journal(location, existing_only=existing, stage_snapshots=True)
        with journal.locked(wait_seconds=.5, existing_only=existing and not reserve):
            validate = lambda value: _validate_state(value, plan_sha)
            stored, damaged = journal.load(validate)
            if damaged or journal.unpublished_snapshot_count():
                fail('FIRST_LAUNCH_PROGRESS_REVIEW_REQUIRED')
            state = stored or empty
            rows = state['stages']

            def save():
                journal.save(state,validate)

            if recover_stage is not None:
                return _recover_host_stage(provider,root,profile,plan,state,recover_stage,save,clock)

            if activation_mode == 'dispatch':
                # The manual import and remote host reservation share this journal lock.
                # An A1/A2 default declaration must be reconciled even before its first import.
                legacy_ad = root / 'ad-observation.json'
                if state.get('manual_activation_attempt') is not None or legacy_ad.exists() or legacy_ad.is_symlink():
                    fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')

            if activation_mode == 'recover':
                if state.get('manual_activation_attempt') is not None:
                    fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')
                attempt = state.get('activation_attempt')
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
                    return activation_operation(frozen, 'recover', state, save, activation_provider,
                                                assert_original_activation, clock)
                if activation_review is None:
                    fail('FIRST_LAUNCH_ACTIVATION_ORIGINAL_REVIEW_REQUIRED')

            view = _view(root, plan, state, workspace_sha, clock)
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
                    current = _view(root,refreshed,state,workspace_identity(fresh_profile),clock)
                    if refreshed_sha != plan_sha or read_review(current, activation_review) != activation:
                        fail('FIRST_LAUNCH_ACTIVATION_REVIEW_CHANGED')

                return activation_operation(activation, activation_mode, state, save, activation_provider,
                                            assert_current_activation, clock)
            if reserve and view['status'] == 'FIRST_LAUNCH_STAGE_FOR_REVIEW':
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

                dispatch_creation(provider,binding,body,rows[-1],save,assert_current,clock)
            result = _view(root, plan, state, workspace_sha, clock)
            if result['status'] == 'FIRST_LAUNCH_OUTCOME_UNKNOWN' and rows[-1].get('host_attempt') is None:
                reserved_sha = rows[-1]['reservation']['stage_sha256']
                configuration = _launch_configuration(root,plan,rows,clock)
                result['configuration_preflight'] = configuration
                if not configuration['issues'] and not configuration['changed_since_authorization']:
                    binding, form = _host_request(root,profile,plan,rows,reserved_sha,configuration['configuration_sha256'])
                    result['host_approval_scope'] = dict(binding=asdict(binding), binding_sha256=binding.sha256,
                                                         form=form, expected_object=rows[-1]['reservation']['expected_object'],
                                                         dispatchable=False)
            if host_action:
                result.update(network_used=None, transport_mode='TRUSTED_HOST_MANAGED')
            return result
