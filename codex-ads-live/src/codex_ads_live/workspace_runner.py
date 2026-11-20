"""Read scoped local runner evidence without starting or resuming collection."""
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import re
from stat import S_ISREG

from .journal import digest
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS
from .monitor import (ACCOUNT_CYCLE_PAGINATION_UNVERIFIED, AD_INSIGHTS_CHANNEL_SETS, AD_INVENTORY_CHANNELS,
                      AD_PLACEMENT_CHANNELS, PAGE_RUN_FAILURES,
                      ReadMonitor, account_cycle_pagination_unverified, complete_row)
from .monitor_runner import PAGE_LIVE_SCHEMA, load_monitor_intent, recorded_monitor_intent, validate_monitor_intent
from .monitor_tools import FOREGROUND_OWNER_RELEASED, foreground_status
from .read_adapter import INSIGHTS_OPERATIONS
from .validation import InputError, aware_time, fail, strict_json

LOCAL_SYNC_TTL = timedelta(minutes=5)


def _read_runner(root, intent, profile, clock, *, include_comment_resume=False):
    from .client_workspace import workspace_identity
    plans = validate_monitor_intent(intent)
    monitor = ReadMonitor(root,mode='READ_ONLY',clock=lambda: clock().isoformat(),existing_only=True)
    status = foreground_status(monitor,include_comment_resume=include_comment_resume)
    lock_observation = status.get('owner_lock_observation')
    if lock_observation not in ('HELD','RELEASED'):
        fail('MONITOR_SCOPE_UNVERIFIED')
    descriptors = {key: row['plan'] for key, row in status.get('sources', {}).items()}
    if (status.get('workspace_identity_sha256') != workspace_identity(profile)
            or descriptors != {key: asdict(plan) for key, plan in plans.items()}):
        fail('MONITOR_SCOPE_UNVERIFIED')
    return status, lock_observation=='HELD'


def _read_scope_file(run, name, limit, *, optional=False):
    path = checked_path(run/name,link_error='MONITOR_STATE_SYMLINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    try:
        stat = path.stat()
    except FileNotFoundError:
        if optional:
            return None, None
        raise
    if not S_ISREG(stat.st_mode):
        fail('MONITOR_RECORDED_SCOPE_INVALID')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        fail('MONITOR_RECORDED_SCOPE_INVALID')
    return strict_json(data.decode('utf-8')), dict(path=name,bytes=len(data),sha256=sha256(data).hexdigest())


def _scope_receipt_matches(receipt, evidence):
    if (type(receipt) is not dict or receipt.get('authorization_reusable') is not False
            or receipt.get('status') not in ('MONITOR_RUN_ENDED','MONITOR_REVIEW_REQUIRED','MONITOR_RUN_FAILED','INTERRUPTED')
            or type(receipt.get('files')) is not list or len(receipt['files'])>64):
        return False
    for expected in evidence:
        entries = [entry for entry in receipt['files'] if type(entry) is dict and entry.get('path')==expected['path']]
        if entries != [expected]:
            return False
    return True


def verify_recorded_intent(root, intent, *, run_id=None, comment_resume=None):
    """Verify the confirmation pair belonging to the observed foreground run."""
    error = 'WORKSPACE_PREVIOUS_INTENT_UNCONFIRMED'
    runs = checked_path(root/'runs',link_error='MONITOR_STATE_SYMLINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if comment_resume is not None:
        if run_id is not None and run_id != comment_resume['run_id']:
            fail(error)
        run_id = comment_resume['run_id']
    try:
        if run_id is None:
            # Older account snapshots omitted the run ID. Multiple attempts are ambiguous.
            legacy_runs = (path.name for path in runs.iterdir() if re.fullmatch('[a-f0-9]{32}',path.name))
            run_id = next(legacy_runs,None)
            if next(legacy_runs,None) is not None:
                fail(error)
        if type(run_id) is not str or not re.fullmatch('[a-f0-9]{32}',run_id):
            fail(error)
        run = runs/run_id
        plan, plan_evidence = _read_scope_file(run,'PLAN.json',128000)
        consent, consent_evidence = _read_scope_file(run,'CONSENT.json',4000)
        recorded_monitor_intent(plan,consent)
        if plan['intent_sha256']!=digest(intent):
            fail(error)
        if comment_resume is not None and not (
                aware_time(consent['approved_at']) <= aware_time(comment_resume['observed_at'])
                <= aware_time(consent['run_deadline'])):
            fail(error)
        receipt, receipt_evidence = _read_scope_file(run,'RECEIPT.json',64000,optional=True)
        if receipt_evidence is None:
            return dict(run_id=run_id,basis='CONFIRMED_PLAN_NO_COMPLETION_RECEIPT')
        if _scope_receipt_matches(receipt,(plan_evidence,consent_evidence)):
            return dict(run_id=run_id,basis='CONFIRMED_PLAN_RECEIPT_MATCHED')
    except (OSError, ValueError, RecursionError):
        fail(error)
    fail(error)


def _recent(row, now):
    stamp = row.get('last_successful_sync')
    return bool(stamp and row['result'] in ('COMPLETE','EMPTY') and not row['last_error']
                and timedelta(0) <= now - aware_time(stamp) < LOCAL_SYNC_TTL)


def scoped_page_live_observation(directory, identifier, profile, clock, *, current_access):
    from .client_workspace import page_live_scope
    unknown = dict(scope_state='UNVERIFIED', reason='MONITOR_SCOPE_UNVERIFIED',
                   monitor_process_verified=False, observation_recency='UNKNOWN', platform_data_as_of=None)
    try:
        root = checked_path(directory, link_error='MONITOR_PATH_LINK', volume_error='MONITOR_VOLUME')
        intent = load_monitor_intent(root/'runner-intent.json')
        if intent['schema'] != PAGE_LIVE_SCHEMA or intent['page_id'] != identifier:
            return unknown
        scope = page_live_scope(profile,intent['connection'])
        state, lock_held = _read_runner(root,intent,profile,clock)
        now = clock()
        diagnostic = {}
        scope_matches = intent['workspace_scope_sha256']==scope
        current_binding = page_live_scope(profile,intent['connection'],include_check=False)
        current_status = profile['checks'].get('PAGE:'+identifier,{}).get('status')
        for kind in PAGE_RUN_FAILURES:
            field = 'page_'+kind
            failure = state.get(field)
            if failure is None or failure['binding_sha256']!=current_binding:
                continue
            update = failure['workspace_health_update']
            if scope_matches or update in ('AUTH_REQUIRED','PERMISSION_REQUIRED') and update==current_status:
                diagnostic[field] = {key:value for key,value in failure.items() if key!='binding_sha256'}
        if not scope_matches:
            return dict(unknown,scope_state='CHANGED',reason='MONITOR_SCOPE_CHANGED',**diagnostic)
        phase = state['phase']
        runner = dict(unknown, scope_state='MATCHED', runner_phase=phase, runner_saved_phase=state['saved_phase'],
                      runner_error=state['last_error'], owner_lock_held=lock_held,
                      monitor_process_verified=lock_held and phase == 'RUNNING')
        if not current_access:
            return dict(runner, reason='PAGE_ACCESS_RECHECK_REQUIRED', observation_recency='HISTORICAL_ONLY', **diagnostic)
        live = state['sources']['live']
        observed = complete_row(state['sources'],'live')
        history = state.get('live_cycle_history',[])
        reason = 'RUNNER_NOT_RUNNING' if state['last_error']==FOREGROUND_OWNER_RELEASED else None
        purpose = {'execution_purpose':state['execution_purpose']} if 'execution_purpose' in state else {}
        coverage = state.get('page_live_cycle_coverage')
        sources = _source_observations(state,coverage)
        for name in METRIC_CHANNELS.keys() & sources.keys():
            rows = state['sources'][name]['rows'] if sources[name]['cycle_evidence']=='SAMPLED' else []
            sources[name]['metric_response_states'] = dict(sorted(Counter(row['state'] for row in rows).items()))
        diagnostic['source_observations'] = sources
        if coverage is not None:
            diagnostic['page_live_cycle'] = {key:coverage[key] for key in ('state','cycle')}
        if 'insights_role_observation' in state:
            diagnostic['insights_role_state'] = state['insights_role_observation']['state']
        if 'rate_limit' in state:
            diagnostic['rate_limit_wait'] = dict(pending=state['rate_limit_pending'],basis=state['rate_limit']['basis'])
        return dict(runner,reason=reason,
                    live_id=live['plan']['object_id'],expected_video_id=intent['expected_live_video_id'],
                    observed_video_id=observed.get('video',{}).get('id'),live_status=observed.get('status'),
                    live_result=live['result'],live_error=live['last_error'],observed_at=live['observed_at'],
                    last_successful_sync=live['last_successful_sync'],
                    observation_recency='RECENT_LOCAL_SYNC' if _recent(live,now) else 'STALE_OR_UNAVAILABLE',
                    entered_cycles=state['cycle_count'],latest_completed_cycle=history[-1]['cycle'] if history else None,
                    **diagnostic,**purpose)
    except (InputError,OSError,UnicodeError):
        return unknown


def _source_observations(status, coverage):
    sampled = set(coverage['sampled_sources']) if coverage is not None else None
    pending = (coverage or {}).get('uncommitted_request',{}).get('source')
    return {
        name: dict(result=row['result'],valid_pages=row['pages'],error_code=row['last_error'],
                   cycle_evidence='UNRECORDED' if sampled is None else 'SAMPLED' if name in sampled else 'UNSAMPLED',
                   request_uncommitted=name==pending,success_previously_recorded=row['last_successful_sync'] is not None)
        for name,row in sorted(status['sources'].items())
    }


def scoped_observation(directory, identifier, profile, task, clock, *, requested_query=None):
    from .client_workspace import monitor_scope
    unknown = dict(status='DEGRADED', reason='MONITOR_SCOPE_UNVERIFIED',
                   monitor_process_verified=False, data_freshness='UNKNOWN',
                   platform_data_as_of=None, last_successful_sync=None)
    try:
        root = checked_path(directory, link_error='MONITOR_PATH_LINK', volume_error='MONITOR_VOLUME')
        intent = load_monitor_intent(root/'runner-intent.json')
        conn = intent['connection']
        binding = profile['binding']
        if (intent['schema'] != 'workspace-monitor-intent-v1' or conn['account_id'] != identifier
                or conn['app_id'] != profile['app_id']
                or conn['principal_id'] != binding['principal_id']
                or conn['credential_reference'] != binding['secret_ref']
                or conn['credential_source'] != 'OS_REFERENCE'):
            return unknown
        status, lock_held = _read_runner(root,intent,profile,clock)
        now = clock()
        if intent['workspace_scope_sha256'] != monitor_scope(profile, conn, lambda: now):
            return dict(unknown,reason='MONITOR_SCOPE_CHANGED')
        rows = status['sources']
        if task=='ads-inventory':
            if set(rows)!=AD_INVENTORY_CHANNELS:
                return unknown
        elif task=='ads-placements':
            if set(rows)!=AD_PLACEMENT_CHANNELS:
                return unknown
        elif set(rows) not in AD_INSIGHTS_CHANNEL_SETS or rows['ads']['plan']['operation'] not in INSIGHTS_OPERATIONS:
            return unknown
        is_running = lock_held and status['phase']=='RUNNING'
        base = dict(unknown,runner_phase=status['phase'],runner_saved_phase=status['saved_phase'],runner_error=status['last_error'],
                    owner_lock_held=lock_held,monitor_process_verified=is_running,
                    account_cycle_pagination=status.get('account_cycle_pagination','UNRECORDED'),
                    source_observations=_source_observations(status,status.get('account_cycle_coverage')))
        if 'rate_limit' in status:
            base['rate_limit_wait'] = dict(pending=status['rate_limit_pending'],basis=status['rate_limit']['basis'])
        if task=='ads-inventory':
            stamps = [row['last_successful_sync'] for row in rows.values()]
            stamp = min(stamps,key=aware_time) if all(stamps) else None
            recent = all(_recent(row,now) for row in rows.values())
            return dict(base,status='HEALTHY' if recent else 'DEGRADED',
                        reason='RECENT_LOCAL_INVENTORY' if recent else 'CHECK_INVENTORY_OBSERVATION',
                        last_successful_sync=stamp,
                        data_freshness='RECENT_LOCAL_SYNC' if recent else 'STALE_OR_UNAVAILABLE')
        source = 'placements' if task == 'ads-placements' else 'ads'
        row = rows[source]
        stamp = row.get('last_successful_sync')
        recent = _recent(row,now)
        query = intent['sources'].get(source,{})
        info = dict(base, sync_query_since=query.get('since'), sync_query_until=query.get('until'),
                    sync_query_operation=query.get('operation'), reason='CHECK_INSIGHTS_OBSERVATION',
                    last_successful_sync=stamp,
                    data_freshness='RECENT_LOCAL_SYNC' if recent else 'STALE_OR_UNAVAILABLE')
        if requested_query is not None:
            if any(query.get(key) != value for key,value in requested_query.items()):
                return dict(info,query_scope_match='MISMATCH',reason='MONITOR_REPORT_QUERY_MISMATCH',
                            monitor_process_verified=False,last_successful_sync=None,
                            other_query_last_successful_sync=stamp,data_freshness='NOT_MEASURED')
            info['query_scope_match'] = 'MATCHED'
        if task == 'ads-placements':
            from .review import placement_analysis
            unverified = account_cycle_pagination_unverified(status)
            analysis = placement_analysis(rows,pagination_unverified=unverified)
            verified = recent and analysis['coverage'] == 'BOUNDED_COMPLETE'
            info.update(status='HEALTHY' if verified else 'DEGRADED',
                        reason='RECENT_LOCAL_PLACEMENTS' if verified else 'CHECK_PLACEMENT_OBSERVATION',
                        placement_coverage=analysis['coverage'],placement_error=analysis['reason'])
            if unverified:
                info.update(analysis_blocker=ACCOUNT_CYCLE_PAGINATION_UNVERIFIED,data_freshness='STALE_OR_UNAVAILABLE')
        elif account_cycle_pagination_unverified(status):
            info.update(reason='CHECK_INSIGHTS_ANALYSIS_CONTEXT',
                        analysis_blocker=ACCOUNT_CYCLE_PAGINATION_UNVERIFIED,
                        data_freshness='STALE_OR_UNAVAILABLE')
        elif task=='ads-monitor' and not is_running:
            info['reason']='RUNNER_NOT_RUNNING'
        elif recent:
            from .review import ACCOUNT_DAILY_ROWS_INCOMPLETE, ads_analysis
            analysis = ads_analysis(rows)
            if analysis['state']=='COMPUTED':
                coverage = analysis['daily_date_coverage']
                info['daily_date_coverage'] = coverage
                if coverage['state']=='MISSING_DAILY_ROWS':
                    info.update(reason=ACCOUNT_DAILY_ROWS_INCOMPLETE,analysis_blocker=ACCOUNT_DAILY_ROWS_INCOMPLETE)
                else:
                    info.update(status='HEALTHY',reason='RECENT_LOCAL_SYNC')
            else:
                info.update(reason='CHECK_INSIGHTS_ANALYSIS_CONTEXT',analysis_blocker=analysis['reason'])
        return info
    except (InputError,OSError,UnicodeError):
        return unknown
