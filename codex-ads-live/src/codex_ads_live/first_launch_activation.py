"""Bind a declared preview to a nonexecutable, single-Ad activation review."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfoNotFoundError

from .first_launch_activation_host import REVIEW_SCHEMA
from .first_launch_plan import _read_plan_json
from .first_launch_progress import PREVIEW_OBSERVATION, progress
from .journal import canonical, digest
from .meta_write_mapping import _minor, _write_new, utcnow
from .monitor_tools import new_directory
from .timezones import load_zone
from .validation import aware_time, fail, object_only


def _utc(value):
    try:
        return aware_time(value).astimezone(timezone.utc)
    except OverflowError:
        fail('FIRST_LAUNCH_ACTIVATION_TIME_OUT_OF_RANGE')


def _review(launch, preview, start_at, end_at, maximum_spend_minor):
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    keys = ('schema', 'object_chain_sha256', 'kind', 'object_id', 'ad_format', 'observed_at', 'evidence_ref')
    object_only(preview, keys, keys)
    target = launch['preview_target']
    kind = preview['kind']
    if (preview['schema'] != PREVIEW_OBSERVATION
            or preview['object_chain_sha256'] != launch['object_chain_sha256']
            or preview['ad_format'] != target['ad_format'] or kind not in ('AD', 'CREATIVE')):
        fail('FIRST_LAUNCH_PREVIEW_SCOPE_MISMATCH')
    is_ad = kind == 'AD'
    target_id = target['ad_id'] if is_ad else target['fallback_creative_id']
    if preview['object_id'] != target_id:
        fail('FIRST_LAUNCH_PREVIEW_OBJECT_MISMATCH')
    reference = preview['evidence_ref']
    if type(reference) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', reference):
        fail('FIRST_LAUNCH_PREVIEW_REFERENCE_REQUIRED')
    observed = _utc(preview['observed_at'])
    target_stage = launch['stages'][-1 if is_ad else -2]
    if not _utc(target_stage['reservation']['reserved_at']) <= observed <= utcnow():
        fail('FIRST_LAUNCH_PREVIEW_OBSERVATION_TIME_INVALID')
    start, end = _utc(start_at), _utc(end_at)
    if start >= end:
        fail('FIRST_LAUNCH_ACTIVATION_WINDOW_INVALID')
    if maximum_spend_minor is None:
        fail('FIRST_LAUNCH_ACTIVATION_SPEND_LIMIT_REQUIRED')
    _minor(maximum_spend_minor)
    context = launch['activation_context']
    if context['currency'] is None or context['daily_budget_minor'] is None:
        fail('FIRST_LAUNCH_ACTIVATION_BUDGET_CONTEXT_REQUIRED')
    _account_zone(context['timezone'])
    scope = launch['separate_activation_scope']
    binding = dict(workspace_identity_sha256=launch['workspace_identity_sha256'],
                   app_id=launch['app_id'], business_id=launch['business_id'],
                   plan_sha256=launch['plan_sha256'], object_chain_sha256=launch['object_chain_sha256'],
                   account_id=launch['account_id'], page_id=launch['page_id'], ad_id=target['ad_id'],
                   creative_id=target['fallback_creative_id'], campaign_id=scope['campaign_id'],
                   adset_id=scope['adset_id'], parent_activation_included=False,
                   currency=context['currency'], timezone=context['timezone'],
                   daily_budget_minor=context['daily_budget_minor'], maximum_spend_minor=maximum_spend_minor,
                   starts_at=start.isoformat(), ends_at=end.isoformat(),
                   preview_observation_sha256=digest(preview))
    blockers = []
    if not is_ad:
        blockers.append('AD_PREVIEW_EVIDENCE_REQUIRED')
    if observed > start:
        blockers.append('PREVIEW_OBSERVED_AFTER_REQUESTED_START')
    if context['daily_budget_minor'] > maximum_spend_minor:
        blockers.append('DAILY_BUDGET_EXCEEDS_PROPOSED_TOTAL')
    budget = context['declared_budget']
    if budget['currency'] not in (None, context['currency']):
        blockers.append('DECLARED_BUDGET_CURRENCY_MISMATCH')
    elif budget['maximum_test_spend'] is not None:
        proposed = Decimal(maximum_spend_minor).scaleb(0 if context['currency'] == 'JPY' else -2)
        if proposed > Decimal(budget['maximum_test_spend']):
            blockers.append('PROPOSED_TOTAL_EXCEEDS_DECLARED_TEST_CAP')
    return dict(schema=REVIEW_SCHEMA, status='FIRST_LAUNCH_ACTIVATION_REVIEW_PREPARED',
                binding=binding, review_sha256=digest(binding), preview_observation=preview,
                preview_evidence='OPERATOR_DECLARED', preview_kind='AD' if is_ad else 'CREATIVE_FALLBACK',
                review_blockers=blockers, parent_delivery_state='REQUIRES_INDEPENDENT_REVIEW',
                independent_activation_approval_verified=False, spend_authorized=False,
                dispatchable=False, network_used=False)


def prepare(workspace, directory, observation_path, start_at, end_at, maximum_spend_minor, output=None):
    review = _review(progress(workspace, directory), _read_plan_json(observation_path),
                     start_at, end_at, maximum_spend_minor)
    if output is not None:
        _write_new(new_directory(output), 'activation-review.json', review)
    return review


def read_review(launch, path):
    packet = _read_plan_json(path)
    if type(packet) is not dict or packet.get('schema') != REVIEW_SCHEMA or type(packet.get('binding')) is not dict:
        fail('FIRST_LAUNCH_ACTIVATION_REVIEW_REQUIRED')
    binding = packet['binding']
    current = _review(launch, packet.get('preview_observation'), binding.get('starts_at'),
                      binding.get('ends_at'), binding.get('maximum_spend_minor'))
    if canonical(current) != canonical(packet):
        fail('FIRST_LAUNCH_ACTIVATION_REVIEW_MISMATCH')
    return current


def _account_zone(key):
    try:
        return load_zone(key)
    except ZoneInfoNotFoundError:
        fail('FIRST_LAUNCH_ACTIVATION_ACCOUNT_TIMEZONE_REQUIRED')


def account_days_against_window(binding, context):
    if any(context[key] != binding[key] for key in ('currency', 'timezone')):
        fail('FIRST_LAUNCH_REPORT_ACTIVATION_CONTEXT_MISMATCH')
    zone = _account_zone(context['timezone'])
    try:
        first_day = date.fromisoformat(context['date_start'])
        next_day = date.fromisoformat(context['date_stop']) + timedelta(days=1)
        first = datetime.combine(first_day, time.min, zone).astimezone(timezone.utc)
        stop = datetime.combine(next_day, time.min, zone).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        fail('FIRST_LAUNCH_REPORT_DAYS_OUT_OF_RANGE')
    start, end = _utc(binding['starts_at']), _utc(binding['ends_at'])
    if first >= stop:
        fail('FIRST_LAUNCH_REPORT_DAYS_OUT_OF_RANGE')
    if stop <= start or first >= end:
        relation = 'OUTSIDE_WINDOW'
    elif start <= first and stop <= end:
        relation = 'WHOLLY_WITHIN_WINDOW'
    else:
        relation = 'PARTIAL_OVERLAP_WITH_WINDOW'
    return dict(relation=relation, starts_at=first.isoformat(), ends_at=stop.isoformat())


def daily_window(review, context):
    result = account_days_against_window(review['binding'], context)
    result['relation'] = result['relation'].replace('_WINDOW', '_PROPOSED_WINDOW')
    return result
