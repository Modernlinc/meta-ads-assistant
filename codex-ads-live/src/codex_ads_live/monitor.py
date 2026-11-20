"""Bounded foreground monitoring through the existing read contract.

Transport/broker instances stay in memory. State holds normalized observations,
never credentials or opaque cursors. Real transports remain subject to their
own scope/expiry/broker gates; no CLI in this module enables real connections.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import time
import uuid

from .http_read import ACCOUNT_SCOPE_ERRORS, PAGE_METRICS_BLOCKERS, PUBLIC_ERRORS
from .credential_record import ERRORS as RECORD_ERRORS
from .journal import Journal, canonical
from .live_contract import LIVE_CONTRACT_PROBE
from .live_timeline import (COMMENT_CHECKPOINT, HISTORY_LIMIT, comment_checkpoint, previous_comment_ids,
                            resume_comment_checkpoint, summarize_cycle, validate_comment_checkpoint, validate_history)
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS, METRIC_PLAN_TYPES, MetricReadPlan, PostMetricReadPlan, MetricReadSession, validate_observation
from .page_live_credentials import (DEBUG_DEVELOPER, DEBUG_PAGE, PAGE_ACTOR, PAGE_COLLECTOR_IDENTITY_MISMATCH,
                                    PAGE_DEVELOPER_REJECTION_ERRORS, PAGE_IDENTITY_STAGES, PAGE_ROLE_STAGES,
                                    ROLE_OBSERVATION_BASIS, ROLE_PAGES, ROLE_VERIFIED,
                                    page_authentication_issue, page_permission_issue)
from .rate_limit import preserve_remaining_wait, resume_time, validate_hold
from .read_adapter import (GRAPH_AUTH_ERRORS, INSIGHTS_OPERATIONS, LIVE_END_STATES, PLACEMENT_INSIGHTS,
                           RECENT_LIVE_COMMENTS, PageLiveReadPlan, ReadPlan, ReadSession, normalize_row,
                           positive_integer, row_identity)
from .validation import InputError, aware_time, fail, finite_number, object_only

AD_OBJECT_SOURCES = {'campaigns': 'campaigns', 'adsets': 'adsets', 'ad_inventory': 'ads'}
AD_INVENTORY_CHANNELS = {'account', *AD_OBJECT_SOURCES}
AD_PLACEMENT_CHANNELS = {'account', 'placements'}
AD_INSIGHTS_CHANNEL_SETS = ({'account','ads'},{'account','ads','ad_links'})
MAX_PAGES_PER_SOURCE = 20
NAMED_ACCOUNT_PERMISSION_DENIED = 'NAMED_ACCOUNT_PERMISSION_DENIED'
CYCLE_SOURCE_FIELDS = ('account_cycle_sources', 'page_live_cycle_sources')
ACCOUNT_CYCLE_PAGINATION = 'FIRST_PAGE_EACH_CYCLE'
ACCOUNT_CYCLE_PAGINATION_UNVERIFIED = 'ACCOUNT_CYCLE_PAGINATION_UNVERIFIED'
FOREGROUND_RESUME_RECOVERY = 'PREVIOUS_RUNNING_STATE_OWNER_LOCK_ACQUIRED'
PAGE_RUN_FAILURES = ('authentication','permission')


def account_cycle_basis(status):
    coverage = status.get('account_cycle_coverage')
    if coverage is not None:
        return dict(cycle=coverage['cycle'],pagination=status.get('account_cycle_pagination') or 'UNRECORDED')
    sources = status.get('sources',{})
    has_insights = ('placements' in sources or
                    sources.get('ads',{}).get('plan',{}).get('operation') in INSIGHTS_OPERATIONS)
    if status.get('mode')=='READ_ONLY' and status.get('cycle_count',0)>1 and has_insights:
        return dict(cycle=status['cycle_count'],pagination='UNRECORDED')
    return None


def account_cycle_pagination_unverified(status):
    basis = account_cycle_basis(status)
    return basis is not None and basis['cycle']>1 and basis['pagination']!=ACCOUNT_CYCLE_PAGINATION

CHANNELS = {'ads': {'account', 'campaigns', 'adsets', 'ads', *INSIGHTS_OPERATIONS},
            'live': {'live'}, 'comments': {'comments', RECENT_LIVE_COMMENTS}, 'live_post': {'live_post'},
            'account': {'account'}, 'ad_links': {'ad_videos'}, 'placements': {PLACEMENT_INSIGHTS},
            **{key: {operation} for key, operation in AD_OBJECT_SOURCES.items()}, 'page': {'page'}}
CHANNELS.update({key: {'video_insights','post_insights'} for key in METRIC_CHANNELS})
SAFE_ERRORS = PUBLIC_ERRORS | PAGE_METRICS_BLOCKERS | {
    *GRAPH_AUTH_ERRORS, 'PERMISSION', 'RATE_LIMIT', 'OBJECT_OR_PARAMETER', 'SERVICE_UNAVAILABLE',
    'UNCLASSIFIED_ERROR', 'EXPLICIT_CONNECTION_REVIEW_REQUIRED', 'CREDENTIAL_OR_TRANSPORT_FAILURE',
    'RETURNED_ACCOUNT_MISMATCH', 'RETURNED_OBJECT_MISMATCH', 'LIVE_OWNER_UNVERIFIED',
    'INVALID_RESPONSE_ENVELOPE', 'DATA_ARRAY_REQUIRED', 'RESPONSE_EXCEEDS_PAGE_BUDGET',
    'CURSOR_LOOP', 'DUPLICATE_ROW_CONFLICT', 'NEXT_WITHOUT_CURSOR', 'INVALID_PAGING',
    'INVALID_ROW', 'INVALID_RELATION', 'INVALID_NUMERIC_FIELD', 'INVALID_COUNT',
    'INVALID_CREATIVE_RELATION', 'INVALID_CURRENCY', 'INSIGHTS_ACTION_STATS_INVALID', 'INSIGHTS_ACTION_STATS_AMBIGUOUS',
    'UNRECOGNIZED_STATUS', 'ROW_ID_MISSING', 'INSIGHTS_GRAIN_MISSING', 'PLACEMENT_GRAIN_REQUIRED',
    'RETURNED_DATE_OUTSIDE_SCOPE', 'INVALID_DATE_FIELD', 'COMMENT_CREATION_TIME_MISSING', 'INVALID_TIMESTAMP',
    'MOCK_RESPONSES_EXHAUSTED', 'MONITOR_COLLECTOR_FAILURE', 'MONITOR_OBSERVATION_BUDGET',
    'METRIC_PAGINATION_UNSUPPORTED', 'METRIC_BUCKET_AMBIGUOUS', 'METRIC_ENVELOPE_AMBIGUOUS',
    'METRIC_BUCKET_BUDGET', 'VALUE_PERIOD_UNVERIFIED', 'INVALID_STRUCTURED_METRIC',
    'METRIC_VIDEO_RELATION_MISMATCH', 'METRIC_LIVE_RELATION_UNVERIFIED',
    'LIVE_VIDEO_RELATION_UNVERIFIED', 'LIVE_VIDEO_RELATION_MISMATCH',
    'LIVE_POST_OWNER_UNVERIFIED','LIVE_POST_VIDEO_UNVERIFIED','INVALID_PAGE_POST_ID','PAGE_POST_SCOPE_MISMATCH',
    'METRIC_POST_RELATION_UNVERIFIED','COMMENTS_LIVE_RELATION_UNVERIFIED',
}
CONTROL_ERRORS = {None, 'CLOCK_ROLLBACK', 'STALE_HEARTBEAT', 'WORKER_INTERRUPTED',
                  'REQUEST_BUDGET_EXHAUSTED', 'SOURCES_REQUIRE_REVIEW', 'SOURCES_ENDED', 'RECOVERY_REQUIRED',
                  'CONNECTION_PREFLIGHT_FAILED', 'MONITOR_SESSION_EXPIRED', 'CREDENTIAL_REFERENCE_REVIEW_REQUIRED',
                  *GRAPH_AUTH_ERRORS, 'CONNECTION_EXPIRED_OR_CLOCK_SKEW', 'MONITOR_RUN_FAILURE', 'RATE_LIMIT',
                  'RETURNED_ACCOUNT_MISMATCH', NAMED_ACCOUNT_PERMISSION_DENIED, *ACCOUNT_SCOPE_ERRORS, *PAGE_IDENTITY_STAGES}


def now(): return datetime.now(timezone.utc).isoformat()


def exact(value, keys): object_only(value, keys, keys)


def source_state(plan):
    return dict(plan=asdict(plan), phase='READY', result='NOT_RUN', generation=0,
                pages=0, rows=[], observed_at=None, last_successful_sync=None, last_error=None)


def source_plan(channel, data):
    kind = ReadPlan
    if channel in METRIC_CHANNELS:
        kind = PostMetricReadPlan if type(data) is dict and data.get('operation')=='post_insights' else MetricReadPlan
    elif channel == 'live' and type(data) is dict and 'expected_live_video_id' in data:
        kind = PageLiveReadPlan
    exact(data, kind.__dataclass_fields__)
    plan = kind(**data)
    if plan.operation not in CHANNELS[channel]: fail('MONITOR_CHANNEL_OPERATION')
    if channel in METRIC_CHANNELS and plan.metric != METRIC_CHANNELS[channel]: fail('MONITOR_METRIC_CHANNEL')
    return plan


def validate_relations(sources, *, strict=False):
    if 'placements' in sources and (set(sources)!=AD_PLACEMENT_CHANNELS
            or sources['placements']['plan']['account_id']!=sources['account']['plan']['account_id']):
        fail('MONITOR_PLACEMENT_ACCOUNT_SCOPE')
    live = sources.get('live',{}).get('plan',{})
    if strict and 'comments' in sources:
        plan = sources['comments']['plan']
        if (plan['page_id'],plan['object_id'])!=(live.get('page_id'),live.get('object_id')):
            fail('MONITOR_COMMENTS_REQUIRE_MATCHING_LIVE_SOURCE')
    if 'live_post' in sources and not (live and sources['live_post']['plan']['page_id']==live.get('page_id')):
        fail('MONITOR_POST_REQUIRES_MATCHING_LIVE_SOURCE')
    for channel, row in sources.items():
        if channel not in METRIC_CHANNELS: continue
        live = sources.get('live', {}).get('plan', {})
        if row['plan']['live_id'] != live.get('object_id') or row['plan']['page_id'] != live.get('page_id'):
            fail('MONITOR_METRICS_REQUIRE_MATCHING_LIVE_SOURCE')
        expected_video = live.get('expected_live_video_id')
        metric_video = row['plan'].get('video_id', row['plan']['object_id'])
        if expected_video is not None and metric_video != expected_video:
            fail('MONITOR_METRICS_REQUIRE_MATCHING_VIDEO_SOURCE')
        if row['plan']['operation']=='post_insights':
            post = sources.get('live_post', {}).get('plan', {})
            if row['plan']['object_id']!=post.get('object_id') or row['plan']['page_id']!=post.get('page_id'):
                fail('MONITOR_METRICS_REQUIRE_MATCHING_POST_SOURCE')


def complete_row(sources, channel):
    source = sources.get(channel)
    if source and source['result']=='COMPLETE' and len(source['rows'])==1:
        return source['rows'][0]
    return {}


def dependency_error(sources, channel, plan):
    if channel not in {'comments','live_post',*METRIC_CHANNELS}:
        return None
    live = complete_row(sources,'live')
    if channel=='comments':
        if (live.get('id'),live.get('from',{}).get('id')) != (plan['object_id'],plan['page_id']):
            return 'COMMENTS_LIVE_RELATION_UNVERIFIED'
    elif channel=='live_post':
        if live.get('from',{}).get('id')!=plan['page_id'] or not live.get('video',{}).get('id'):
            return 'LIVE_POST_VIDEO_UNVERIFIED'
    else:
        if not live:
            return 'METRIC_LIVE_RELATION_UNVERIFIED'
        video_id = live.get('video',{}).get('id')
        if plan['operation']=='video_insights':
            if video_id!=plan['object_id']:
                return 'METRIC_VIDEO_RELATION_MISMATCH'
        else:
            if video_id!=plan['video_id']:
                return 'METRIC_VIDEO_RELATION_MISMATCH'
            post = complete_row(sources,'live_post')
            if not post or post['id']!=plan['object_id']:
                return 'METRIC_POST_RELATION_UNVERIFIED'
            if post['attachments']['data'][0]['target']['id']!=video_id:
                return 'METRIC_POST_RELATION_UNVERIFIED'
    return None


def read_session(pair, clock):
    return MetricReadSession(*pair, clock=clock) if type(pair[0]) in METRIC_PLAN_TYPES else ReadSession(*pair)


def validate_source(channel, row):
    exact(row, {'plan', 'phase', 'result', 'generation', 'pages', 'rows', 'observed_at', 'last_successful_sync', 'last_error'})
    plan = source_plan(channel, row['plan'])
    if row['phase'] not in ('READY', 'PAUSED', 'ENDED') or row['result'] not in ('NOT_RUN', 'GAP', 'COMPLETE', 'EMPTY', 'ERROR'):
        fail('MONITOR_SOURCE_STATE')
    if row['last_error'] is not None and row['last_error'] not in SAFE_ERRORS: fail('MONITOR_ERROR_CODE')
    for key, limit in (('generation', 1000000), ('pages', 1000)):
        if type(row[key]) is not int or not 0 <= row[key] <= limit: fail('MONITOR_SOURCE_COUNT')
    for stamp in (row['observed_at'], row['last_successful_sync']):
        if stamp is not None: aware_time(stamp)
    if type(row['rows']) is not list or len(row['rows']) > 1000: fail('MONITOR_OBSERVATION_BUDGET')
    seen = set()
    for item in row['rows']:
        if channel in METRIC_CHANNELS:
            validate_observation(plan, item)
            key = (item['period'], aware_time(item['bucket_end_time']) if item['bucket_end_time'] else None)
        else:
            if normalize_row(plan, item) != item: fail('MONITOR_UNSANITIZED_ROW')
            key = row_identity(plan,item)
        if key in seen: fail('MONITOR_DUPLICATE_ROW')
        seen.add(key)
    if row['result'] == 'EMPTY' and row['rows']: fail('MONITOR_EMPTY_CONFLICT')
    if row['result'] == 'COMPLETE' and not row['rows']: fail('MONITOR_COMPLETE_CONFLICT')


def validate_page_run(state):
    run = state['page_run']
    object_only(run,('id',*PAGE_RUN_FAILURES),('id',))
    live = state['sources'].get('live',{}).get('plan',{})
    page = state['sources'].get('page',{}).get('plan',{})
    if (state['mode']!='READ_ONLY' or 'workspace_identity_sha256' not in state
            or not live.get('page_id') or live['page_id']!=page.get('object_id')
            or type(run['id']) is not str or not re.fullmatch('[a-f0-9]{32}',run['id'])):
        fail('MONITOR_PAGE_RUN')
    if len(run)==1:
        return
    if len(run)!=2 or state['worker'] is not None:
        fail('MONITOR_PAGE_RUN')
    kind, = set(run)-{'id'}
    failure = run[kind]
    exact(failure,{'error_code','issue','workspace_health_update','binding_sha256','result_sha256','receipt_sha256'})
    if kind=='authentication':
        validate_page_authentication(failure)
    else:
        validate_page_permission(failure)
    for name in ('binding_sha256','result_sha256','receipt_sha256'):
        if type(failure[name]) is not str or not re.fullmatch('[a-f0-9]{64}',failure[name]):
            fail('MONITOR_PAGE_AUTHENTICATION' if kind=='authentication' else 'MONITOR_PAGE_PERMISSION')


def validate_page_authentication(auth):
    issue = auth['issue']
    exact(issue,{'credential','stage','next_action'})
    stage = issue['stage']
    if (stage not in (DEBUG_DEVELOPER,DEBUG_PAGE,PAGE_ACTOR,*PAGE_ROLE_STAGES,'PAGE_COLLECTOR','LOCAL_CREDENTIAL_READ')
            or stage=='LOCAL_CREDENTIAL_READ' and auth['error_code']!='NATIVE_CREDENTIAL_UNAVAILABLE'
            or stage=='PAGE_COLLECTOR' and auth['error_code'] not in (*GRAPH_AUTH_ERRORS,'CONNECTION_EXPIRED_OR_CLOCK_SKEW',
                                                                    PAGE_COLLECTOR_IDENTITY_MISMATCH)
            or issue!=page_authentication_issue(auth['error_code'],stage,stage=='PAGE_COLLECTOR',issue['credential']=='PAGE_TOKEN')
            or auth['workspace_health_update'] not in ('NOT_APPLICABLE','AUTH_REQUIRED','CURRENT_WORKSPACE_UPDATE_UNVERIFIED')):
        fail('MONITOR_PAGE_AUTHENTICATION')
    if auth['workspace_health_update']!='NOT_APPLICABLE' and (issue['credential']!='DEVELOPER_USER'
            or auth['error_code'] not in PAGE_DEVELOPER_REJECTION_ERRORS):
        fail('MONITOR_PAGE_AUTHENTICATION')


def validate_page_permission(failure):
    issue = failure['issue']
    exact(issue,{'credential','stage'})
    if issue!=page_permission_issue(failure['error_code'],issue['stage']):
        fail('MONITOR_PAGE_PERMISSION')
    outcomes = ('NOT_APPLICABLE',) if issue['credential']=='PAGE_TOKEN' else (
        'PERMISSION_REQUIRED','CURRENT_WORKSPACE_UPDATE_UNVERIFIED')
    if failure['workspace_health_update'] not in outcomes:
        fail('MONITOR_PAGE_PERMISSION')


def page_failure_view(directory, run, kind):
    failure = run[kind]
    issue = failure['issue']
    if kind=='permission':
        issue = dict(issue,next_action=('RECHECK_NAMED_PAGE_GRANT' if issue['credential']=='DEVELOPER_USER'
                                       else 'RECHECK_NAMED_PAGE_TOKEN_GRANT'))
    paths = {name:f"runs/{run['id']}/{name.upper()}.json" for name in ('result','receipt')}
    view = dict(error_code=failure['error_code'],issue=issue,workspace_health_update=failure['workspace_health_update'],
                binding_sha256=failure['binding_sha256'],evidence_state='MISSING_OR_CHANGED')
    try:
        for name, max_bytes in (('result',8_000_000),('receipt',32_000)):
            path = checked_path(directory/paths[name],link_error='MONITOR_STATE_SYMLINK',volume_error='MONITOR_VOLUME')
            if not path.is_file() or path.stat().st_size>max_bytes:
                return view
            with path.open('rb') as stream:
                data = stream.read(max_bytes+1)
            if len(data)>max_bytes or hashlib.sha256(data).hexdigest()!=failure[name+'_sha256']:
                return view
    except (InputError,OSError):
        return view
    return dict(view,evidence_state='LOCAL_DIGEST_MATCH',evidence_paths=paths)


def validate(state):
    required = {'schema', 'mode', 'phase', 'worker', 'last_heartbeat', 'last_error',
                'interval_seconds', 'request_budget', 'requests_reserved', 'cycle_count', 'sources'}
    object_only(state,required | {'rate_limit','foreground_claim_id','insights_role_observation','workspace_identity_sha256','page_run','account_run_id','resume_recovery',
                                  'live_cycle_history', 'account_cycle_pagination','uncommitted_request',
                                  COMMENT_CHECKPOINT, 'execution_purpose', *CYCLE_SOURCE_FIELDS},required)
    if state['schema'] != 'read-monitor-v1' or state['mode'] not in ('MOCK', 'READ_ONLY'): fail('MONITOR_SCHEMA')
    if 'resume_recovery' in state and (state['mode']!='READ_ONLY' or state['resume_recovery']!=FOREGROUND_RESUME_RECOVERY):
        fail('MONITOR_RESUME_RECOVERY')
    if 'workspace_identity_sha256' in state:
        identity = state['workspace_identity_sha256']
        if state['mode']!='READ_ONLY' or type(identity) is not str or not re.fullmatch('[0-9a-f]{64}',identity):
            fail('MONITOR_WORKSPACE_IDENTITY')
    if state['phase'] not in ('RUNNING', 'PAUSED', 'STOPPED', 'INTERRUPTED') or state['last_error'] not in CONTROL_ERRORS:
        fail('MONITOR_STATE')
    if type(state['worker']) is not str and state['worker'] is not None: fail('MONITOR_WORKER')
    if state['worker'] is not None and not re.fullmatch('[a-f0-9]{32}', state['worker']): fail('MONITOR_WORKER')
    if state['phase'] == 'RUNNING' and state['worker'] is None: fail('MONITOR_WORKER_REQUIRED')
    if 'foreground_claim_id' in state:
        claim = state['foreground_claim_id']
        if (state['mode']!='READ_ONLY' or type(claim) is not str or not re.fullmatch('[a-f0-9]{32}',claim)
                or state['worker'] not in (None,claim)):
            fail('MONITOR_FOREGROUND_CLAIM_ID')
    aware_time(state['last_heartbeat'])
    minimum = .05 if state['mode'] == 'MOCK' else 1
    if not finite_number(state['interval_seconds']) or not minimum <= state['interval_seconds'] <= 3600: fail('MONITOR_INTERVAL')
    positive_integer(state['request_budget'], 1000)
    for key in ('requests_reserved', 'cycle_count'):
        if type(state[key]) is not int or not 0 <= state[key] <= 1000000: fail('MONITOR_COUNT')
    if state['requests_reserved'] > state['request_budget']: fail('MONITOR_REQUEST_BUDGET')
    if 'rate_limit' in state:
        if state['mode'] != 'READ_ONLY': fail('MONITOR_RATE_LIMIT_STATE')
        validate_hold(state['rate_limit'],state['requests_reserved'],'MONITOR_RATE_LIMIT_STATE')
    if type(state['sources']) is not dict or not 1 <= len(state['sources']) <= len(CHANNELS) or not set(state['sources']) <= set(CHANNELS):
        fail('MONITOR_SOURCES')
    for field in CYCLE_SOURCE_FIELDS:
        if field not in state: continue
        sampled = state[field]
        account = field == 'account_cycle_sources'
        if account:
            sources = set(state['sources'])
            scope_matches = state['mode']=='READ_ONLY' and 'account' in sources and (
                'workspace_identity_sha256' in state or sources in AD_INSIGHTS_CHANNEL_SETS)
        else:
            scope_matches = 'live_cycle_history' in state
        if (not scope_matches or type(sampled) is not list or len(sampled)>len(state['sources'])
                or any(type(channel) is not str or channel not in state['sources'] for channel in sampled)
                or sorted(set(sampled))!=sampled or sampled and not state['cycle_count']):
            fail('MONITOR_ACCOUNT_CYCLE_SOURCES' if account else 'MONITOR_PAGE_LIVE_CYCLE_SOURCES')
    if 'account_cycle_pagination' in state and ('account_cycle_sources' not in state
            or state['account_cycle_pagination'] != ACCOUNT_CYCLE_PAGINATION):
        fail('MONITOR_ACCOUNT_CYCLE_PAGINATION')
    for channel, row in state['sources'].items():
        validate_source(channel, row)
    if 'uncommitted_request' in state:
        request = state['uncommitted_request']
        exact(request, {'source','generation','earlier_cycle_page'})
        source, generation = request['source'], request['generation']
        fields = [field for field in CYCLE_SOURCE_FIELDS if field in state]
        if (not fields or not state['cycle_count'] or not state['requests_reserved']
                or type(source) is not str or source not in state['sources'] or type(generation) is not int
                or generation<=0 or state['sources'][source]['generation']!=generation
                or state['sources'][source]['phase']!='READY' or type(request['earlier_cycle_page']) is not bool
                or request['earlier_cycle_page'] and (state['cycle_count']==1 or any(source in state[field] for field in fields))):
            fail('MONITOR_UNCOMMITTED_REQUEST')
    role = state.get('insights_role_observation')
    if 'insights_role_observation' in state:
        exact(role,{'state','page_id','app_id','principal_id','get_attempts','basis','observed_at'})
        page_plan = state['sources'].get('page',{}).get('plan',{})
        if (state['mode']!='READ_ONLY' or role['page_id']!=page_plan.get('page_id')
                or not set(METRIC_CHANNELS) <= state['sources'].keys()
                or role['state'] not in PAGE_METRICS_BLOCKERS | {ROLE_VERIFIED}
                or role['basis']!=ROLE_OBSERVATION_BASIS or type(role['get_attempts']) is not int
                or not 0 <= role['get_attempts'] <= ROLE_PAGES or role['state']==ROLE_VERIFIED and role['get_attempts']==0
                or any(type(role[key]) is not str or not re.fullmatch(r'[0-9]{1,40}',role[key])
                       for key in ('page_id','app_id','principal_id'))):
            fail('MONITOR_PAGE_ROLE_OBSERVATION')
        aware_time(role['observed_at'])
    validate_relations(state['sources'])
    if 'account_run_id' in state:
        identifier = state['account_run_id']
        if ('foreground_claim_id' not in state or 'account' not in state['sources'] or 'page_run' in state
                or type(identifier) is not str or not re.fullmatch('[a-f0-9]{32}',identifier)):
            fail('MONITOR_ACCOUNT_RUN')
    if 'page_run' in state:
        validate_page_run(state)
    if 'execution_purpose' in state and (state['execution_purpose'] != LIVE_CONTRACT_PROBE
            or 'page_run' not in state or 'foreground_claim_id' not in state
            or state['cycle_count']>1 or state['request_budget']!=len(state['sources'])):
        fail('MONITOR_EXECUTION_PURPOSE')
    if 'live_cycle_history' in state:
        validate_history(state, SAFE_ERRORS)
    if COMMENT_CHECKPOINT in state:
        validate_comment_checkpoint(state)
    if len(canonical(state).encode('utf-8')) > 900000: fail('MONITOR_STATE_SIZE')


class ReadMonitor:
    def __init__(self, directory, mode='MOCK', clock=now, *, existing_only=False):
        self.journal = Journal(directory, mode=mode, existing_only=existing_only, stage_snapshots=True)
        self.mode = mode; self.clock = clock
        self.sessions = {}; self.sources = {}; self.worker = None
        self.resumed_comment_checkpoint = None

    def _load(self):
        state, damaged = self.journal.load(validate)
        if damaged: fail('MONITOR_RECOVERY_REVIEW_REQUIRED')
        if state is not None and state['mode'] != self.mode: fail('MONITOR_MODE_CHANGED')
        return state

    def _observed(self, state, *, observed_at=None):
        state = deepcopy(state)
        if state['worker'] is not None and state['phase'] in ('RUNNING', 'PAUSED'):
            age = (aware_time(observed_at if observed_at is not None else self.clock())
                   - aware_time(state['last_heartbeat'])).total_seconds()
            if age < 0: state.update(phase='INTERRUPTED', last_error='CLOCK_ROLLBACK')
            elif age > max(60, 3 * state['interval_seconds']):
                state.update(phase='INTERRUPTED', last_error='STALE_HEARTBEAT')
        return state

    def foreground_owner_probe(self):
        root = self.journal.directory
        saved_intent, owner_directory = root/'runner-intent.json', root/'runner-owner'
        if self.mode != 'READ_ONLY' or not (os.path.lexists(saved_intent) or os.path.lexists(owner_directory)):
            return None

        def observe_owner():
            if saved_intent.is_symlink() or not saved_intent.is_file():
                return 'UNVERIFIED'
            try:
                owner = Journal(owner_directory, mode='READ_ONLY', existing_only=True)
                with owner.locked(existing_only=True):
                    return 'RELEASED'
            except InputError as exc:
                return 'HELD' if str(exc) == 'STATE_BUSY' else 'UNVERIFIED'
            except (OSError, UnicodeError):
                return 'UNVERIFIED'

        return observe_owner

    def status(self, *, existing_only=False, include_saved_phase=False, foreground_probe=None,
               include_comment_resume=False):
        with self.journal.locked(wait_seconds=.5, existing_only=existing_only):
            state = self._load()
            pending = self.journal.unpublished_snapshot_count()
            unpublished = dict(unpublished_snapshot_count=pending) if pending else {}
            # A foreground runner publishes its final snapshot under this lock
            # before releasing ownership. Probe without waiting between publications.
            owner = dict(owner_lock_observation=foreground_probe()) if foreground_probe else {}
            if state is None:
                return dict(phase='NOT_CONFIGURED', mode=self.mode, real_connection_verified=False, **unpublished, **owner)
            result = self._observed(state)
            if include_comment_resume and owner.get('owner_lock_observation') == 'RELEASED':
                evidence = self._comment_resume_evidence(state)
                if evidence is not None:
                    result['comment_resume_evidence'] = evidence
        if include_saved_phase:
            result['saved_phase'] = state['phase']
        result.update(unpublished, **owner)
        return self._status_view(result)

    def review_snapshot(self, reference=None):
        """Read one published snapshot and project the phase at its recorded review time."""
        if reference is not None:
            fields = ('snapshot','payload_sha256','phase_observed_at')
            object_only(reference,fields,fields)
            name, digest = reference['snapshot'], reference['payload_sha256']
            if (type(name) is not str or not re.fullmatch(r'snapshot-[0-9]{10}\.json',name)
                    or type(digest) is not str or not re.fullmatch('[0-9a-f]{64}',digest)):
                fail('MONITOR_REVIEW_SNAPSHOT_REFERENCE')
            observed_at = reference['phase_observed_at']
            if aware_time(observed_at).isoformat()!=observed_at:
                fail('MONITOR_REVIEW_SNAPSHOT_REFERENCE')
            path = self.journal.directory/name
            state, checksum = self.journal.read_snapshot(path,validate)
            if checksum != digest: fail('MONITOR_REVIEW_SNAPSHOT_CHANGED')
        else:
            with self.journal.locked(wait_seconds=.5,existing_only=True):
                snapshots = self.journal.snapshots()
                if not snapshots: fail('MONITOR_REVIEW_SNAPSHOT_UNAVAILABLE')
                path = snapshots[-1]
                try:
                    state, checksum = self.journal.read_snapshot(path,validate)
                except (TypeError,ValueError,OverflowError):
                    fail('MONITOR_RECOVERY_REVIEW_REQUIRED')
                observed_at = aware_time(self.clock()).isoformat()
                reference = dict(snapshot=path.name,payload_sha256=checksum,phase_observed_at=observed_at)
        if state['mode'] != self.mode: fail('MONITOR_MODE_CHANGED')
        observed = self._observed(state,observed_at=observed_at)
        return self._status_view(observed,observed_at=observed_at), reference

    def _comment_resume_evidence(self, current):
        """Recover the timestamps from the published snapshot of the completed sample."""
        if 'execution_purpose' in current: return None
        checkpoint = resume_comment_checkpoint(current,self.clock())
        if checkpoint is None or not checkpoint['id_digests']: return None
        completed = current
        if current['cycle_count'] != checkpoint['cycle']:
            current_plans = {key: row['plan'] for key, row in current['sources'].items()}
            for path in reversed(self.journal.snapshots()[:-1]):
                candidate, _ = self.journal.read_snapshot(path,validate)
                if (candidate.get('page_run',{}).get('id') != checkpoint['run_id']
                        or candidate['cycle_count'] < checkpoint['cycle']):
                    return None
                if (candidate['cycle_count'] == checkpoint['cycle']
                        and candidate.get(COMMENT_CHECKPOINT) == current[COMMENT_CHECKPOINT]
                        and candidate.get('workspace_identity_sha256') == current.get('workspace_identity_sha256')
                        and {key: row['plan'] for key, row in candidate['sources'].items()} == current_plans):
                    completed = candidate
                    break
            else:
                return None
        comments = completed['sources']['comments']
        if any('created_time' not in row for row in comments['rows']): return None
        newest = max(aware_time(row['created_time']) for row in comments['rows'])
        if newest > aware_time(checkpoint['observed_at']): return None
        try:
            newest = newest.astimezone(timezone.utc)
            since = newest.replace(microsecond=0) - timedelta(seconds=1)
        except (ValueError, OverflowError):
            return None
        previous_since = comments['plan']['since']
        if previous_since is not None:
            since = max(since,aware_time(previous_since))
        return dict(run_id=checkpoint['run_id'],cycle=checkpoint['cycle'],observed_at=checkpoint['observed_at'],
                    newest_created_at=newest.isoformat().replace('+00:00','Z'),
                    since=since.isoformat().replace('+00:00','Z'),interruption_coverage='UNKNOWN')

    def previous_page_run_status(self, *, require_relation=False):
        """Read the previous lease, optionally locating its latest committed object relation."""
        with self.journal.locked(wait_seconds=.5, existing_only=True):
            current = self._load()
            if current is None or 'page_run' not in current:
                fail('MONITOR_PREVIOUS_PAGE_RUN_UNAVAILABLE')
            snapshots = self.journal.snapshots()
        expected = {key: row['plan'] for key, row in current['sources'].items()}
        selected = None
        for path in reversed(snapshots[:-1]):
            try:
                state, checksum = self.journal.read_snapshot(path, validate)
            except (OSError, ValueError, TypeError, OverflowError):
                fail('MONITOR_PAGE_HISTORY_REVIEW_REQUIRED')
            if 'page_run' not in state:
                break
            run_id = state['page_run']['id']
            if run_id == current['page_run']['id']:
                continue
            if selected is not None and run_id != selected:
                break
            selected = run_id
            if (state['workspace_identity_sha256'] != current['workspace_identity_sha256']
                    or {key: row['plan'] for key, row in state['sources'].items()} != expected):
                fail('MONITOR_PAGE_HISTORY_SCOPE_CHANGED')
            if require_relation:
                sources = state['sources']
                conflicts = ('RETURNED_OBJECT_MISMATCH','LIVE_OWNER_UNVERIFIED','LIVE_VIDEO_RELATION_MISMATCH')
                if any(sources[key]['last_error'] in conflicts for key in ('page','live')):
                    fail('WORKSPACE_LIVE_LINK_SAVED_RELATION_CONFLICT')
                sampled = state.get('page_live_cycle_sources',[])
                if (not {'page','live'} <= set(sampled) or not complete_row(sources,'page')
                        or not complete_row(sources,'live').get('video',{}).get('id')):
                    continue
            state['saved_phase'] = state['phase']
            if state['phase'] == 'RUNNING':
                state.update(phase='INTERRUPTED', last_error='FOREGROUND_RUN_SUPERSEDED')
            state['historical_page_run'] = dict(run_id=run_id, snapshot=path.name,
                                                 payload_sha256=checksum, completion_receipt_verified=False)
            return self._status_view(state)
        if selected is not None:
            fail('WORKSPACE_LIVE_LINK_SAVED_RELATION_UNAVAILABLE')
        fail('MONITOR_PREVIOUS_PAGE_RUN_UNAVAILABLE')

    def _status_view(self, result, *, observed_at=None):
        result.pop('worker')
        result.pop('foreground_claim_id', None)
        result.pop(COMMENT_CHECKPOINT, None)
        account_run_id = result.pop('account_run_id',None)
        if account_run_id is not None:
            result['confirmed_run_id'] = account_run_id
        run = result.pop('page_run',None)
        if run is not None:
            result['confirmed_run_id'] = run['id']
            for kind in PAGE_RUN_FAILURES:
                if kind in run:
                    result['page_'+kind] = page_failure_view(self.journal.directory,run,kind)
        for field in CYCLE_SOURCE_FIELDS:
            if field in result:
                self._latest_cycle(result, field)
        result.pop('uncommitted_request', None)
        if 'account_cycle_coverage' in result:
            result.setdefault('account_cycle_pagination','UNRECORDED')
        if 'rate_limit' in result:
            stamp = self.clock() if observed_at is None else observed_at
            result['rate_limit_pending'] = aware_time(stamp) < resume_time(result['rate_limit'])
        result.update(real_connection_verified=False, opaque_cursor_persisted=False,
                      health='RECENT_HEARTBEAT' if result['phase'] == 'RUNNING' else result['phase'],
                      restart_strategy='EXPLICIT_NEW_LEASE_REPLAYS_FROM_FIRST_PAGE',
                      request_count_semantics='RESERVED_ATTEMPTS_INCLUDES_FAILURES_NOT_PROOF_OF_NETWORK_TRANSMISSION',
                      last_successful_sync={key: result['sources'].get(key, {}).get('last_successful_sync') for key in CHANNELS})
        return result

    @staticmethod
    def _latest_cycle(status, field):
        """Present current observations and retain prior successes in the journal."""
        sampled = status.pop(field)
        rows = status['sources']
        missing = sorted(rows.keys() - set(sampled))
        page_live = field == 'page_live_cycle_sources'
        # Recent comments deliberately sample a bounded page, even if a next page exists.
        partial = [key for key in sampled if rows[key]['result'] not in ('COMPLETE','EMPTY')
                   and not (page_live and key=='comments' and rows[key]['result']=='GAP' and rows[key]['pages'])]
        request = status.get('uncommitted_request',{})
        previous = [key for key in missing if rows[key]['pages'] or
                    (key==request.get('source') and request['earlier_cycle_page'])]
        for key in missing:
            row = rows[key]
            row.update(rows=[],pages=0,observed_at=None)
            if row['result'] != 'ERROR': row.update(result='NOT_RUN',last_error=None)
        state = 'NOT_STARTED' if not status['cycle_count'] else 'INCOMPLETE' if missing or partial else 'COMPLETE'
        coverage_field = 'page_live_cycle_coverage' if page_live else 'account_cycle_coverage'
        status[coverage_field] = dict(state=state,cycle=status['cycle_count'],sampled_sources=sampled,
                                      unsampled_sources=missing,incomplete_sources=partial,earlier_cycle_sources=previous)
        if request:
            status[coverage_field]['uncommitted_request'] = {key:request[key] for key in ('source','generation')}

    def claim(self, sources, interval_seconds=5, request_budget=10, resume=False, *, workspace_identity_sha256=None,
              track_account_cycles=False, page_run_id=None, account_run_id=None,
              foreground_owner=None, execution_purpose=None):
        if type(sources) is not dict or not sources or not set(sources) <= set(CHANNELS): fail('MONITOR_SOURCES')
        if type(resume) is not bool: fail('MONITOR_RESUME_FLAG')
        if foreground_owner is not None and (type(foreground_owner) is not Journal or self.mode!='READ_ONLY'
                or foreground_owner.mode!='READ_ONLY'
                or foreground_owner.directory!=self.journal.directory/'runner-owner' or not foreground_owner.locked_by_current_thread()):
            fail('MONITOR_FOREGROUND_OWNER_REQUIRED')
        positive_integer(request_budget, 1000)
        descriptors = {}
        for channel, pair in sources.items():
            if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) not in (ReadPlan,PageLiveReadPlan,*METRIC_PLAN_TYPES): fail('MONITOR_SOURCE_CONTRACT')
            source_plan(channel, asdict(pair[0]))
            session = read_session(pair, self.clock)
            if session.mode != self.mode or pair[0].operation not in CHANNELS[channel]: fail('MONITOR_CHANNEL_OR_MODE')
            descriptors[channel] = asdict(pair[0])
        validate_relations({name:{'plan':descriptor} for name,descriptor in descriptors.items()},strict=True)
        with self.journal.locked(wait_seconds=.5):
            state = self._load(); stamp = self.clock(); aware_time(stamp)
            if state is not None:
                if 'execution_purpose' in state or execution_purpose is not None:
                    fail('MONITOR_CONTRACT_CHECK_REQUIRES_NEW_DIRECTORY')
                observed = self._observed(state)
                if ('workspace_identity_sha256' in state
                        and state['workspace_identity_sha256']!=workspace_identity_sha256):
                    fail('MONITOR_WORKSPACE_IDENTITY')
                if {key: row['plan'] for key, row in state['sources'].items()} != descriptors: fail('MONITOR_SCOPE_CHANGED_USE_NEW_DIRECTORY')
                if observed['phase'] == 'RUNNING' and (not resume or foreground_owner is None):
                    fail('MONITOR_ALREADY_ACTIVE')
                if 'rate_limit' in state and aware_time(stamp) < resume_time(state['rate_limit']):
                    fail('RATE_LIMIT_WAIT_REQUIRED')
                if observed['phase'] in ('PAUSED', 'INTERRUPTED') and not resume: fail('MONITOR_EXPLICIT_RESUME_REQUIRED')
            old = state
            state = dict(schema='read-monitor-v1', mode=self.mode, phase='RUNNING', worker=uuid.uuid4().hex,
                         last_heartbeat=stamp, last_error=None, interval_seconds=interval_seconds,
                         request_budget=request_budget, requests_reserved=0, cycle_count=0,
                         sources={key: source_state(pair[0]) for key, pair in sources.items()})
            if foreground_owner is not None:
                state['foreground_claim_id'] = state['worker']
            if workspace_identity_sha256 is not None:
                state['workspace_identity_sha256'] = workspace_identity_sha256
            if page_run_id is not None:
                state['page_run'] = {'id':page_run_id}
            if account_run_id is not None:
                state['account_run_id'] = account_run_id
            if execution_purpose is not None:
                state['execution_purpose'] = execution_purpose
            if track_account_cycles:
                state['account_cycle_sources'] = []
                state['account_cycle_pagination'] = ACCOUNT_CYCLE_PAGINATION
            if old is not None:
                if resume and foreground_owner is not None and old['phase']=='RUNNING':
                    state['resume_recovery'] = FOREGROUND_RESUME_RECOVERY
                for key, row in state['sources'].items():
                    row['generation'] = old['sources'][key]['generation']
                    row['last_successful_sync'] = old['sources'][key]['last_successful_sync']
            self.journal.save(state, validate)
            self.sources = dict(sources); self.sessions = {}; self.worker = state['worker']
            self.resumed_comment_checkpoint = (resume_comment_checkpoint(old,stamp)
                if old is not None and resume and page_run_id is not None and foreground_owner is not None else None)
            return self.worker

    def control(self, action):
        if action not in ('pause', 'stop'): fail('MONITOR_CONTROL_ACTION')
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state is None: fail('MONITOR_NOT_CONFIGURED')
            if action == 'pause':
                if self._observed(state)['phase'] != 'RUNNING': fail('MONITOR_PAUSE_REQUIRES_RUNNING')
                # The last heartbeat may still be fresh after the process exits.
                # Probe without waiting while holding the state lock, as status does.
                probe = self.foreground_owner_probe()
                if probe is not None and probe() != 'HELD': fail('MONITOR_PAUSE_REQUIRES_RUNNING')
                state['phase'] = 'PAUSED'
            else: state.update(phase='STOPPED', worker=None)
            self.journal.save(state, validate)
        return self.status()

    def attach_verified_sources(self, worker, sources, *, blocked=None, insights_role=None):
        """Replace preflight transports before polling, without changing the claimed scope."""
        blocked = {} if blocked is None else blocked
        if (self.mode != 'READ_ONLY' or self.worker != worker
                or set(blocked) - set(METRIC_CHANNELS) or not set(blocked.values()) <= PAGE_METRICS_BLOCKERS):
            fail('MONITOR_SOURCE_PREFLIGHT_CONTRACT')
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker or state['phase'] != 'RUNNING': fail('MONITOR_CONTROL_CHANGED')
            scope = state['sources']
            if (state['cycle_count'] or state['requests_reserved'] or set(sources) & set(blocked)
                    or set(sources) | set(blocked) != set(scope)):
                fail('MONITOR_SOURCE_PREFLIGHT_CONTRACT')
            for key, pair in sources.items():
                if asdict(pair[0]) != scope[key]['plan'] or read_session(pair,self.clock).mode != self.mode:
                    fail('MONITOR_SOURCE_PREFLIGHT_CONTRACT')
            for key, error in blocked.items():
                self._pause_dependent_source(key, scope[key], error)
            if insights_role is not None:
                state['insights_role_observation'] = dict(insights_role)
            if blocked or insights_role is not None:
                self.journal.save(state, validate)
            self.sources = dict(sources)

    def _rate_limit_claim(self, worker):
        state = self._load()
        if (worker is None or self.worker!=worker
                or worker not in (state['worker'],state.get('foreground_claim_id'))):
            fail('MONITOR_CONTROL_CHANGED')
        return state

    def record_rate_limit(self, worker, hold):
        """Keep a received cooldown for its original claim, even after a local stop."""
        with self.journal.locked(wait_seconds=.5):
            state = self._rate_limit_claim(worker)
            previous = state.get('rate_limit',{})
            count = previous.get('automatic_retries_reserved',0)
            if not previous or aware_time(hold['not_before']) > aware_time(previous['not_before']):
                saved = dict(hold,automatic_retries_reserved=count)
                if 'local_not_before' in previous and resume_time(previous)>resume_time(saved):
                    saved['local_not_before'] = previous['local_not_before']
                state['rate_limit'] = saved
                self.journal.save(state,validate)
            return (state['worker']==worker and state['phase']=='RUNNING' and count==0
                    and state['requests_reserved']<state['request_budget'])

    def preserve_rate_limit_wait(self, worker, remaining_seconds):
        with self.journal.locked(wait_seconds=.5):
            state = self._rate_limit_claim(worker)
            hold = state['rate_limit']
            saved = preserve_remaining_wait(hold,aware_time(self.clock()),remaining_seconds)
            if saved != hold:
                state['rate_limit'] = saved
                self.journal.save(state,validate)

    def reserve_rate_limit_retry(self, worker):
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker or state['phase'] != 'RUNNING': fail('MONITOR_CONTROL_CHANGED')
            hold = state['rate_limit']; stamp = self.clock()
            if (aware_time(stamp) < resume_time(hold) or hold['automatic_retries_reserved']
                    or state['requests_reserved'] >= state['request_budget']):
                return False
            hold['automatic_retries_reserved'] = 1
            state.update(requests_reserved=state['requests_reserved']+1,last_heartbeat=stamp)
            self.journal.save(state,validate)
            return True

    def _reserve(self, worker, channel):
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker or state['phase'] != 'RUNNING': return None
            row = state['sources'][channel]
            if row['phase'] != 'READY': return None
            error = dependency_error(state['sources'],channel,row['plan'])
            if error:
                self._pause_dependent_source(channel, row, error)
                self.journal.save(state,validate)
                return None
            stamp = self.clock()
            if aware_time(stamp) < aware_time(state['last_heartbeat']):
                state.update(phase='INTERRUPTED', worker=None, last_error='CLOCK_ROLLBACK')
                self.journal.save(state, validate); return None
            if state['requests_reserved'] >= state['request_budget']:
                state.update(phase='STOPPED', worker=None, last_error='REQUEST_BUDGET_EXHAUSTED')
                self.journal.save(state, validate); return None
            tracked = [state[field] for field in CYCLE_SOURCE_FIELDS if field in state]
            earlier = bool(tracked and row['pages'] and all(channel not in sampled for sampled in tracked))
            session = self.sessions.get(channel)
            if session is None or session.state in ('COMPLETE', 'EMPTY'):
                session = read_session(self.sources[channel], self.clock); self.sessions[channel] = session
                row.update(generation=row['generation'] + 1, pages=0, rows=[], result='NOT_RUN', observed_at=None, last_error=None)
            if session.pages >= 1000:
                row.update(phase='PAUSED', result='ERROR', last_error='MONITOR_OBSERVATION_BUDGET')
                self.journal.save(state, validate); return None
            state.update(last_heartbeat=stamp, requests_reserved=state['requests_reserved'] + 1)
            if tracked:
                state['uncommitted_request'] = dict(source=channel,generation=row['generation'],earlier_cycle_page=earlier)
            self.journal.save(state, validate)
            return row['generation']

    def _pause_dependent_source(self, channel, row, error):
        row.update(phase='PAUSED', result='ERROR', rows=[], pages=0, observed_at=None, last_error=error)
        self.sessions.pop(channel, None)

    def _commit(self, worker, channel, generation, result, *, stop_reason=None):
        with self.journal.locked(wait_seconds=.5):
            state = self._load(); row = state['sources'][channel]
            # stop/pause/replacement is honored without holding a lock while
            # the network is blocked. A late response cannot reanimate state.
            if state['worker'] != worker or state['phase'] != 'RUNNING' or row['generation'] != generation: return False
            stamp = self.clock()
            if aware_time(stamp) < aware_time(state['last_heartbeat']):
                state.update(phase='INTERRUPTED', worker=None, last_error='CLOCK_ROLLBACK')
                self.journal.save(state, validate); return False
            state['last_heartbeat'] = stamp
            if len(result['rows']) > 1000:
                row.update(phase='PAUSED', result='ERROR', last_error='MONITOR_OBSERVATION_BUDGET', observed_at=stamp)
            else:
                if channel=='live_post' and result['state']=='COMPLETE':
                    expected = complete_row(state['sources'],'live').get('video',{}).get('id')
                    attached = result['rows'][0]['attachments']['data'][0]['target']['id']
                    if attached!=expected:
                        result = dict(result,state='ERROR',rows=[],pages=0,last_error='LIVE_POST_VIDEO_UNVERIFIED')
                error = result['last_error']
                if error is not None and error not in SAFE_ERRORS: error = 'MONITOR_COLLECTOR_FAILURE'
                row.update(rows=result['rows'], pages=result['pages'], result=result['state'], observed_at=stamp, last_error=error)
                if result['state'] == 'ERROR': row['phase'] = 'PAUSED'
                elif result['state'] in ('COMPLETE', 'EMPTY'):
                    row['last_successful_sync'] = stamp
                    if channel == 'live' and any(x.get('status') in LIVE_END_STATES for x in row['rows']): row['phase'] = 'ENDED'
            if (channel in ('live', 'live_post') and row['result'] != 'COMPLETE'
                    and 'expected_live_video_id' in state['sources'].get('live', {}).get('plan', {})):
                # Invalidate dependent samples in the same journal update, even
                # if this request used the final slot or a source was already paused.
                dependents = ('comments', 'live_post', *METRIC_CHANNELS) if channel == 'live' else METRIC_CHANNELS
                for name in dependents:
                    source = state['sources'].get(name)
                    if source is None or source['last_error'] in PAGE_METRICS_BLOCKERS: continue
                    error = dependency_error(state['sources'], name, source['plan'])
                    if error is None: continue
                    self._pause_dependent_source(name, source, error)
            if stop_reason is not None:
                state.update(phase='INTERRUPTED',worker=None,last_error=stop_reason)
            state.pop('uncommitted_request', None)
            for field in CYCLE_SOURCE_FIELDS:
                if field in state and channel not in state[field]:
                    state[field] = sorted([*state[field],channel])
            self.journal.save(state, validate)
            return True

    def tick(self, worker, pages_per_source=1, abort_errors=(), *, page_live_cycle=False, abort_account_permission=False):
        positive_integer(pages_per_source, MAX_PAGES_PER_SOURCE)
        if type(abort_errors) is not tuple or not set(abort_errors) <= SAFE_ERRORS: fail('MONITOR_ABORT_ERRORS')
        if worker != self.worker: fail('MONITOR_LOCAL_WORKER_NOT_BOUND')
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker or state['phase'] not in ('RUNNING', 'PAUSED'): return False
            stamp = self.clock()
            if aware_time(stamp) < aware_time(state['last_heartbeat']):
                state.update(phase='INTERRUPTED', worker=None, last_error='CLOCK_ROLLBACK')
                self.journal.save(state, validate); return False
            state['last_heartbeat'] = stamp
            if state['phase'] == 'PAUSED':
                self.journal.save(state, validate); return True
            if 'account_cycle_sources' in state:
                state['account_cycle_sources'] = []
                self.sessions.clear()
            previous_comments = None
            resumed_checkpoint = None
            if page_live_cycle:
                state.setdefault('live_cycle_history', [])
                if pages_per_source == 1:
                    previous_comments = previous_comment_ids(state)
                    if state['cycle_count']==0:
                        resumed_checkpoint = self.resumed_comment_checkpoint
                    comments = self.sessions.get('comments')
                    if comments is not None and comments.plan.operation == RECENT_LIVE_COMMENTS:
                        self.sessions.pop('comments')
                state['page_live_cycle_sources'] = []
            state['cycle_count'] += 1; self.journal.save(state, validate)
        sampled = set()
        order = list(self.sources)
        ordered_checks = ('page','live','live_post') if page_live_cycle else ('live','live_post')
        dependencies = [name for name in ordered_checks if name in self.sources]
        order = dependencies + [name for name in order if name not in dependencies]
        for channel in order:
            for _ in range(pages_per_source):
                generation = self._reserve(worker, channel)
                if generation is None: break
                try: result = self.sessions[channel].collect(1)
                except Exception:
                    result = self.sessions[channel].report()
                    result.update(state='ERROR', last_error='MONITOR_COLLECTOR_FAILURE')
                stop_reason = None
                if channel=='account':
                    if self.mode=='READ_ONLY' and result['last_error']=='RETURNED_ACCOUNT_MISMATCH':
                        stop_reason = 'RETURNED_ACCOUNT_MISMATCH'
                    elif abort_account_permission and result['last_error']=='PERMISSION':
                        stop_reason = NAMED_ACCOUNT_PERMISSION_DENIED
                elif page_live_cycle and channel=='page' and result['last_error']=='RETURNED_OBJECT_MISMATCH':
                    stop_reason = PAGE_COLLECTOR_IDENTITY_MISMATCH
                if not self._commit(worker, channel, generation, result,stop_reason=stop_reason): return False
                if stop_reason is not None: return False
                sampled.add(channel)
                if result['last_error'] in abort_errors:
                    error = result['last_error']
                    reason = (error if error in (*GRAPH_AUTH_ERRORS,'CONNECTION_EXPIRED_OR_CLOCK_SKEW',
                                                  'RATE_LIMIT',*ACCOUNT_SCOPE_ERRORS) else
                              'MONITOR_SESSION_EXPIRED' if error == 'MONITOR_SESSION_EXPIRED' else
                              'CREDENTIAL_REFERENCE_REVIEW_REQUIRED' if error in RECORD_ERRORS else 'MONITOR_RUN_FAILURE')
                    self.finish(worker, interrupted=True, reason=reason)
                    return False
                if result['state'] != 'GAP': break
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker or state['phase'] != 'RUNNING': return False
            live_phase = state['sources'].get('live',{}).get('phase')
            if page_live_cycle:
                history = state['live_cycle_history']
                summary = summarize_cycle(state, sampled, previous_comments, resumed_checkpoint)
                history.append(summary)
                state['live_cycle_history'] = history[-HISTORY_LIMIT:]
                checkpoint = comment_checkpoint(state,summary,sampled)
                if checkpoint is None:
                    state.pop(COMMENT_CHECKPOINT,None)
                else:
                    state[COMMENT_CHECKPOINT] = checkpoint
            if page_live_cycle and live_phase in ('ENDED','PAUSED'):
                ended = live_phase=='ENDED'
                state.update(phase='STOPPED' if ended else 'PAUSED',worker=None,
                             last_error='SOURCES_ENDED' if ended else 'SOURCES_REQUIRE_REVIEW')
            elif state['requests_reserved'] >= state['request_budget']:
                state.update(phase='STOPPED', worker=None, last_error='REQUEST_BUDGET_EXHAUSTED')
            elif all(row['phase'] != 'READY' for row in state['sources'].values()):
                blocked = any(row['phase'] == 'PAUSED' for row in state['sources'].values())
                state.update(phase='PAUSED' if blocked else 'STOPPED', worker=None,
                             last_error='SOURCES_REQUIRE_REVIEW' if blocked else 'SOURCES_ENDED')
            self.journal.save(state, validate)
            return state['phase'] == 'RUNNING'

    def finish(self, worker, interrupted=False, reason=None):
        if reason not in CONTROL_ERRORS: fail('MONITOR_FINISH_REASON')
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state['worker'] != worker: return
            state['worker'] = None
            if interrupted: state.update(phase='INTERRUPTED', last_error='WORKER_INTERRUPTED')
            elif state['phase'] == 'RUNNING': state['phase'] = 'STOPPED'
            if reason is not None: state['last_error'] = reason
            self.journal.save(state, validate)

    def record_page_failure(self, run_id, kind, outcome):
        """Attach a finalized local receipt to the same Page Live claim."""
        if kind not in PAGE_RUN_FAILURES:
            fail('MONITOR_PAGE_RUN')
        with self.journal.locked(wait_seconds=.5):
            state = self._load()
            if state is None or state.get('page_run')!={'id':run_id} or state['worker'] is not None:
                fail('MONITOR_PAGE_RUN_CHANGED')
            state['page_run'][kind] = outcome
            self.journal.save(state,validate)

    def _wait(self, worker, seconds, *, exit_on_pause=False):
        deadline = time.monotonic() + seconds
        phases = ('RUNNING',) if exit_on_pause else ('RUNNING','PAUSED')
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: return True
            with self.journal.locked(wait_seconds=.5):
                state = self._load()
                if state is None or state['worker'] != worker or state['phase'] not in phases: return False
            time.sleep(min(.25, remaining))

    def run(self, sources, interval_seconds=5, request_budget=10, cycles=10, pages_per_source=1, resume=False,
            *, exit_on_pause=False):
        positive_integer(cycles, 10000); positive_integer(pages_per_source, 20)
        worker = self.claim(sources, interval_seconds, request_budget, resume)
        interrupted = True
        try:
            for index in range(cycles):
                if not self.tick(worker, pages_per_source): break
                if index + 1 < cycles and not self._wait(worker, interval_seconds,exit_on_pause=exit_on_pause): break
            interrupted = False
        finally: self.finish(worker, interrupted)
        return self.status()
