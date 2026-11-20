"""Offline relationship and per-metric contracts; real Live access remains gated."""
from .analysis import METRICS, live_metric
from .read_adapter import RECENT_LIVE_COMMENTS, ReadPlan, error_category, identifier, normalize_row, numeric_string, post_identifier
from .validation import aware_time, fail

LIVE_CONTRACT_PROBE = 'T1_NAMED_PAGE_LIVE_CONTRACT_CHECK'


def relationship(page_id, live_id, returned_live):
    plan=ReadPlan('live',live_id,page_id=page_id,reviewed_live_contract=True)
    live=normalize_row(plan,returned_live)
    if not live.get('video',{}).get('id'):fail('VIDEO_RELATION_NOT_VERIFIED')
    return {'page_id':page_id,'live_id':live_id,'video_id':live['video']['id'],
            'status':live.get('status','UNKNOWN'),'evidence':'RETURNED_RELATION_FIELDS_ONLY',
            'ownership_independently_verified':False,'real_connection_verified':False}


def planned_queries(relation, page_post_id=None):
    for key in ('page_id','live_id'):
        identifier(relation[key])
    video = relation.get('video_id')
    if video is not None:
        identifier(video)
    if page_post_id is not None:
        post_identifier(page_post_id,relation['page_id'])
    live = ReadPlan('live',relation['live_id'],page_id=relation['page_id'],reviewed_live_contract=True)
    if video is None:
        return {'state':'REAL_LIVE_CONTRACT_PENDING','network_authorized':False,'plan_stage':'IDENTITY_ONLY',
                'live_identity':live.request(),'comments':None,'post_link':None,'metrics':[],
                'metric_requirements':['RETURNED_LIVE_VIDEO_ID_REQUIRED'],
                'source_scope':'V27_LIVE_IDENTITY_CANDIDATE_NO_ACCOUNT_EVIDENCE'}
    return {'state':'REAL_LIVE_CONTRACT_PENDING','network_authorized':False,'plan_stage':'DECLARED_RELATION_PREVIEW',
            'live_identity':live.request(),
            'comments':ReadPlan(RECENT_LIVE_COMMENTS,relation['live_id'],page_id=relation['page_id'],
                                reviewed_live_contract=True).request(),
            'post_link':(ReadPlan('live_post',page_post_id,page_id=relation['page_id'],reviewed_live_contract=True).request()
                         if page_post_id is not None else None),
            'metrics':[{'method':'GET','path':f'/v27.0/{page_post_id}/insights','params':{'metric':metric}}
                       for metric in METRICS] if page_post_id is not None else [],
            'metric_requirements':['NAMED_PAGE_POST','DIRECT_LIVE_VIDEO_MATCH','VERIFIED_PAGE_INSIGHTS_PERMISSIONS'],
            'source_scope':'V27_PAGE_POST_CANDIDATE_NO_ACCOUNT_EVIDENCE'}


def count_value(raw):
    if type(raw) is int:
        if raw<0:fail('NEGATIVE_METRIC_COUNT')
        return numeric_string(str(raw),integer=True)
    if type(raw) is str:return numeric_string(raw,integer=True)
    if type(raw) is dict:
        if not raw or len(raw)>20:fail('INVALID_STRUCTURED_METRIC')
        result={}
        for key,value in raw.items():
            if type(value) not in (str,int):fail('NESTED_METRIC_NOT_SUPPORTED')
            result[key]=count_value(value)
        return result
    fail('METRIC_COUNT_TYPE')


def metric_observations(metric,object_id,http_status,body,observed_at,as_of,max_age_seconds,data_as_of=None,*,post=False):
    if metric not in METRICS:fail('LIVE_METRIC_UNSUPPORTED')
    (post_identifier if post else identifier)(object_id)
    aware_time(observed_at)
    if type(body) is not dict or type(http_status) is not int or not 100<=http_status<=599:fail('INVALID_RESPONSE_ENVELOPE')
    if data_as_of is not None:aware_time(data_as_of)
    base={'metric':metric,'object_id':object_id,'observed_at':observed_at,'data_as_of':data_as_of}
    def build(state,period=None,value=None,end=None):
        record={**base,'state':state,'period':period}
        if state=='VALUE':record['value']=count_value(value)
        if end is not None:record['bucket_end_time']=end
        return live_metric(record,as_of,max_age_seconds)
    if 'error' in body or not 200<=http_status<300:
        return {'state':'ERROR','error_code':error_category(http_status,body),'observations':[build('ERROR')],
                'real_live_validated':False}
    if type(body.get('data')) is not list:fail('DATA_ARRAY_REQUIRED')
    paging=body.get('paging',{})
    if type(paging) is not dict:fail('INVALID_PAGING')
    if paging.get('next') is not None:fail('METRIC_PAGINATION_UNSUPPORTED')
    matches=[row for row in body['data'] if type(row) is dict and row.get('name')==metric]
    if len(body['data'])>20 or len(matches)>1:fail('METRIC_ENVELOPE_AMBIGUOUS')
    if not matches:return {'state':'MISSING','observations':[build('MISSING')],'real_live_validated':False}
    row=matches[0];period=row.get('period')
    if 'values' not in row:return {'state':'MISSING','observations':[build('MISSING',period)],'real_live_validated':False}
    values=row['values']
    if type(values) is not list or len(values)>100:fail('METRIC_BUCKET_BUDGET')
    if not values:return {'state':'EMPTY','observations':[build('EMPTY',period)],'real_live_validated':False}
    observations=[];seen=set()
    for item in values:
        if type(item) is not dict:fail('METRIC_BUCKET_TYPE')
        record=build('VALUE' if item.get('value') is not None else 'MISSING',period,item.get('value'),item.get('end_time'))
        key=(record['period'],aware_time(record['bucket_end_time']) if record['bucket_end_time'] else None)
        if key in seen:fail('METRIC_BUCKET_AMBIGUOUS')
        seen.add(key);observations.append(record)
    return {'state':'OBSERVED_BUCKETS','observations':observations,'real_live_validated':False,
            'bucket_comparison':'Select the same metric/object/period/window explicitly; no automatic summation',
            'data_as_of_basis':'CALLER_VERIFIED_SOURCE_UPDATE_METADATA' if data_as_of is not None else 'UNKNOWN_NOT_INFERRED_FROM_POLL_OR_BUCKET_END'}
