"""Bounded Graph Ads Library queries, separate from customer Insights."""

import getpass
from datetime import date
import json
import re
import sys
from time import monotonic
from urllib.parse import urlencode
import warnings

from .connection import _credential_format, _get_connection_json
from .creative_research import _normalize_public, _value, add_public_record
from .meta_write_mapping import utcnow
from .operator_review import evidence_text, load_input, minor, pattern
from .validation import InputError, fail

FIELDS = ('id','page_id','page_name','ad_creative_bodies','ad_delivery_start_time',
          'ad_delivery_stop_time','publisher_platforms',
          'ad_creative_link_titles','ad_creative_link_descriptions','ad_creative_link_captions','ad_snapshot_url')
VERSION = 'v27.0'


def _delivery_day(value):
    if value is None:
        return None
    if type(value) is not str or len(value) < 10:
        fail('PUBLIC_AD_QUERY_RESPONSE_DATE_INVALID')
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        fail('PUBLIC_AD_QUERY_RESPONSE_DATE_INVALID')


def query(scope, sample_limit, page_limit, *, page_id=None, archive_id=None):
    _normalize_public(dict(scope=scope,records=[],coverage='SAMPLE'))
    if (type(sample_limit) is not int or not 1<=sample_limit<=50 or
            type(page_limit) is not int or not 1<=page_limit<=5):
        fail('PUBLIC_AD_QUERY_LIMIT_INVALID')
    params=dict(search_terms=scope['query'],ad_reached_countries=json.dumps(scope['countries'],separators=(',',':')),
                ad_active_status=scope['status'],ad_type='ALL',fields=','.join(FIELDS))
    if (page_id is None) != (archive_id is None):
        fail('PUBLIC_AD_DETAIL_BOTH_IDS_REQUIRED')
    if page_id is not None:
        if any(not isinstance(v, str) or not re.fullmatch(r'[0-9]{1,40}', v) for v in (page_id, archive_id)):
            fail('PUBLIC_AD_QUERY_RESPONSE_ID_INVALID')
        params['search_page_ids'] = json.dumps([page_id], separators=(',', ':'))
    for key in ('min','max'):
        if scope['date_'+key] is not None:params['ad_delivery_date_'+key]=scope['date_'+key]
    plan = dict(scope=scope,sample_limit=sample_limit,page_limit=page_limit,parameters=params)
    if page_id is not None:
        plan['detail_target'] = {'page_id': page_id, 'ad_archive_id': archive_id}
    return plan



def _marked_fields(row):
    result = {}
    for key, field in (
        ('titles', 'ad_creative_link_titles'),
        ('descriptions', 'ad_creative_link_descriptions'),
        ('captions', 'ad_creative_link_captions'),
    ):
        if row.get(field) is None:
            continue
        values = row[field]
        if type(values) is not list or len(values) > 50 or any(type(v) is not str for v in values):
            fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        texts = [v.strip()[:800] for v in values if v.strip()][:8]
        if texts:
            result[key] = texts
    platforms = row.get('publisher_platforms')
    if platforms is not None:
        if type(platforms) is not list or len(platforms) > 16:
            fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        if any(type(v) is not str or not re.fullmatch('[a-z_]{2,40}', v) for v in platforms):
            fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        result['publisher_platforms'] = platforms
    # Snapshot URLs may contain an access_token. Record only its availability.
    if 'ad_snapshot_url' in row:
        if row['ad_snapshot_url'] is not None and type(row['ad_snapshot_url']) is not str:
            fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        result['snapshot_available'] = bool(row['ad_snapshot_url'])
    return result

def fetch(plan,get,*,clock=utcnow):
    entries={}
    after=None
    cursors=set()
    coverage='SAMPLE'
    target = plan.get('detail_target')
    for index in range(plan['page_limit']):
        params=dict(plan['parameters'],limit=min(25,plan['sample_limit']-len(entries)))
        if after is not None:params['after']=after
        try:
            body=get('/'+VERSION+'/ads_archive?'+urlencode(params))
        except InputError as error:
            if index == 0 or str(error) not in ('PERMISSION','OBJECT_OR_PARAMETER','RATE_LIMIT','HTTP_NETWORK_OR_TLS_FAILURE'):
                raise
            return dict(scope=plan['scope'], records=list(entries.values()), coverage='TRUNCATED',
                        stop='INCOMPLETE_PAGE_READ', error_code=str(error))
        rows=body.get('data') if type(body) is dict else None
        if type(rows) is not list or len(rows)>25:
            fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        paging=body.get('paging',{})
        if type(paging) is not dict:fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
        has_next=bool(paging.get('next'))
        for i,row in enumerate(rows):
            if type(row) is not dict:fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
            identifier,page=row.get('id'),row.get('page_id')
            if any(type(v) is not str or not re.fullmatch(r'[0-9]{1,40}',v) for v in (identifier,page)):
                fail('PUBLIC_AD_QUERY_RESPONSE_ID_INVALID')
            if target and (identifier, page) != (target['ad_archive_id'], target['page_id']):
                continue
            bodies=row.get('ad_creative_bodies')
            if bodies is not None and (type(bodies) is not list or any(type(x) is not str for x in bodies)):
                fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
            source_url = 'https://www.facebook.com/ads/library/?id=' + identifier
            observed_at = clock().isoformat()
            body = '\n'.join(bodies)[:2400] if bodies and any(x.strip() for x in bodies) else None
            observed = _marked_fields(row)
            detail = dict(ad_archive_id=identifier, page_id=page, source_url=source_url,
                          observed_at=observed_at, body=body) if target or observed else None
            if observed:
                detail['additional_observations'] = dict(observed, basis='GRAPH_ADS_ARCHIVE')
            add_public_record(entries, dict(ad_archive_id=identifier,page_id=page,
                source_url=source_url,observed_at=observed_at,body=body,
                delivery_start=_delivery_day(row.get('ad_delivery_start_time')),
                delivery_stop=_delivery_day(row.get('ad_delivery_stop_time')),
                detail=detail,angle_hypothesis=None))
            if len(entries)==plan['sample_limit']:
                coverage='TRUNCATED' if has_next or i<len(rows)-1 else 'SAMPLE'
                return dict(scope=plan['scope'],records=list(entries.values()),coverage=coverage,stop='SAMPLE_LIMIT')
        if not has_next:break
        cursor=paging.get('cursors')
        after=cursor.get('after') if type(cursor) is dict else None
        if (type(after) is not str or not re.fullmatch(r'[A-Za-z0-9._~+=/-]{1,1024}',after)
                or after in cursors):
            return dict(scope=plan['scope'],records=list(entries.values()),coverage='TRUNCATED',stop='CURSOR_UNAVAILABLE')
        cursors.add(after)
    else:
        coverage='TRUNCATED'
    return dict(scope=plan['scope'],records=list(entries.values()),coverage=coverage,
                stop='PAGE_LIMIT' if coverage=='TRUNCATED' else 'END_OF_AVAILABLE_RESULTS')


def run_query(path, sample_limit, page_limit, execute, *, page_id=None, archive_id=None):
    plan=query(_value(path),sample_limit,page_limit,page_id=page_id,archive_id=archive_id)
    if not execute:
        return dict(status='PUBLIC_AD_QUERY_PREVIEW',plan=plan,endpoint='/'+VERSION+'/ads_archive',
                    network_used=False,access='REQUIRES_SEPARATE_ADS_LIBRARY_API_ACCESS')
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        fail('PUBLIC_AD_QUERY_INTERACTIVE_AUTHORIZATION_REQUIRED')
    print(json.dumps(plan,ensure_ascii=False),file=sys.stderr)
    if input('Type READ PUBLIC ADS to confirm this scoped query: ')!='READ PUBLIC ADS':
        fail('PUBLIC_AD_QUERY_NOT_CONFIRMED')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try: token=getpass.getpass('Ads Library token (hidden, not saved): ')
        except getpass.GetPassWarning as exc: raise InputError('SECRET_TERMINAL_UNAVAILABLE') from exc
    _credential_format(token)
    end=monotonic()+45
    def check():
        if monotonic()>=end:fail('PUBLIC_AD_QUERY_DEADLINE')
    try:
        return query_result(plan, lambda route: _get_connection_json(
            route, token, check, time_remaining=lambda: end - monotonic()))
    finally:
        token=None


def query_result(plan, get):
    try:
        result = fetch(plan, get)
    except InputError as error:
        if str(error) not in ("PERMISSION", "OBJECT_OR_PARAMETER"):
            raise
        return dict(status="PUBLIC_AD_QUERY_SCOPE_UNAVAILABLE", error_code=str(error),
                    fallback="IMPORT_AUTHORIZED_PUBLIC_OBSERVATIONS", network_used=True)
    stop=result.pop('stop')
    error = result.pop('error_code', None)
    state = ("PARTIAL" if result["coverage"] == "TRUNCATED" else
             "ROWS_OBSERVED" if result["records"] else "NO_ROWS_IN_OBSERVED_SCOPE")
    reply = dict(status='PUBLIC_AD_QUERY_RETURNED', observation=_normalize_public(result), stop=stop,
                 data_state=state, network_used=True)
    if error is not None:
        reply.update(error_code=error, fallback='IMPORT_AUTHORIZED_PUBLIC_OBSERVATIONS')
    if 'detail_target' in plan:
        reply['detail_state'] = 'ID_PAIR_OBSERVED' if result['records'] else 'PAIR_NOT_OBSERVED_IN_SCOPE'
    return reply


def inspect_detail(path, page_id, archive_id, media=None, angle=None):
    for identifier in (page_id, archive_id):
        pattern(identifier, r"[0-9]{1,40}")
    document = load_input(path)
    if type(document) is dict and document.get('status') == 'PUBLIC_AD_QUERY_RETURNED':
        observed = document.get('observation')
        if type(observed) is not dict:
            fail('PUBLIC_AD_DETAIL_INVALID')
        document = {key: observed.get(key) for key in ('scope', 'records', 'coverage')}
    normalized = _normalize_public(document)
    matching = next((row for row in normalized['records']
                     if (row['page_id'], row['ad_archive_id']) == (page_id, archive_id)), None)
    media_evidence = None
    if media is not None:
        evidence = load_input(media)
        if not isinstance(evidence, dict) or evidence.get("status") not in ("LOCAL_MEDIA_EVIDENCE", "LOCAL_MEDIA_PARTIAL"):
            fail("PUBLIC_AD_DETAIL_MEDIA_REPORT_INVALID")
        pairs = ("media_sha256", "duration_ms", "frames")
        if (not all(key in evidence for key in pairs) or type(evidence["frames"]) is not list
                or not 1 <= len(evidence["frames"]) <= 3 or minor(evidence["duration_ms"]) is None):
            fail("PUBLIC_AD_DETAIL_MEDIA_REPORT_INVALID")
        media_evidence = {
            "sha256": pattern(evidence["media_sha256"], r"[a-f0-9]{64}"),
            "format_observed": "VIDEO" if evidence["duration_ms"] else "STILL_IMAGE",
            "frame_count": len(evidence["frames"]),
            "source": "USER_SUPPLIED_MEDIA_PUBLIC_AD_LINK_UNVERIFIED",
        }
    fields = {
        "observed_at": "COLLECTION_TIME", "delivery_start": "PLATFORM_FIELD", "delivery_stop": "PLATFORM_FIELD",
        "body": "PLATFORM_OR_SUPPLIED_TEXT", "source_url": "PUBLIC_REFERENCE",
    }
    field_coverage = {key: {"state": "OBSERVED" if matching and matching.get(key) is not None else "UNAVAILABLE",
                            "basis": basis} for key, basis in fields.items()}
    extra = (matching.get('detail') or {}).get('additional_observations', {}) if matching else {}
    for key in ('titles', 'descriptions', 'captions', 'publisher_platforms', 'snapshot_available',
                'call_to_action', 'destination', 'media_observations'):
        field_coverage[key] = dict(state='OBSERVED' if key in extra else 'UNAVAILABLE',
                                   basis=extra.get('basis') if key in extra else None)

    return dict(status="PUBLIC_AD_DETAIL_REVIEW", page_id=page_id, ad_archive_id=archive_id,
                coverage=normalized["coverage"], search_scope=normalized["scope"], field_coverage=field_coverage,
                official_field_reference="https://developers.facebook.com/docs/marketing-api/reference/archived-ad/",
                observed=matching, supplied_media=media_evidence,
                reviewed_angle_hypothesis=evidence_text(angle) if angle is not None else None,
                public_linkage_verified=False, own_account_metrics=False, network_used=False)
