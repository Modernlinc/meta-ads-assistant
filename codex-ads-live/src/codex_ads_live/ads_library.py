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
from .validation import InputError, fail

FIELDS = ('id','page_id','page_name','ad_creative_bodies','ad_delivery_start_time',
          'ad_delivery_stop_time','publisher_platforms')
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


def query(scope, sample_limit, page_limit):
    _normalize_public(dict(scope=scope,records=[],coverage='SAMPLE'))
    if (type(sample_limit) is not int or not 1<=sample_limit<=50 or
            type(page_limit) is not int or not 1<=page_limit<=5):
        fail('PUBLIC_AD_QUERY_LIMIT_INVALID')
    params=dict(search_terms=scope['query'],ad_reached_countries=json.dumps(scope['countries'],separators=(',',':')),
                ad_active_status=scope['status'],ad_type='ALL',fields=','.join(FIELDS))
    for key in ('min','max'):
        if scope['date_'+key] is not None:params['ad_delivery_date_'+key]=scope['date_'+key]
    return dict(scope=scope,sample_limit=sample_limit,page_limit=page_limit,parameters=params)


def fetch(plan,get,*,clock=utcnow):
    entries={}
    after=None
    cursors=set()
    coverage='SAMPLE'
    for _ in range(plan['page_limit']):
        params=dict(plan['parameters'],limit=min(25,plan['sample_limit']-len(entries)))
        if after is not None:params['after']=after
        body=get('/'+VERSION+'/ads_archive?'+urlencode(params))
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
            bodies=row.get('ad_creative_bodies')
            if bodies is not None and (type(bodies) is not list or any(type(x) is not str for x in bodies)):
                fail('PUBLIC_AD_QUERY_RESPONSE_INVALID')
            add_public_record(entries, dict(ad_archive_id=identifier,page_id=page,
                source_url='https://www.facebook.com/ads/library/?id='+identifier,observed_at=clock().isoformat(),
                body='\n'.join(bodies)[:2400] if bodies and any(x.strip() for x in bodies) else None,
                delivery_start=_delivery_day(row.get('ad_delivery_start_time')),
                delivery_stop=_delivery_day(row.get('ad_delivery_stop_time')),
                detail=None,angle_hypothesis=None))
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


def run_query(path, sample_limit, page_limit, execute):
    plan=query(_value(path),sample_limit,page_limit)
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
        result=fetch(plan,lambda route:_get_connection_json(route,token,check,time_remaining=lambda:end-monotonic()))
    finally:
        token=None
    stop=result.pop('stop')
    return dict(status='PUBLIC_AD_QUERY_RETURNED',observation=_normalize_public(result),stop=stop,network_used=True)
