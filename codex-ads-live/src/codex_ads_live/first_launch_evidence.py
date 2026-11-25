"""Narrow first-launch creative and account activity reads for a named recorded Ad."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext
import re
from urllib.parse import urlencode

from .analysis import aggregate_ads, aggregate_hierarchy
from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
from .first_launch_authority import read_key
from .first_launch_progress import progress
from .journal import digest
from .meta_write_mapping import utcnow
from .validation import aware_time, fail, object_only

ID = re.compile(r'[1-9][0-9]{0,39}')
EVENT = re.compile(r'[A-Za-z0-9_ -]{1,100}')
CURSOR = re.compile(r'[A-Za-z0-9._~+=/-]{1,1024}')


def _window(since, until):
    try:
        start, stop = date.fromisoformat(since), date.fromisoformat(until)
    except ValueError:
        fail('FIRST_LAUNCH_EVIDENCE_WINDOW_INVALID')
    if start.isoformat() != since or stop.isoformat() != until or not 0 <= (stop-start).days <= 30:
        fail('FIRST_LAUNCH_EVIDENCE_WINDOW_INVALID')
    return start, stop


def evidence_plan(launch, since, until):
    start, stop = _window(since, until)
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    ad = launch['stages'][-1]['readback']['object']
    ids = dict(ad_id=ad['id'], **{key: ad[key] for key in ('account_id','campaign_id','adset_id','creative_id')})
    if any(type(value) is not str or not ID.fullmatch(value) for value in ids.values()):
        fail('FIRST_LAUNCH_EVIDENCE_IDS_INVALID')
    return dict(plan_sha256=launch['plan_sha256'], workspace_identity_sha256=launch['workspace_identity_sha256'],
                ids=ids, since=start.isoformat(), until=stop.isoformat(),
                creative_route='/v27.0/' + ad['id'] + '?' + urlencode(
                    dict(fields='id,account_id,status,effective_status,creative{id,account_id,object_story_spec}')),
                activities_route='/v27.0/act_' + ad['account_id'] + '/activities',
                activities_params=dict(fields='object_id,event_type,event_time', limit=50,
                                       since=start.isoformat(), until=(stop+timedelta(days=1)).isoformat()),
                activities_max_pages=3)


def _creative(body, ids):
    if (type(body) is not dict or 'data' in body or 'paging' in body
            or (body.get('id'), body.get('account_id')) != (ids['ad_id'], ids['account_id'])):
        fail('FIRST_LAUNCH_EVIDENCE_AD_ID_MISMATCH')
    creative = body.get('creative')
    if creative is None:
        return dict(state='FIELD_MISSING', id=None)
    if type(creative) is not dict or creative.get('id') != ids['creative_id']:
        fail('FIRST_LAUNCH_EVIDENCE_CREATIVE_ID_MISMATCH')
    if creative.get('account_id') not in (None, ids['account_id']):
        fail('FIRST_LAUNCH_EVIDENCE_CROSS_ACCOUNT_CREATIVE')
    spec = creative.get('object_story_spec')
    media = dict(kind='NOT_OBSERVED', image_hash=None, video_id=None)
    if type(spec) is dict:
        branches = [key for key in ('link_data', 'video_data') if type(spec.get(key)) is dict]
        if len(branches) == 1:
            data = spec[branches[0]]
            from .first_launch_media import IMAGE_HASH
            image, video = data.get('image_hash'), data.get('video_id')
            media.update(kind='IMAGE' if branches[0] == 'link_data' else 'VIDEO',
                         image_hash=image if type(image) is str and IMAGE_HASH.fullmatch(image) else None,
                         video_id=video if type(video) is str and ID.fullmatch(video) else None)
    enum = re.compile(r'[A-Z_]{2,40}')
    statuses = {key: value if type(value) is str and enum.fullmatch(value) else None
                for key in ('status','effective_status') for value in [body.get(key)]}
    return dict(state='RETURNED', id=creative['id'], media=media, observed_ad_status=statuses,
                account_id_returned=creative.get('account_id') is not None)


def _activities(plan, get):
    targets = {value for key, value in plan['ids'].items() if key != 'account_id'}
    params = plan['activities_params'].copy()
    activities, cursors = {}, set()
    for _ in range(plan['activities_max_pages']):
        body = get(plan['activities_route'] + '?' + urlencode(params))
        data, paging = body.get('data'), body.get('paging', {})
        if type(data) is not list or len(data) > 50 or type(paging) is not dict:
            fail('FIRST_LAUNCH_EVIDENCE_ACTIVITIES_ENVELOPE')
        for row in data:
            if type(row) is not dict:
                fail('FIRST_LAUNCH_EVIDENCE_ACTIVITIES_ENVELOPE')
            obj, event, at = (row.get(key) for key in ('object_id', 'event_type', 'event_time'))
            if type(obj) is not str or obj not in targets:
                continue
            if type(event) is not str or not EVENT.fullmatch(event):
                fail('FIRST_LAUNCH_EVIDENCE_ACTIVITY_EVENT')
            when = aware_time(at)
            normalized = dict(object_id=obj, event_type=event, event_time=when.isoformat())
            activities[digest(normalized)] = normalized
        if not paging.get('next'):
            return dict(coverage='AVAILABLE_RESULTS', observations=list(activities.values()))
        container = paging.get('cursors')
        cursor = container.get('after') if type(container) is dict else None
        if type(cursor) is not str or not CURSOR.fullmatch(cursor) or cursor in cursors:
            break
        params['after'] = cursor
        cursors.add(cursor)
    return dict(coverage='TRUNCATED', observations=list(activities.values()))


def fetch(plan, get, *, clock=utcnow):
    creative = _creative(get(plan['creative_route']), plan['ids'])
    activities = _activities(plan, get)
    evidence = dict(plan=plan, captured_at=clock().isoformat(), creative=creative, activities=activities)
    return dict(status='FIRST_LAUNCH_EVIDENCE_RETURNED', evidence=evidence, evidence_sha256=digest(evidence))


def run(workspace, directory, since, until, execute):
    launch = progress(workspace, directory)
    plan = evidence_plan(launch, since, until)
    if not execute:
        return dict(status='FIRST_LAUNCH_EVIDENCE_PREVIEW', plan=plan, network_used=False,
                    required_permission='INDEPENDENT_NAMED_READ_APPROVAL')
    host = LocalFirstLaunchHost(scope_from_launch(launch))
    key = read_key(host.identity, 'delivery-evidence', digest(plan))
    lease = host._validated_lease('read', key)
    return dict(fetch(plan, lambda path: host._get(path, lease, assert_current=lambda: host.assert_lease(
        lease, 'read', key))), network_used=True)


def import_evidence(launch, path, since, until):
    if path is None:
        return dict(state='NOT_COLLECTED', creative=None, activities=None)
    from .creative_research import _value
    document = _value(path)
    evidence = document['evidence']
    fields = ('plan', 'captured_at', 'creative', 'activities')
    object_only(evidence, fields, fields)
    if evidence['plan'] != evidence_plan(launch, since, until) or document['evidence_sha256'] != digest(evidence):
        fail('FIRST_LAUNCH_DELIVERY_EVIDENCE_MISMATCH')
    aware_time(evidence['captured_at'])
    creative = evidence['creative']
    if (type(creative) is not dict or creative.get('state') not in ('FIELD_MISSING', 'RETURNED')
            or creative.get('id') != (evidence['plan']['ids']['creative_id'] if creative['state']=='RETURNED' else None)):
        fail('FIRST_LAUNCH_DELIVERY_EVIDENCE_MISMATCH')
    return dict(state='SUPPLIED_MATCHING_SCOPE', creative=evidence['creative'], activities=evidence['activities'],
                captured_at=evidence['captured_at'], evidence_sha256=document['evidence_sha256'])


def spend_focus(rows, context, coverage):
    if coverage != 'COMPLETE' or any(row.get('spend') is None for row in rows):
        return dict(state='INCOMPLETE', ad_ids=[], metrics=None)
    per_ad = defaultdict(Decimal)
    with localcontext() as arithmetic:
        arithmetic.prec = 64
        for row in rows:
            per_ad[row['ad_id']] += Decimal(row['spend'])
        total = sum(per_ad.values(), Decimal(0))
        if total <= 0:
            return dict(state='NO_POSITIVE_SPEND', ad_ids=[], metrics=None)
        selected = []
        cumulative = Decimal(0)
        for ad_id, amount in sorted(per_ad.items(), key=lambda pair: (-pair[1], pair[0])):
            selected.append(ad_id)
            cumulative += amount
            if cumulative >= total * Decimal('0.90'):
                break
    selected_ids = set(selected)
    members = [row for row in rows if row['ad_id'] in selected_ids]
    return dict(state='OBSERVED_SPEND_FOCUS', ad_ids=selected, combined_spend=str(cumulative), observed_spend=str(total),
                metrics=aggregate_ads(members, context, context, 'COMPLETE')['metrics'],
                hierarchy=aggregate_hierarchy(members, context, 'COMPLETE'))


def next_review(target, result_type, metrics, coverage, evidence):
    metrics = metrics or {}
    missing = []
    if not coverage:
        missing.append('COMPARABLE_COMPLETE_DAILY_WINDOW')
    if metrics.get('impressions', {}).get('value') is None:
        missing.append('IMPRESSIONS')
    primary = {'PURCHASE':'website_purchases', 'LEAD':'website_leads', 'LINK_CLICK':'clicks'}.get(result_type)
    if primary is None or metrics.get(primary, {}).get('value') is None:
        missing.append(primary.upper() if primary else 'PRIMARY_BUSINESS_RESULT')
    creative = evidence.get('creative')
    if creative is None or creative.get('state') != 'RETURNED':
        missing.append('SAME_AD_CREATIVE_EVIDENCE')
    if missing:
        return dict(action='INVESTIGATE', ad_id=target, reason='BUSINESS_EVIDENCE_INCOMPLETE',
                    missing=missing, next_step='COLLECT_MATCHING_WINDOW_AND_CREATIVE_BEFORE_CHANGING_DELIVERY')
    current = creative.get('observed_ad_status', {})
    if current.get('effective_status') in ('DISAPPROVED', 'WITH_ISSUES'):
        return dict(action='FIX', ad_id=target, reason='CURRENT_AD_EFFECTIVE_STATUS_REQUIRES_REVIEW',
                    missing=['PLATFORM_STATUS_CAUSE'], next_step='INSPECT_THE_NAMED_AD_BEFORE_REQUESTING_CHANGES')
    if Decimal(metrics['impressions']['value']) == 0:
        return dict(action='HOLD', ad_id=target, reason='ZERO_IMPRESSIONS_IN_COMPLETE_WINDOW',
                    missing=[], next_step='COMPARE_CURRENT_DELIVERY_AND_REVIEW_STATUS')
    return dict(action='HOLD', ad_id=target, reason='BASELINE_OBSERVED_WITHOUT_COMPARABLE_PRIOR',
                missing=['PRIOR_COMPARABLE_PERIOD'], next_step='COMPARE_WITH_A_MATCHING_PRIOR_WINDOW')
