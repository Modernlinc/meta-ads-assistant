"""Project-local handoff for a customer's independently approved Meta operations."""

import re

from .first_launch_activation import read_review
from .first_launch_plan import _read_plan_json, plan_directory
from .first_launch_progress import progress
from .journal import digest
from .meta_write_mapping import _write_new, load_draft, utcnow
from .monitor_tools import new_directory
from .validation import aware_time, fail, object_only


def _reference(value):
    if type(value) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', value):
        fail('FIRST_LAUNCH_HANDOFF_EVIDENCE_REQUIRED')


def _observation(path, expected, *, clock, earliest=None):
    row = path if isinstance(path, dict) else _read_plan_json(path)
    object_only(row, expected, expected)
    for key, value in expected.items():
        if value is not None and (type(row[key]) is not type(value) or row[key] != value):
            fail('FIRST_LAUNCH_HANDOFF_OBSERVATION_SCOPE_MISMATCH')
    _reference(row['evidence_ref'])
    timestamp = aware_time(row['observed_at'])
    if timestamp > clock or (earliest is not None and timestamp < earliest):
        fail('FIRST_LAUNCH_HANDOFF_OBSERVATION_TIME_INVALID')
    return row, timestamp


def _local_receipt(explicit, default):
    if explicit is not None:
        return explicit
    if default.exists() or default.is_symlink():
        return default
    return None


def activation_paths(workspace, directory, *, review=None, before=None, after=None, ad=None):
    root = plan_directory(workspace, directory)
    return (
        _local_receipt(review, root / 'activation-review' / 'activation-review.json'),
        _local_receipt(before, root / 'before-parents.json'),
        _local_receipt(after, root / 'after-parents.json'),
        _local_receipt(ad, root / 'ad-observation.json'),
    )


def _image_scope(workspace, directory, launch, path, clock):
    draft = load_draft(plan_directory(workspace, directory) / 'creative-draft.json')
    if (draft['operation'], draft['account_id'], draft['context']['page_id']) != (
            'CREATE_CREATIVE', launch['account_id'], launch['page_id']):
        fail('FIRST_LAUNCH_DRAFT_SCOPE_CHANGED')
    material = draft['context']['material_sha256']
    image = draft['parameters']['image_hash']
    template = dict(schema='first-launch-image-observation-v1', plan_sha256=launch['plan_sha256'],
                    account_id=launch['account_id'], page_id=launch['page_id'],
                    material_sha256=material, image_hash=image, evidence_ref=None, observed_at=None)
    if not material or not image:
        if path is not None:
            fail('FIRST_LAUNCH_IMAGE_DRAFT_REFERENCE_REQUIRED')
        return dict(image_state='NEEDS_SAME_ACCOUNT_IMAGE_REFERENCE', image_observation_template=template)
    if path is None:
        return dict(image_state='SAME_ACCOUNT_IMAGE_OBSERVATION_REQUIRED', image_observation_template=template)
    row, _ = _observation(path, template, clock=clock)
    return dict(image_state='OPERATOR_DECLARED_IMAGE_MATCH', image_observation=row)


def _parent_handoff(launch, review, before_path, after_path, ad_path, clock):
    binding = review['binding']
    start, end = (aware_time(binding[key]) for key in ('starts_at', 'ends_at'))
    ad_id = binding['ad_id']
    scope = {key: binding[key] for key in (
        'account_id', 'campaign_id', 'adset_id', 'ad_id', 'currency',
        'daily_budget_minor', 'maximum_spend_minor', 'starts_at', 'ends_at')}
    policy = binding.get('selected_budget')
    if policy is not None:
        scope['selected_budget'] = policy
    scope['review_sha256'] = review['review_sha256']
    parent_template = dict(schema='first-launch-parent-before-v1', **scope, observed_at=None,
                           evidence_ref=None, campaign_configured_status=None, adset_configured_status=None,
                           ad_configured_status='PAUSED', affected_ad_ids=[ad_id], campaign_inventory_complete=True)
    context = dict(activation_scope=scope, review_blockers=review['review_blockers'],
                   parent_before_template=parent_template)
    if review['review_blockers']:
        return dict(context, next_action='RESOLVE_AD_PREVIEW_OR_BUDGET_REVIEW')
    if launch.get('activation_attempt') is not None:
        return dict(context, next_action='RECOVER_OR_REVIEW_ORIGINAL_HOST_ATTEMPT')
    if before_path is None:
        return dict(context, next_action='CHECK_NAMED_CAMPAIGN_ADSET_AD_AND_AFFECTED_ADS')
    before, before_at = _observation(
        before_path, parent_template, clock=clock,
        earliest=aware_time(review['preview_observation']['observed_at']))
    parent_changes = {}
    for level in ('campaign', 'adset'):
        key = f'{level}_configured_status'
        if before[key] not in ('PAUSED', 'ACTIVE'):
            fail('FIRST_LAUNCH_HANDOFF_PARENT_STATUS_UNRESOLVED')
        if before[key] == 'PAUSED':
            parent_changes[level] = dict(id=binding[f'{level}_id'], from_status='PAUSED', to_status='ACTIVE')
    parent_scope = dict(scope, before_sha256=digest(before), changes=parent_changes, affected_ad_ids=[ad_id])
    after_template = dict(parent_template, schema='first-launch-parent-after-v1',
                          campaign_configured_status='ACTIVE', adset_configured_status='ACTIVE',
                          parent_scope_sha256=digest(parent_scope),
                          enforceable_stop_at=None, enforceable_spend_cap_minor=None,
                          control_evidence_ref=None)
    if parent_changes:
        after_template['parent_approval_ref'] = None
    context.update(parent_approval_scope=parent_scope, parent_before=before, parent_after_template=after_template)
    if after_path is None:
        if clock >= end:
            return dict(context, next_action='REVIEW_WINDOW_ELAPSED_KEEP_AD_PAUSED')
        action = ('OBTAIN_SEPARATE_PARENT_APPROVAL_AND_RECHECK' if parent_changes
                  else 'RECORD_EXISTING_ACTIVE_PARENTS_AND_CONTROLS')
        return dict(context, next_action=action)
    after, after_at = _observation(after_path, after_template, clock=clock, earliest=before_at)
    if parent_changes:
        _reference(after['parent_approval_ref'])
    _reference(after['control_evidence_ref'])
    cap = after['enforceable_spend_cap_minor']
    stop = aware_time(after['enforceable_stop_at'])
    planned_minor = policy['budget_minor'] if policy is not None else binding['daily_budget_minor']
    if type(cap) is not int or not planned_minor <= cap <= binding['maximum_spend_minor']:
        fail('FIRST_LAUNCH_HANDOFF_SPEND_CONTROL_MISMATCH')
    if not max(after_at, start) < stop <= end:
        fail('FIRST_LAUNCH_HANDOFF_STOP_CONTROL_MISMATCH')
    ad_template = dict(schema='first-launch-ad-observation-v1', **scope, evidence_ref=None, observed_at=None,
                       parent_after_sha256=digest(after), ad_approval_ref=None, attempt_at=None,
                       configured_status=None, effective_status=None)
    context.update(parent_after=after, ad_observation_template=ad_template)
    if ad_path is None:
        if clock >= stop:
            return dict(context, next_action='REVIEW_ORIGINAL_AD_WINDOW_ELAPSED')
        if clock < start:
            return dict(context, next_action='WAIT_FOR_REVIEWED_START_AND_RECHECK')
        return dict(context, next_action='OBTAIN_SEPARATE_SINGLE_AD_APPROVAL_AND_RECHECK_WITHIN_WINDOW')
    ad, observed_at = _observation(ad_path, ad_template, clock=clock, earliest=after_at)
    _reference(ad['ad_approval_ref'])
    if parent_changes and ad['ad_approval_ref'] == after['parent_approval_ref']:
        fail('FIRST_LAUNCH_HANDOFF_SEPARATE_AD_APPROVAL_REQUIRED')
    attempt_at = aware_time(ad['attempt_at'])
    if not max(start, after_at) <= attempt_at < stop or observed_at < attempt_at:
        fail('FIRST_LAUNCH_HANDOFF_AD_ATTEMPT_TIME_INVALID')
    if ad['configured_status'] not in ('ACTIVE', 'PAUSED', 'UNKNOWN') or (
            type(ad['effective_status']) is not str or not re.fullmatch(r'[A-Z_]{2,40}', ad['effective_status'])):
        fail('FIRST_LAUNCH_HANDOFF_AD_STATUS_REQUIRED')
    context.update(ad_observation=ad, ad_state_evidence='OPERATOR_DECLARED',
                   observed_inside_window=observed_at < stop)
    action = ('REVIEW_SAME_AD_INSIGHTS' if ad['configured_status'] == 'ACTIVE'
              else 'RECONCILE_ORIGINAL_AD_ATTEMPT_WITHOUT_RESENDING')
    return dict(context, next_action=action)


def validate_manual_attempt(attempt, rows, plan_sha):
    fields = ('source', 'attempt_id', 'review', 'before', 'after', 'first_observation', 'observation')
    object_only(attempt, fields, fields)
    review = attempt['review']
    required_review = ('schema', 'status', 'binding', 'review_sha256', 'preview_observation',
                       'preview_evidence', 'preview_kind', 'review_blockers', 'parent_delivery_state',
                       'independent_activation_approval_verified', 'spend_authorized', 'dispatchable', 'network_used')
    object_only(review, required_review, required_review)
    binding = review['binding']
    obj = rows[-1]['readback']['object']
    first = attempt['first_observation']
    if (type(binding) is not dict or type(review['preview_observation']) is not dict
            or type(first) is not dict or type(attempt['observation']) is not dict
            or review['schema'] != 'first-launch-activation-review-v1'
            or review['status'] != 'FIRST_LAUNCH_ACTIVATION_REVIEW_PREPARED'
            or any(review[key] is not False for key in (
                'independent_activation_approval_verified', 'spend_authorized', 'dispatchable', 'network_used'))
            or binding.get('preview_observation_sha256') != digest(review['preview_observation'])
            or attempt['source'] != 'MANUAL' or review['review_sha256'] != digest(binding)
            or attempt['review']['review_blockers'] or attempt['review']['preview_kind'] != 'AD'
            or binding.get('plan_sha256') != plan_sha or binding.get('object_chain_sha256') != digest(rows)
            or any(binding.get(k) != obj[v] for k, v in (('ad_id', 'id'), ('account_id', 'account_id'),
                ('adset_id', 'adset_id'), ('campaign_id', 'campaign_id'), ('creative_id', 'creative_id')))
            or attempt['attempt_id'] != _manual_id(attempt['review'], attempt['before'], attempt['after'], first)):
        fail('FIRST_LAUNCH_MANUAL_ATTEMPT_BINDING_MISMATCH')
    # Validate both the first declaration and latest observation against the frozen scope.
    for entry in (first, attempt['observation']):
        if (entry.get('attempt_at'), entry.get('ad_approval_ref')) != (first.get('attempt_at'), first.get('ad_approval_ref')):
            fail('FIRST_LAUNCH_MANUAL_ATTEMPT_BINDING_MISMATCH')
        if aware_time(entry.get('observed_at')) < aware_time(first.get('observed_at')):
            fail('FIRST_LAUNCH_MANUAL_OBSERVATION_REGRESSION')
        _parent_handoff({}, attempt['review'], attempt['before'], attempt['after'], entry, utcnow())


def _manual_id(review, before, after, observation):
    return digest(['MANUAL', review['review_sha256'], digest(before), digest(after),
                   observation.get('ad_approval_ref'), observation.get('attempt_at')])


def record_manual_attempt(view, state, paths, default_ad, save, clock, *, reserve):
    if state.get('activation_attempt') is not None:
        fail('FIRST_LAUNCH_REPORT_ACTIVATION_SOURCE_CONFLICT')
    previous = state.get('manual_activation_attempt')
    review_path, before_path, after_path, ad_path = paths
    if previous is None:
        if view['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
            fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
        if any(path is None for path in paths):
            fail('FIRST_LAUNCH_HANDOFF_PRIOR_OBSERVATION_REQUIRED')
        review = read_review(view, review_path)
    else:
        review = previous['review']
        if review_path is not None and _read_plan_json(review_path) != review:
            fail('FIRST_LAUNCH_MANUAL_ORIGINAL_ATTEMPT_REQUIRED')
        binding = review['binding']
        if any(view[key] != binding[key] for key in (
                'workspace_identity_sha256', 'plan_sha256', 'app_id', 'business_id', 'account_id', 'page_id')):
            fail('FIRST_LAUNCH_MANUAL_ORIGINAL_ATTEMPT_REQUIRED')
    before = _read_plan_json(before_path) if before_path is not None else previous['before']
    after = _read_plan_json(after_path) if after_path is not None else previous['after']
    ad = _read_plan_json(ad_path) if ad_path is not None else previous['observation']
    now = clock()
    checked = _parent_handoff(view, review, before, after, ad, now)
    if checked.get('ad_observation') is None:
        fail('FIRST_LAUNCH_HANDOFF_MANUAL_ACTIVATION_UNRESOLVED')
    identifier = _manual_id(review, before, after, ad)
    first = previous['first_observation'] if previous else ad
    latest = previous['observation'] if previous else None
    if ad_path is not None and ad_path != default_ad and (default_ad.exists() or default_ad.is_symlink()):
        # An explicit update cannot conceal an earlier declaration in the original plan.
        legacy = _read_plan_json(default_ad)
        _parent_handoff(view, review, before, after, legacy, now)
        if _manual_id(review, before, after, legacy) != identifier:
            fail('FIRST_LAUNCH_MANUAL_ORIGINAL_ATTEMPT_REQUIRED')
        if previous is None:
            if reserve:
                fail('FIRST_LAUNCH_MANUAL_RECONCILE_EXISTING_DECLARATION')
            first, latest = legacy, legacy
    if previous is not None:
        if identifier != previous['attempt_id']:
            fail('FIRST_LAUNCH_MANUAL_ORIGINAL_ATTEMPT_REQUIRED')
        if ad == latest or ad == first:
            return 'ORIGINAL_ALREADY_RECORDED'
        if reserve:
            fail('FIRST_LAUNCH_MANUAL_ORIGINAL_ATTEMPT_REQUIRED')
    if latest is not None and ad != latest and (
            aware_time(ad['observed_at']) <= aware_time(latest['observed_at'])
            or (latest['configured_status'] != 'UNKNOWN' and ad['configured_status'] == 'UNKNOWN')):
        fail('FIRST_LAUNCH_MANUAL_OBSERVATION_REGRESSION')
    if reserve and (ad['configured_status'] != 'UNKNOWN' or ad['effective_status'] != 'UNKNOWN'
                    or now >= aware_time(after['enforceable_stop_at'])):
        fail('FIRST_LAUNCH_MANUAL_PREACTION_RESERVATION_REQUIRED')
    state['manual_activation_attempt'] = dict(
        source='MANUAL', attempt_id=identifier, review=review, before=before, after=after,
        first_observation=first, observation=ad)
    save()
    return 'FIRST_PREACTION_RESERVATION' if reserve else 'ORIGINAL_DECLARATION_RECORDED'


def resume_manual(workspace, directory, paths, *, reserve=False):
    if reserve and paths[-1] is None:
        fail('FIRST_LAUNCH_MANUAL_PREACTION_RESERVATION_REQUIRED')
    if paths[-1] is not None:
        return progress(workspace, directory, manual_inputs=paths, manual_reserve=reserve)
    launch = progress(workspace, directory)
    if launch.get('manual_activation_attempt') and any(path is not None for path in paths):
        return progress(workspace, directory, manual_inputs=paths)
    return launch


def manual_activation_observation(launch, review, before, after, ad):
    if not all(path is not None for path in (before, after, ad)):
        fail('FIRST_LAUNCH_HANDOFF_PRIOR_OBSERVATION_REQUIRED')
    result = _parent_handoff(launch, review, before, after, ad, utcnow())
    if result.get('ad_observation') is None:
        fail('FIRST_LAUNCH_HANDOFF_MANUAL_ACTIVATION_UNRESOLVED')
    return result['parent_after'], result['ad_observation']


def handoff(workspace, directory=None, *, image_path=None, review_path=None, before_path=None,
            after_path=None, ad_path=None, output=None, reserve_manual=False):
    plan_root = plan_directory(workspace, directory)
    image_path = _local_receipt(image_path, plan_root / 'image-observation.json')
    review_path, before_path, after_path, ad_path = activation_paths(
        workspace, directory, review=review_path, before=before_path, after=after_path, ad=ad_path)
    launch = resume_manual(workspace, directory, (review_path, before_path, after_path, ad_path),
                           reserve=reserve_manual)
    saved = launch.get('manual_activation_attempt')
    if not saved and ((before_path is not None and review_path is None)
            or (after_path is not None and before_path is None)
            or (ad_path is not None and after_path is None)):
        fail('FIRST_LAUNCH_HANDOFF_PRIOR_OBSERVATION_REQUIRED')
    clock = utcnow()
    result = dict(schema='first-launch-customer-handoff-v1', operating_channel='CUSTOMER_META_ADS_MANAGER',
                  automatic_write_provider='LOCAL_AUTHORITY_STATUS_NOT_QUERIED',
                  progress_status=launch['status'], plan_sha256=launch['plan_sha256'],
                  workspace_identity_sha256=launch['workspace_identity_sha256'],
                  app_id=launch['app_id'], account_id=launch['account_id'], page_id=launch['page_id'],
                  next_action=launch.get('next_action', 'RESTORE_OR_REVIEW_ORIGINAL_PLAN'),
                  approved=False, dispatchable=False, network_used=False, delivery_state='NOT_OBSERVED')
    if saved is not None:
        result.update(manual_attempt_id=saved['attempt_id'],
                      manual_attempt_source='OPERATOR_DECLARED_PENDING_EXTERNAL_VERIFICATION',
                      manual_registration=launch.get('manual_registration', 'ORIGINAL_ALREADY_RECORDED'))
    if launch['status'] == 'FIRST_LAUNCH_SAVED_SCOPE_REVIEW_REQUIRED':
        if saved is not None:
            result.update(next_action='RECONCILE_ORIGINAL_AD_ATTEMPT_WITHOUT_RESENDING',
                          ad_observation=saved['observation'])
        return result
    result.update(_image_scope(workspace, directory, launch, image_path, clock))
    if launch['status'] == 'FIRST_LAUNCH_STAGE_FOR_REVIEW':
        result.update(operation=launch['preview']['draft']['operation'],
                      expected_object=launch['expected_object'])
        if result['image_state'] != 'OPERATOR_DECLARED_IMAGE_MATCH':
            result['next_action'] = 'PREPARE_AND_BIND_IMAGE_FOR_THIS_LAUNCH'
    if launch.get('readback_template') is not None:
        result['readback_template'] = launch['readback_template']
    if launch.get('preview_observation_template') is not None:
        result['preview_observation_template'] = launch['preview_observation_template']
        result['ad_id'] = launch['ad_id']
    if saved is not None:
        result.update(_parent_handoff(launch, saved['review'], saved['before'], saved['after'],
                                      saved['observation'], clock))
        if result['manual_registration'] == 'FIRST_PREACTION_RESERVATION':
            result['next_action'] = 'PERFORM_ONLY_ORIGINAL_EXTERNALLY_APPROVED_MANUAL_ACTION'
    elif review_path is not None:
        review = read_review(launch, review_path)
        result.update(_parent_handoff(launch, review, before_path, after_path, ad_path, clock))
    if output is not None:
        _write_new(new_directory(output), 'first-launch-handoff.json', result)
    return result
