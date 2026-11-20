"""Read saved Page Live contract checks and attended foreground runs."""
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import re

from .credential_record import public_record
from .http_read import PAGE_METRICS_BLOCKERS
from .journal import digest
from .live_contract import LIVE_CONTRACT_PROBE
from .live_timeline import HISTORY_LIMIT, summarize_cycle, timeline_data, validate_history
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS
from .monitor import CHANNELS, CONTROL_ERRORS, SAFE_ERRORS, dependency_error, validate_source
from .monitor_runner import ERRORS, PAGE_LIVE_SCHEMA, page_polling_preview, validate_monitor_intent, validate_page_contract_probe
from .monitor_tools import live_metrics_section, recent_comment_sample, validate_report_cycle_coverage
from .page_live_credentials import (DEBUG_DEVELOPER, DEBUG_PAGE, PAGE_ACTOR, PAGE_ROLE_STAGES, ROLE_OBSERVATION_BASIS,
                                    ROLE_OPTION, ROLE_VERIFIED, page_token_preflight_plan, startup_get_limit)
from .rate_limit import read_archived_hold
from .read_adapter import LIVE_END_STATES
from .validation import InputError, aware_time, fail, object_only, strict_json


RECEIPT_ERROR = 'PAGE_CONTRACT_EVIDENCE_RECEIPT'
SCOPE_ERROR = 'PAGE_CONTRACT_EVIDENCE_SCOPE'
TIMING_ERROR = 'PAGE_CONTRACT_EVIDENCE_TIMING'
OUTCOME_ERROR = 'PAGE_CONTRACT_EVIDENCE_OUTCOME'
REPORT_FILES = ('report/RECEIPT.json','report/REPORT.json','report/REPORT.md')
MANDATORY = {'PLAN.json','CONSENT.json','RESULT.json',*REPORT_FILES}
STAGES = (DEBUG_DEVELOPER,DEBUG_PAGE,PAGE_ACTOR,*PAGE_ROLE_STAGES)
ATTEMPTS = tuple(f'ATTEMPT-{i}.json' for i in range(1,len(STAGES)+1))
# The report repeats normalized metric buckets in the per-metric section.
LIMITS = {'PLAN.json':128000,'CONSENT.json':4000,'RESULT.json':96000,'CONNECTION.json':32000,
          'report/RECEIPT.json':16000,'report/REPORT.json':2_000_000,'report/REPORT.md':1_000_000,
          **dict.fromkeys(ATTEMPTS,4000)}
STATUSES = ('MONITOR_RUN_ENDED','MONITOR_REVIEW_REQUIRED','MONITOR_RUN_FAILED','INTERRUPTED')
SOURCE_FIELDS = dict(plan='query',phase='collector_phase',result='state',generation='generation',pages='valid_pages',
                     rows='rows',observed_at='observed_at',last_successful_sync='last_successful_sync',last_error='last_error')
GRANTS = ('NAMED_PAGE_IN_DEBUG_TARGETS','NO_EXPLICIT_PAGE_TARGET_LIST')


def require(condition, error):
    if not condition:
        fail(error)


def _member(root, name, limit):
    path = checked_path(root/name,link_error='PAGE_CONTRACT_EVIDENCE_LINK',volume_error='PAGE_CONTRACT_EVIDENCE_VOLUME')
    require(path.is_file(),'PAGE_CONTRACT_EVIDENCE_FILE_REQUIRED')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    require(len(data)<=limit,'PAGE_CONTRACT_EVIDENCE_FILE_SIZE')
    return data


def _read_archive(root):
    raw = _member(root,'RECEIPT.json',64000)
    receipt = strict_json(raw.decode('utf-8'))
    entries = receipt.get('files') if type(receipt) is dict else None
    require(type(entries) is list and len(MANDATORY)<=len(entries)<=len(LIMITS),RECEIPT_ERROR)
    values, identities = {}, {}
    for entry in entries:
        object_only(entry,('path','bytes','sha256'),('path','bytes','sha256'))
        require(type(entry['path']) is str,RECEIPT_ERROR)
        # The original receipt uses native relative separators. Resolve only fixed names.
        name = entry['path'].replace('\\','/')
        require(name in LIMITS and name not in identities,RECEIPT_ERROR)
        content = _member(root,name,LIMITS[name])
        identity = dict(path=name,bytes=len(content),sha256=sha256(content).hexdigest())
        require(type(entry['bytes']) is int and dict(entry,path=name)==identity,'PAGE_CONTRACT_EVIDENCE_FILE_HASH')
        identities[name] = identity
        if name.endswith('.json'):
            value = strict_json(content.decode('utf-8'),max_chars=LIMITS[name])
            require(type(value) is dict,RECEIPT_ERROR)
            values[name] = value
    require(MANDATORY<=identities.keys(),RECEIPT_ERROR)
    nested = values['report/RECEIPT.json']
    expected = [dict(identities[name],path=name.split('/')[-1]) for name in REPORT_FILES[1:]]
    require(nested.get('files')==expected and nested.get('status')=='BOUNDED_OBSERVATION_REPORT_WRITTEN'
            and nested.get('mode')=='READ_ONLY' and nested.get('real_connection_verified') is False
            and nested.get('live_action_authorized') is False,RECEIPT_ERROR)
    return receipt,sha256(raw).hexdigest(),values


def _scope(plan, consent, expected, *, contract_probe=True):
    intent = plan.get('intent')
    plans = validate_monitor_intent(intent)
    require(intent['schema']==PAGE_LIVE_SCHEMA,SCOPE_ERROR)
    if contract_probe:
        validate_page_contract_probe(intent,plans)
        require(plan.get('automatic_retry') is False and plan.get('release_gate_changes') is False,SCOPE_ERROR)
    identifier = digest(intent)
    require(expected is None or identifier==expected,'PAGE_CONTRACT_EVIDENCE_EXPECTED_SCOPE')
    startup = startup_get_limit(intent)
    preflight = page_token_preflight_plan('live_post' in plans,verify_role=intent.get(ROLE_OPTION,False))
    requests = list(preflight['requests'])
    if intent.get(ROLE_OPTION,False):
        requests.append(preflight['optional_insights_role_lookup'])
    connection = plan['connection']
    native = intent['connection']
    require(plan.get('schema')=='monitor-preview-v1' and plan.get('status')=='READY_FOR_LOCAL_SCOPE_CONFIRMATION'
            and plan.get('blockers')==[] and plan.get('source_requests')=={key:p.request() for key,p in plans.items()}
            and plan.get('page_token_preflight')==preflight and connection.get('requests')==requests
            and connection.get('intent')==native and native['credential_source']=='OS_REFERENCE'
            and native['app_developer_confirmed'] and native['asset_owner_confirmed']
            and native['appsecret_proof_requirement']=='NOT_REQUIRED_CONFIRMED'
            and plan.get('initial_get_attempts')==connection.get('maximum_get_attempts')==startup
            and plan.get('collector_attempt_limit')==intent['request_budget']-startup
            and plan.get('total_get_attempt_limit')==intent['request_budget']
            and all(plan.get(key) is False for key in ('network_used','remote_write_authorized',
                                                        'independent_human_identity_verified'))
            and consent.get('method')=='LOCAL_TERMINAL_SCOPE_CONFIRMATION'
            and consent.get('authorization_reusable') is False
            and consent.get('independently_authenticated_human') is False
            and consent.get('intent_sha256')==plan.get('intent_sha256')==identifier,SCOPE_ERROR)
    record = connection['credential_record']
    metadata = record['record']
    require(record==public_record(metadata) and metadata['state']=='PREPARED'
            and (metadata['app_id'],metadata['principal_id'],metadata['reference'])==
                (native['app_id'],native['principal_id'],native['credential_reference']),SCOPE_ERROR)
    start,end = aware_time(consent.get('approved_at')),aware_time(consent.get('run_deadline'))
    require(end-start==timedelta(seconds=intent['duration_seconds']),TIMING_ERROR)
    return intent,plans,identifier,start,end


def _attempts(files, result, intent, start, end):
    initial,collector,total = (result.get(key) for key in ('initial_get_attempts_reserved','collector_attempts_reserved',
                                                           'total_get_attempts_reserved'))
    startup = startup_get_limit(intent)
    require(all(type(count) is int for count in (initial,collector,total))
            and 0<=initial<=startup and 0<=collector<=intent['request_budget']-startup
            and total==initial+collector<=intent['request_budget']
            and result.get('request_count_semantics')=='RESERVED_NOT_PROOF_OF_TRANSMISSION',OUTCOME_ERROR)
    require(tuple(name for name in ATTEMPTS if name in files)==ATTEMPTS[:initial],OUTCOME_ERROR)
    stamps = []
    for name,stage in zip(ATTEMPTS[:initial],STAGES):
        marker = files[name]
        require(marker.get('stage')==stage,SCOPE_ERROR)
        stamp = aware_time(marker.get('reserved_at'))
        require((stamps[-1] if stamps else start)<=stamp,TIMING_ERROR)
        if result.get('metadata') is not None or result['status']=='MONITOR_RUN_ENDED':
            require(stamp<end,TIMING_ERROR)
        stamps.append(stamp)
    return stamps,collector


def _connection(files, result, report, intent, stamps, collector, end):
    proof = files.get('CONNECTION.json')
    metadata = result.get('metadata')
    if metadata is None:
        require(proof is None and collector==0 and result.get('preflight_page_actor_verified') is False
                and report.get('insights_role_observation') is None,OUTCOME_ERROR)
        return None,None,False
    require(type(proof) is dict and proof.get('metadata')==metadata and len(stamps)>=3
            and metadata.get('get_attempts')==len(stamps)
            and result.get('preflight_page_actor_verified') is True
            and result.get('native_access_attempted') is True and result.get('page_native_access_attempted') is True
            and metadata.get('status')=='NAMED_PAGE_TOKEN_PREFLIGHT_PASSED'
            and metadata.get('actor_binding')=='DEBUG_PROFILE_ID_AND_PAGE_TOKEN_ME_MATCH'
            and all(metadata.get(key) is False for key in ('authorization_reusable','credentials_persisted',
                                                            'live_endpoint_verified','comment_grant_verified')),OUTCOME_ERROR)
    page,scope = metadata['page'],intent['connection']
    keys = ('app_id','principal_id','page_id')
    require(all(page[key]==scope[key] for key in keys) and page.get('page_grant_binding') in GRANTS,SCOPE_ERROR)
    debug,actor,final,effective = (aware_time(value) for value in (
        page['observed_at'],metadata['actor_observed_at'],proof['observed_at'],page['effective_read_expires_at']))
    require(stamps[1]<=debug<=stamps[2]<=actor<=final<effective<=min(
        end,aware_time(page['token_expires_at']),aware_time(page['data_access_expires_at'])),TIMING_ERROR)
    role = metadata.get('insights_role_observation')
    verified = metadata.get('insights_role_verified')
    if intent.get(ROLE_OPTION,False):
        require(type(role) is dict and (not collector or role==report.get('insights_role_observation'))
                and all(role[key]==scope[key] for key in keys)
                and role.get('basis')==ROLE_OBSERVATION_BASIS and role.get('get_attempts')==len(stamps)-3
                and role.get('state')==page.get('insights_state')
                and role['state'] in PAGE_METRICS_BLOCKERS|{ROLE_VERIFIED}
                and verified is (role['state']==ROLE_VERIFIED),OUTCOME_ERROR)
        at = aware_time(role['observed_at'])
        require(max(actor,stamps[-1])<=at<=final,TIMING_ERROR)
        if verified:
            require(role['get_attempts']>0 and page.get('read_insights_grant_binding') in GRANTS,SCOPE_ERROR)
    else:
        require(role is None and report.get('insights_role_observation') is None
                and verified is False and len(stamps)==3,OUTCOME_ERROR)
    return final,effective,verified


def _metric_records(row):
    records = row['rows']
    if row['result']=='COMPLETE':
        require(1<=len(records)<=100 and all(record['state'] in ('VALUE','MISSING','EMPTY') for record in records)
                and (len(records)==1 or all(record['state']!='EMPTY' for record in records)),OUTCOME_ERROR)
    elif records:
        # An HTTP response can produce one normalized failure record without a valid results page.
        require(row['result']=='ERROR' and row['generation']>0 and len(records)==1
                and records[0]['state']=='ERROR' and records[0]['period'] is None
                and records[0]['bucket_end_time'] is None,OUTCOME_ERROR)
    require(row['result'] in ('NOT_RUN','COMPLETE','ERROR'),OUTCOME_ERROR)


def _saved_sources(report, plans):
    observations = report['observations']
    require(set(observations)==set(CHANNELS),OUTCOME_ERROR)
    entries = {}
    for name,observation in observations.items():
        if name not in plans:
            require(observation==dict(state='NOT_CONFIGURED',last_successful_sync=None),OUTCOME_ERROR)
            continue
        row = {key:observation[field] for key,field in SOURCE_FIELDS.items()}
        require(row['plan']==asdict(plans[name]),SCOPE_ERROR)
        validate_source(name,row)
        require(observation.get('current_query_rows')==len(row['rows']),OUTCOME_ERROR)
        entries[name] = row
    return entries


def _sources(result, report, plans, collector, earliest, latest, role_verified):
    entries = _saved_sources(report,plans)
    for name,row in entries.items():
        require(row['generation']<=1 and row['pages']<=row['generation']
                and (row['pages']>0)==(row['result'] in ('COMPLETE','EMPTY','GAP'))
                and row['last_successful_sync']==(row['observed_at'] if row['result'] in ('COMPLETE','EMPTY') else None)
                and (row['result']!='GAP' or name=='comments'),OUTCOME_ERROR)
        if name in METRIC_CHANNELS:
            _metric_records(row)
        else:
            limit = plans[name].limit if name=='comments' else 1
            require((row['pages'] or not row['rows']) and len(row['rows'])<=limit,OUTCOME_ERROR)
        if row['pages'] or row['rows']:
            observed = aware_time(row['observed_at'])
            require(earliest is not None and earliest<=observed<latest,TIMING_ERROR)
            if name in METRIC_CHANNELS:
                require(all(value['data_as_of'] is None and value['as_of']==value['observed_at']
                            and earliest<=aware_time(value['observed_at'])<=observed for value in row['rows']),TIMING_ERROR)
    require(sum(row['generation'] for row in entries.values())==collector
            and result.get('source_results')=={name:row['result'] for name,row in entries.items()},OUTCOME_ERROR)
    for name,row in entries.items():
        if row['generation']:
            require(not dependency_error(entries,name,row['plan'])
                    and (name not in METRIC_CHANNELS or role_verified),SCOPE_ERROR)
    if 'comments' in entries:
        require(report.get('live_comment_sample')==recent_comment_sample(entries['comments']),OUTCOME_ERROR)
    require(report.get('four_live_metrics')==live_metrics_section(entries),OUTCOME_ERROR)
    outcomes = {}
    for name,row in entries.items():
        outcome = dict(state=row['result'],valid_pages=row['pages'],error_code=row['last_error'],
                       attempt_reserved=bool(row['generation']))
        if name in METRIC_CHANNELS:
            outcome['metric_bucket_states'] = dict(Counter(bucket['state'] for bucket in row['rows']))
        outcomes[name] = outcome
    return outcomes


def review_page_contract(directory, *, expected_intent_sha256=None):
    if expected_intent_sha256 is not None:
        require(type(expected_intent_sha256) is str and re.fullmatch('[a-f0-9]{64}',expected_intent_sha256),
                'PAGE_CONTRACT_EVIDENCE_EXPECTED_DIGEST')
    root = checked_path(directory,link_error='PAGE_CONTRACT_EVIDENCE_LINK',volume_error='PAGE_CONTRACT_EVIDENCE_VOLUME')
    receipt,receipt_hash,files = _read_archive(root)
    try:
        plan,consent,result,report = (files[name] for name in ('PLAN.json','CONSENT.json','RESULT.json','report/REPORT.json'))
        require(all(item.get('execution_purpose')==LIVE_CONTRACT_PROBE for item in
                    (receipt,plan,consent,result,report,files['report/RECEIPT.json'])),SCOPE_ERROR)
        intent,plans,identifier,start,end = _scope(plan,consent,expected_intent_sha256)
        status,error = result.get('status'),result.get('error_code')
        require(status in STATUSES and receipt.get('status')==status
                and (error is None or type(error) is str and error in ERRORS|SAFE_ERRORS|CONTROL_ERRORS|{'USER_INTERRUPT'})
                and (status=='INTERRUPTED')==(error=='USER_INTERRUPT')
                and result.get('report')==files['report/RECEIPT.json']
                and all(result.get(key) is False for key in ('secret_persisted','remote_write_authorized','resumed'))
                and all(receipt.get(key) is False for key in ('real_live_verified','authorization_reusable',
                                                            'independent_human_identity_verified'))
                and report.get('mode')=='READ_ONLY'
                and all(report.get(key) is False for key in ('real_connection_verified','opaque_cursor_persisted',
                                                            'causal_claim_allowed'))
                and report.get('monitor_phase')==result.get('monitor_phase')
                and report.get('monitor_error')==result.get('monitor_last_error')
                and report.get('page_live_cycle_coverage')==result.get('page_live_cycle_coverage'),OUTCOME_ERROR)
        stamps,collector = _attempts(files,result,intent,start,end)
        require(report.get('request_attempts_reserved')==collector and report.get('request_budget')==len(plans)
                and type(report.get('collection_cycles')) is int and 0<=report['collection_cycles']<=1,OUTCOME_ERROR)
        earliest,latest,role_verified = _connection(files,result,report,intent,stamps,collector,end)
        sources = _sources(result,report,plans,collector,earliest,latest,role_verified)
        if status=='MONITOR_RUN_ENDED':
            require(error is None and collector==len(plans) and report['collection_cycles']==1
                    and all(row['valid_pages']==1 for row in sources.values()),OUTCOME_ERROR)
    except (AttributeError,KeyError,TypeError,RecursionError):
        fail(OUTCOME_ERROR)
    return dict(status='PAGE_LIVE_CONTRACT_EVIDENCE_CHECKED',recorded_status=status,recorded_error_code=error,
                sources=sources,intent_sha256=identifier,receipt_sha256=receipt_hash,
                initial_get_attempts_reserved=len(stamps),collector_attempts_reserved=collector,
                independent_runtime_verified=False,real_live_contract_accepted=False,
                authorization_reusable=False,network_used=False,native_credential_accessed=False)


def _saved_run_budget(report, result, intent, plans, collector):
    cycles = report.get('collection_cycles')
    coverage = report.get('page_live_cycle_coverage')
    require(type(cycles) is int and 0<=cycles<=intent['cycles']
            and report.get('request_attempts_reserved')==collector
            and report.get('request_budget')==intent['request_budget']-startup_get_limit(intent),OUTCOME_ERROR)
    if coverage is None:
        require(cycles==collector==0,OUTCOME_ERROR)
    else:
        require(type(coverage) is dict and type(coverage.get('cycle')) is int and coverage['cycle']==cycles,OUTCOME_ERROR)
        validate_report_cycle_coverage(report['observations'],coverage,error=OUTCOME_ERROR,page_live=True)
    rate = read_archived_hold(report,result,collector,OUTCOME_ERROR)
    retries = 0 if rate is None else rate['automatic_retries_reserved']
    per_cycle = len(plans) + (intent['pages_per_source']-1 if 'comments' in plans else 0)
    attempts = sum(row['valid_pages'] for row in report['observations'].values() if row['state']!='NOT_CONFIGURED')
    pending = coverage is not None and 'uncommitted_request' in coverage
    require(attempts+pending+retries<=collector<=cycles*per_cycle+retries,OUTCOME_ERROR)
    return cycles, coverage


def _saved_run_sources(report, result, plans, coverage, page_limit, earliest, latest, role_verified):
    entries = _saved_sources(report,plans)
    require(result.get('source_results')=={name:row['result'] for name,row in entries.items()},OUTCOME_ERROR)
    sampled = set(coverage['sampled_sources']) if coverage is not None else set()
    outcomes = {}
    for name,row in entries.items():
        count, pages, error = len(row['rows']), row['pages'], row['last_error']
        require((row['result'] not in ('COMPLETE','EMPTY','GAP') or pages>0)
                and pages<=(page_limit if name=='comments' else 1),OUTCOME_ERROR)
        if name in METRIC_CHANNELS:
            _metric_records(row)
        else:
            require(count<=(pages*plans[name].limit if name=='comments' else pages),OUTCOME_ERROR)
        if name not in sampled:
            require(pages==count==0 and row['observed_at'] is None and row['result'] in ('NOT_RUN','ERROR'),OUTCOME_ERROR)
        if name in sampled:
            require(not dependency_error(entries,name,row['plan'])
                    and (name not in METRIC_CHANNELS or role_verified),SCOPE_ERROR)
        if pages:
            observed = aware_time(row['observed_at'])
            require(earliest is not None and earliest<=observed<latest,TIMING_ERROR)
            if row['result'] in ('COMPLETE','EMPTY'):
                require(row['last_successful_sync']==row['observed_at'],OUTCOME_ERROR)
        if name in METRIC_CHANNELS and count:
            observed = aware_time(row['observed_at'])
            require(earliest is not None and all(value['data_as_of'] is None and value['as_of']==value['observed_at']
                    and earliest<=aware_time(value['observed_at'])<=observed for value in row['rows']),TIMING_ERROR)
        outcomes[name] = dict(state=row['result'],valid_pages=pages,error_code=error,sampled_in_current_cycle=name in sampled)
        if name in METRIC_CHANNELS:
            outcomes[name]['metric_bucket_states'] = dict(Counter(bucket['state'] for bucket in row['rows']))
    if 'comments' in entries:
        require(report.get('live_comment_sample')==recent_comment_sample(entries['comments']),OUTCOME_ERROR)
    require(report.get('four_live_metrics')==live_metrics_section(entries),OUTCOME_ERROR)
    live = entries['live']
    ended = (live['phase']=='ENDED' and live['result']=='COMPLETE'
             and live['rows'][0].get('status') in LIVE_END_STATES)
    require(report.get('live_end')==('END_STATUS_OBSERVED' if ended else 'NOT_ESTABLISHED'),OUTCOME_ERROR)
    return entries,outcomes


def _saved_run_timeline(report, entries, coverage, cycles, run_id, start, end):
    timeline = report.get('live_cycle_timeline')
    if cycles==0:
        require(timeline is None,OUTCOME_ERROR)
        return None
    rows = timeline['cycles']
    state = dict(sources=entries,cycle_count=cycles,last_heartbeat=end.isoformat(),live_cycle_history=rows,
                 page_run={'id':run_id})
    validate_history(state,SAFE_ERRORS)
    require(all(aware_time(row['observed_at'])>=start for row in rows),TIMING_ERROR)
    latest = rows[-1]['cycle'] if rows else 0
    require(cycles-1<=latest<=cycles and [row['cycle'] for row in rows]==list(range(max(1,latest-HISTORY_LIMIT+1),latest+1))
            and timeline==timeline_data(state),OUTCOME_ERROR)
    if rows and latest==cycles:
        last = rows[-1]
        state['last_heartbeat'] = last['observed_at']
        expected = summarize_cycle(state,set(coverage['sampled_sources']))
        require(expected=={key:last[key] for key in expected},OUTCOME_ERROR)
    return timeline


def read_page_run_observations(directory, *, expected_intent_sha256=None):
    """Verify an exported foreground run without the original workspace or runner."""
    if expected_intent_sha256 is not None:
        require(type(expected_intent_sha256) is str and re.fullmatch('[a-f0-9]{64}',expected_intent_sha256),
                'PAGE_CONTRACT_EVIDENCE_EXPECTED_DIGEST')
    root = checked_path(directory,link_error='PAGE_CONTRACT_EVIDENCE_LINK',volume_error='PAGE_CONTRACT_EVIDENCE_VOLUME')
    receipt,receipt_hash,files = _read_archive(root)
    try:
        plan,consent,result,report = (files[name] for name in ('PLAN.json','CONSENT.json','RESULT.json','report/REPORT.json'))
        require(all('execution_purpose' not in item for item in
                    (receipt,plan,consent,result,report,files['report/RECEIPT.json'])),SCOPE_ERROR)
        intent,plans,identifier,start,end = _scope(plan,consent,expected_intent_sha256,contract_probe=False)
        require(plan.get('page_live_polling')==page_polling_preview(intent),SCOPE_ERROR)
        key = 'workspace_identity_sha256'
        bindings = (consent,report,result,files['report/RECEIPT.json'])
        identity = None
        if any(key in item for item in bindings):
            identity = consent.get(key)
            require(type(identity) is str and re.fullmatch('[0-9a-f]{64}',identity)
                    and all(item.get(key)==identity for item in bindings), 'PAGE_RUN_EVIDENCE_WORKSPACE_BINDING')
        run_id, status, error = (result.get(key) for key in ('run_id','status','error_code'))
        require(type(run_id) is str and re.fullmatch('[a-f0-9]{32}',run_id)
                and status in STATUSES and receipt.get('status')==status
                and (error is None or type(error) is str and error in ERRORS|SAFE_ERRORS|CONTROL_ERRORS|{'USER_INTERRUPT'})
                and (status=='INTERRUPTED')==(error=='USER_INTERRUPT')
                and type(result.get('resumed')) is bool
                and result.get('report')==files['report/RECEIPT.json']
                and result.get('remote_write_authorized') is False and result.get('secret_persisted') is False
                and all(receipt.get(key) is False for key in ('real_live_verified','authorization_reusable',
                                                            'independent_human_identity_verified'))
                and report.get('schema')=='bounded-monitor-report-v1' and report.get('mode')=='READ_ONLY'
                and all(report.get(key) is False for key in ('real_connection_verified','opaque_cursor_persisted',
                                                            'causal_claim_allowed'))
                and report.get('monitor_phase')==result.get('monitor_phase') in ('STOPPED','PAUSED','INTERRUPTED')
                and report.get('monitor_error')==result.get('monitor_last_error') in CONTROL_ERRORS
                and report.get('page_live_cycle_coverage')==result.get('page_live_cycle_coverage'),OUTCOME_ERROR)
        stamps,collector = _attempts(files,result,intent,start,end)
        cycles,coverage = _saved_run_budget(report,result,intent,plans,collector)
        earliest,latest,role_verified = _connection(files,result,report,intent,stamps,collector,end)
        entries,sources = _saved_run_sources(report,result,plans,coverage,intent['pages_per_source'],
                                             earliest,latest,role_verified)
        timeline = _saved_run_timeline(report,entries,coverage,cycles,run_id,start,end)
        if status=='MONITOR_RUN_ENDED':
            require(error in (None,'MONITOR_CONTROL_CHANGED')
                    and (coverage is None or coverage['state']!='INCOMPLETE'),OUTCOME_ERROR)
    except (AttributeError,KeyError,TypeError,RecursionError):
        fail(OUTCOME_ERROR)
    observations = dict(mode='READ_ONLY',phase=report['monitor_phase'],last_error=report['monitor_error'],
                        requests_reserved=collector,request_budget=report['request_budget'],cycle_count=cycles,sources=entries)
    if report.get('rate_limit') is not None:
        observations.update(rate_limit=report['rate_limit'],rate_limit_pending=report['rate_limit_pending'])
    if error is not None:
        observations['recorded_run_error_code'] = error
    if identity is not None:
        observations['workspace_identity_sha256'] = identity
    if coverage is not None:
        observations['page_live_cycle_coverage'] = coverage
    if timeline is not None:
        observations['live_cycle_history'] = timeline['cycles']
    report_hash = next(item['sha256'] for item in receipt['files'] if item['path'].replace('\\','/')=='report/REPORT.json')
    evidence = dict(status='PAGE_LIVE_RUN_EVIDENCE_CHECKED',run_id=run_id,recorded_status=status,recorded_error_code=error,
                    sources=sources,intent_sha256=identifier,receipt_sha256=receipt_hash,report_sha256=report_hash,
                    live_end=report['live_end'],cycles_entered=cycles,cycle_coverage=coverage,cycle_timeline=timeline,
                    initial_get_attempts_reserved=len(stamps),collector_attempts_reserved=collector,
                    independent_runtime_verified=False,real_live_contract_accepted=False,
                    authorization_reusable=False,network_used=False,native_credential_accessed=False)
    return observations,evidence


def review_page_run(directory, *, expected_intent_sha256=None):
    _, evidence = read_page_run_observations(directory,expected_intent_sha256=expected_intent_sha256)
    return evidence
