"""Explicitly confirmed, bounded foreground read runner; dormant in all demos.

Every start/resume rechecks metadata. No OAuth, refresh, credential persistence,
automatic restart or remote writes. The same read contracts back fake-HTTP tests.
"""
from dataclasses import asdict
from datetime import timedelta
from functools import partial
import getpass
import hashlib
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
import warnings

from .connection import (blank_intent, validate_intent, preview as connection_preview, utcnow,
                         _assert_window, _credential_format, _debug, verified_metadata, _write,
                         _MemoryBroker, CHECK_ERRORS)
from .credential_record import RecordGuard, ERRORS as RECORD_ERRORS
from .http_read import (ACCOUNT_SCOPE_ERRORS, ACCOUNT_STATUS_REVIEW_REQUIRED, PAGE_SCOPE_CHANGED, GraphReadTransport,
                        ReadBinding, _PageCollectorBinding)
from .journal import Journal, canonical, digest
from .live_contract import LIVE_CONTRACT_PROBE
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS
from .monitor import (ACCOUNT_CYCLE_PAGINATION, AD_INSIGHTS_CHANNEL_SETS, AD_INVENTORY_CHANNELS, AD_PLACEMENT_CHANNELS,
                      MAX_PAGES_PER_SOURCE, NAMED_ACCOUNT_PERMISSION_DENIED,
                      PAGE_RUN_FAILURES, ReadMonitor, CHANNELS, source_plan, validate_relations)
from .monitor_tools import new_directory, save_report_with_state
from .native_credentials import NativeCredentialBroker, credential_labels
from .page_live_credentials import (PAGE_COLLECTOR_IDENTITY_MISMATCH, PAGE_DEVELOPER_REJECTION_ERRORS,
                                    PAGE_IDENTITY_STAGES, PREFLIGHT_ERRORS, ROLE_OPTION,
                                    page_authentication_issue, page_permission_issue,
                                    page_token_preflight_plan, startup_get_limit, verify_page_credentials)
from .rate_limit import MAX_AUTOMATIC_WAIT_SECONDS
from .read_adapter import (GRAPH_AUTH_ERRORS, INSIGHTS_OPERATIONS, LIVE_PENDING, RECENT_LIVE_COMMENTS, PageLiveReadPlan,
                           ReadPlan, ReadSession, positive_integer)
from .validation import InputError, aware_time, fail, object_only, strict_json

KEYS = ('schema', 'connection', 'page_id', 'sources', 'interval_seconds', 'duration_seconds',
        'request_budget', 'cycles', 'pages_per_source')
ERRORS = CHECK_ERRORS | PREFLIGHT_ERRORS | {
    'MONITOR_SESSION_EXPIRED', 'MONITOR_CONTROL_CHANGED', 'MONITOR_RUN_FAILURE',
    'MONITOR_SCOPE_METADATA_MISSING', 'MONITOR_SECRET_INPUT_UNAVAILABLE', 'STATE_BUSY',
    NAMED_ACCOUNT_PERMISSION_DENIED, *ACCOUNT_SCOPE_ERRORS,
}
GLOBAL_ERRORS = (*GRAPH_AUTH_ERRORS, 'CONNECTION_EXPIRED_OR_CLOCK_SKEW', 'RATE_LIMIT', 'MONITOR_SESSION_EXPIRED', 'WORKSPACE_MONITOR_SCOPE_CHANGED',
                 PAGE_SCOPE_CHANGED, *ACCOUNT_SCOPE_ERRORS, *sorted(RECORD_ERRORS))
PAGE_LIVE_SCHEMA = 'workspace-page-live-monitor-intent-v1'
LIVE_RELEASE_BLOCKER = 'REAL_LIVE_CONTRACT_PENDING'
WORKSPACE_ACCOUNT_SOURCE_SETS = (*AD_INSIGHTS_CHANNEL_SETS, AD_INVENTORY_CHANNELS, AD_PLACEMENT_CHANNELS)
PLACEMENT_PAGE_LIMIT = 50
PLACEMENT_PAGES = 3
PLACEMENT_GET_LIMIT = 3 + PLACEMENT_PAGES


def _page_live_release_blocker():
    # Set by release acceptance after named Meta read evidence exists. This is
    # not derived from plans, terminal confirmations or local fixtures.
    return LIVE_RELEASE_BLOCKER


def blank_monitor_intent():
    plan = asdict(ReadPlan('account', 'SIM_ACCOUNT', account_id='SIM_ACCOUNT'))
    plan.update(object_id=None, account_id=None)
    return dict(schema='monitor-intent-v1', connection=blank_intent(), page_id=None,
                sources={'account': plan}, interval_seconds=60, duration_seconds=300,
                request_budget=10, cycles=5, pages_per_source=1)


def page_polling_preview(intent):
    return dict(max_cycles=intent['cycles'],interval_seconds=intent['interval_seconds'],
                duration_seconds=intent['duration_seconds'],
                comment_sampling='NEWEST_PAGE_EACH_CYCLE' if intent['cycles']>1 else 'BOUNDED_CURRENT_QUERY',
                terminal_live_behavior='FINISH_CURRENT_CYCLE_THEN_STOP')


def validate_monitor_intent(value):
    schema = value.get('schema') if type(value) is dict else None
    scoped = schema in ('workspace-monitor-intent-v1', PAGE_LIVE_SCHEMA)
    page_live = schema == PAGE_LIVE_SCHEMA
    keys = KEYS + (('workspace_scope_sha256',) if scoped else ())
    if page_live: keys += ('expected_live_video_id',)
    object_only(value, keys + ((ROLE_OPTION,) if page_live else ()), keys)
    verify_role = value.get(ROLE_OPTION,False)
    if type(verify_role) is not bool:
        fail('PAGE_INSIGHTS_ROLE_OPTION_INVALID')
    if scoped and (type(value['workspace_scope_sha256']) is not str
                   or not re.fullmatch('[0-9a-f]{64}',value['workspace_scope_sha256'])):
        fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
    if schema == 'workspace-monitor-intent-v1':
        if (type(value['sources']) is not dict or set(value['sources']) not in WORKSPACE_ACCOUNT_SOURCE_SETS
                or value['page_id'] is not None):
            fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
    elif not page_live and schema != 'monitor-intent-v1':
        fail('MONITOR_INTENT_SCHEMA')
    connection = validate_intent(value['connection'],page=page_live)
    page_id = value['page_id']
    if page_id is not None and (type(page_id) is not str or not re.fullmatch(r'[0-9]{1,40}', page_id)):
        fail('MONITOR_PAGE_WHITELIST')
    for key, maximum in (('interval_seconds', 3600), ('duration_seconds', 3600),
                         ('request_budget', 1000), ('cycles', 10000), ('pages_per_source', MAX_PAGES_PER_SOURCE)):
        positive_integer(value[key], maximum)
    if page_live and value['request_budget'] <= startup_get_limit(value):
        fail('PAGE_MONITOR_BUDGET_MUST_INCLUDE_PREFLIGHT_AND_READ')
    if page_live and value['cycles']>1:
        if (value['cycles']-1)*value['interval_seconds'] >= value['duration_seconds']:
            fail('PAGE_MONITOR_INTERVALS_EXCEED_DURATION')
        if value['pages_per_source']!=1:
            fail('PAGE_PERIODIC_MONITOR_REQUIRES_ONE_PAGE_SAMPLE')
    if not page_live and value['request_budget'] < 3: fail('MONITOR_BUDGET_MUST_INCLUDE_TWO_PREFLIGHT_GETS')
    sources = value['sources']
    if type(sources) is not dict or not 1 <= len(sources) <= len(CHANNELS) or not set(sources) <= set(CHANNELS):
        fail('MONITOR_SOURCES')
    video = value.get('expected_live_video_id')
    if page_live:
        expected = {'page','live'}
        if video is not None:
            if type(video) is not str or not re.fullmatch('[0-9]{1,40}',video): fail('MONITOR_NUMERIC_SCOPE_REQUIRED')
            expected.add('comments')
        if 'live_post' in sources:
            expected.update(('live_post', *METRIC_CHANNELS))
        if set(sources)!=expected or page_id!=connection['page_id'] or ('live_post' in sources and video is None):
            fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
        if verify_role and 'live_post' not in sources:
            fail('PAGE_INSIGHTS_ROLE_REQUIRES_METRICS')
    elif 'account' not in sources:
        fail('MONITOR_ACCOUNT_SOURCE_REQUIRED')
    plans = {key: source_plan(key, data) for key, data in sources.items()}
    if 'placements' in plans:
        if schema != 'workspace-monitor-intent-v1':
            fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
        if (value['cycles'] != 1 or value['request_budget'] > PLACEMENT_GET_LIMIT
                or value['pages_per_source'] > PLACEMENT_PAGES or plans['placements'].limit > PLACEMENT_PAGE_LIMIT):
            fail('INVALID_BUDGET')
    live = plans.get('live')
    if type(live) is PageLiveReadPlan and (not page_live or live.expected_live_video_id != video):
        fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
    if page_live and video is not None and type(live) is ReadPlan:
        # Earlier Page-only intents stored the expected Video at the top level.
        # Keep the reviewed intent intact while applying its pin at runtime.
        plans['live'] = PageLiveReadPlan(**asdict(live), expected_live_video_id=video)
    page_used = False
    for channel, plan in plans.items():
        if channel == 'ads' and plan.operation not in INSIGHTS_OPERATIONS: fail('MONITOR_ADS_CHANNEL_REQUIRES_INSIGHTS')
        object_pattern = r'[1-9][0-9]{0,39}_[1-9][0-9]{0,39}' if plan.operation in ('post_insights','live_post') else r'[0-9]{1,40}'
        if (not re.fullmatch(object_pattern,plan.object_id)
                or any(identifier is not None and not re.fullmatch(r'[0-9]{1,40}',identifier)
                       for identifier in (plan.account_id,plan.page_id,getattr(plan,'live_id',None),getattr(plan,'video_id',None)))):
            fail('MONITOR_NUMERIC_SCOPE_REQUIRED')
        if plan.account_id and plan.account_id != connection.get('account_id'): fail('MONITOR_ACCOUNT_WHITELIST')
        if plan.page_id:
            page_used = True
            if plan.page_id != page_id: fail('MONITOR_PAGE_WHITELIST')
        if page_live and channel in METRIC_CHANNELS and getattr(plan,'video_id',None)!=video:
            fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
        if page_live and channel=='comments' and plan.operation!=RECENT_LIVE_COMMENTS:
            fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
        if channel=='comments' and plan.operation==RECENT_LIVE_COMMENTS and plan.since is not None:
            if not page_live or value['pages_per_source']!=1:
                fail('PAGE_COMMENT_SINCE_REQUIRES_ONE_PAGE_SAMPLE')
    if page_id is not None and not page_used: fail('MONITOR_UNUSED_PAGE_WHITELIST')
    validate_relations({key: {'plan': asdict(plan)} for key, plan in plans.items()},strict=True)
    return plans


def load_monitor_intent(path):
    path = checked_path(path, link_error='MONITOR_INTENT_SYMLINK', volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 64000: fail('MONITOR_INTENT_FILE')
    value = strict_json(path.read_text(encoding='utf-8-sig'))
    validate_monitor_intent(value)
    receipt_path = path.parent / 'RECEIPT.json'
    if value['schema']=='monitor-intent-v1' and path.name=='monitor-intent.json' and receipt_path.is_file():
        receipt_path = checked_path(receipt_path,link_error='MONITOR_INTENT_SYMLINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
        if receipt_path.stat().st_size>64000:
            fail('MONITOR_RECEIPT_SIZE')
        origin = strict_json(receipt_path.read_text(encoding='utf-8'))
        if isinstance(origin,dict) and origin.get('task') in ('ads-analysis','ads-review','ads-monitor','ads-inventory','ads-placements'):
            fail('WORKSPACE_LEGACY_PLAN_REGENERATE')
    return value


def recorded_monitor_intent(plan, consent):
    """Recover the exact reviewed intent from a matching local confirmation pair."""
    error = 'MONITOR_RECORDED_SCOPE_INVALID'
    if type(plan) is not dict or type(consent) is not dict:
        fail(error)
    intent = plan.get('intent')
    duration = intent.get('duration_seconds') if type(intent) is dict else None
    if (type(duration) is not int or not 1 <= duration <= 3600
            or 'execution_purpose' in plan or 'execution_purpose' in consent
            or plan.get('schema')!='monitor-preview-v1' or plan.get('status')!='READY_FOR_LOCAL_SCOPE_CONFIRMATION'
            or plan.get('remote_write_authorized') is not False
            or consent.get('method')!='LOCAL_TERMINAL_SCOPE_CONFIRMATION' or consent.get('authorization_reusable') is not False
            or plan.get('intent_sha256')!=consent.get('intent_sha256')):
        fail(error)
    try:
        intent_hash = digest(intent)
    except (ValueError, RecursionError):
        fail(error)
    approved = aware_time(consent.get('approved_at'))
    deadline = aware_time(consent.get('run_deadline'))
    if intent_hash != plan.get('intent_sha256') or deadline - approved != timedelta(seconds=duration):
        fail(error)
    return intent


def prepare_monitor(directory):
    root = new_directory(directory); _write(root, 'monitor-intent.json', blank_monitor_intent())
    return dict(status='BLANK_MONITOR_INTENT_CREATED', credential_store_accessed=False, network_used=False)


def validate_page_contract_probe(intent, plans):
    if intent['schema']!=PAGE_LIVE_SCHEMA:
        fail('PAGE_LIVE_CONTRACT_CHECK_REQUIRES_PAGE_PLAN')
    if (intent['cycles']!=1 or intent['pages_per_source']!=1 or intent['duration_seconds']>300
            or intent['request_budget']!=startup_get_limit(intent)+len(plans)
            or ('comments' in plans and (plans['comments'].limit>10 or plans['comments'].since is not None))):
        fail('PAGE_LIVE_CONTRACT_CHECK_REQUIRES_FIXED_SINGLE_ROUND')
    if 'live_post' in plans and not intent.get(ROLE_OPTION,False):
        fail('PAGE_LIVE_CONTRACT_CHECK_REQUIRES_INSIGHTS_ROLE_PLAN')


def preview_monitor(intent, *, record_directory=None, workspace=None, clock=utcnow, contract_probe=False):
    plans = validate_monitor_intent(intent)
    page_live = intent['schema']==PAGE_LIVE_SCHEMA
    if contract_probe:
        validate_page_contract_probe(intent,plans)
    connection = connection_preview(intent['connection'], record_directory=record_directory,page=page_live)
    blockers = list(connection['blockers'])
    workspace_timezone = workspace_currency = None
    if intent['schema']=='workspace-monitor-intent-v1':
        from .client_workspace import monitor_workspace_account_context
        try:
            workspace_timezone,workspace_currency = monitor_workspace_account_context(workspace,intent,clock)
        except InputError as exc:
            blockers.append(str(exc))
    elif page_live:
        from .client_workspace import check_page_live_workspace
        try:
            record = connection['credential_record'] or {}
            check_page_live_workspace(workspace,intent,record.get('record_sha256'))
        except InputError as exc:
            blockers.append(str(exc))
    elif workspace is not None:
        blockers.append('WORKSPACE_LEGACY_PLAN_REGENERATE')
    if not contract_probe and any(plan.operation in LIVE_PENDING for plan in plans.values()):
        blocker = _page_live_release_blocker() if page_live else LIVE_RELEASE_BLOCKER
        if blocker is not None:
            blockers.append(blocker)
    startup = startup_get_limit(intent) if page_live else 2
    result = dict(schema='monitor-preview-v1', intent_sha256=digest(intent), intent=intent,
                connection=connection, blockers=blockers,
                status='PREPARATION_BLOCKED' if blockers else 'READY_FOR_LOCAL_SCOPE_CONFIRMATION',
                initial_get_attempts=startup, total_get_attempt_limit=intent['request_budget'],
                collector_attempt_limit=intent['request_budget'] - startup,
                source_requests={key: plan.request() for key, plan in plans.items()},
                workspace_account_timezone=workspace_timezone,
                required_scopes=(['pages_read_engagement'] if page_live else
                                 ['ads_read'] + (['pages_read_engagement'] if 'page' in plans else [])),
                resume='FRESH_CONFIRMATION_AND_DEBUG_FROM_FIRST_PAGE_NO_STORED_CREDENTIAL',
                network_used=False, remote_write_authorized=False, independent_human_identity_verified=False)
    if intent['schema']=='workspace-monitor-intent-v1':
        result['workspace_account_currency'] = workspace_currency
    if contract_probe:
        result.update(execution_purpose=LIVE_CONTRACT_PROBE, automatic_retry=False,
                      release_gate_changes=False, resume='NEW_EXPLICIT_CONTRACT_CHECK_REQUIRED')
    if intent['schema']=='workspace-monitor-intent-v1' or set(plans) in AD_INSIGHTS_CHANNEL_SETS:
        result['account_cycle_pagination'] = ACCOUNT_CYCLE_PAGINATION
    if page_live:
        verify_role = intent.get(ROLE_OPTION,False)
        preflight = page_token_preflight_plan('live_post' in plans,verify_role=verify_role)
        startup_requests = list(preflight['requests'])
        if verify_role:
            startup_requests.append(preflight['optional_insights_role_lookup'])
        # Preserve the reviewed local connection inputs while replacing the standalone Page probe.
        result['connection'] = dict(connection,schema='page-live-monitor-connection-preview-v1',
                                    maximum_get_attempts=startup,requests=startup_requests)
        result['page_token_preflight'] = preflight
        result['page_live_polling'] = page_polling_preview(intent)
        source = connection['intent']
        result['session_credentials'] = dict(developer_user=source['credential_source'])
        if source['credential_source']=='OS_REFERENCE':
            service, account = credential_labels(source['credential_reference'],source['app_id'],
                                                 source['principal_id'],source['page_id'])
            result['session_credentials']['page_token'] = dict(source='OS_PREEXISTING_NAMED_PAGE',
                                                                 service_or_target=service,account_label=account,
                                                                 record_managed_by_owner=True)
    return result


class _RunGuard:
    def __init__(self, monitor, worker, record, started, deadline, clock, monotonic, scope_check=None, *, intent_hash=None):
        self.scope_check = scope_check
        self.intent_hash = intent_hash
        self.monitor = monitor; self.worker = worker; self.record = record
        self.started = started; self.deadline = deadline; self.clock = clock; self.monotonic = monotonic
        self.hard_deadline = monotonic() + (deadline - clock()).total_seconds()
        self.cooldown_monotonic_deadline = None

    @classmethod
    def for_page(cls, intent, monitor, worker, started, deadline, clock, monotonic, *, workspace, record_directory):
        from .client_workspace import check_page_live_workspace

        intent = strict_json(canonical(intent))
        validate_monitor_intent(intent)
        if intent['schema'] != PAGE_LIVE_SCHEMA: fail(PAGE_SCOPE_CHANGED)
        connection = intent['connection']
        checked = connection_preview(connection,record_directory=record_directory,page=True)
        if checked['blockers']: fail('MONITOR_PREPARATION_INCOMPLETE')
        record_hash = (checked['credential_record'] or {}).get('record_sha256')
        scope = partial(check_page_live_workspace,workspace,intent,record_hash)
        record = RecordGuard(record_directory,connection,record_hash) if record_hash is not None else None
        guard = cls(monitor,worker,record,started,deadline,clock,monotonic,scope,intent_hash=digest(intent))
        guard.check()
        return guard

    def check(self):
        stamp = self.clock()
        if not self.started <= stamp < self.deadline or self.monotonic() >= self.hard_deadline:
            fail('MONITOR_SESSION_EXPIRED')
        if self.scope_check is not None:
            self.scope_check()
        if self.record is not None: self.record.check()
        with self.monitor.journal.locked(wait_seconds=.5):
            state = self.monitor._load()
            if state['worker'] != self.worker or state['phase'] != 'RUNNING': fail('MONITOR_CONTROL_CHANGED')

    def restrict_expiry(self, stamp):
        self.deadline = min(self.deadline, aware_time(stamp))
        self.hard_deadline = min(self.hard_deadline, self.monotonic() + (self.deadline - self.clock()).total_seconds())
        self.check()

    def time_remaining(self):
        return min((self.deadline-self.clock()).total_seconds(), self.hard_deadline-self.monotonic())

    def wait(self, seconds, sleep):
        until = self.monotonic() + seconds
        while True:
            remaining = until - self.monotonic()
            if remaining <= 0: break
            self.check(); sleep(min(.25, remaining))
        self.check()

    def record_rate_limit(self, hold):
        started = self.monotonic()
        retry_available = self.monitor.record_rate_limit(self.worker,hold)
        wait = aware_time(hold['not_before']) - aware_time(hold['observed_at'])
        deadline = started + wait.total_seconds()
        if self.cooldown_monotonic_deadline is None or deadline > self.cooldown_monotonic_deadline:
            self.cooldown_monotonic_deadline = deadline
        return retry_available

    def preserve_cooldown(self):
        if self.cooldown_monotonic_deadline is not None:
            remaining = self.cooldown_monotonic_deadline-self.monotonic()
            if remaining > 0:
                self.monitor.preserve_rate_limit_wait(self.worker,remaining)

    def record_preflight_rate_limit(self, hold):
        self.record_rate_limit(hold)
        self.check()

    def retry_rate_limit(self, sleep, hold):
        self.preserve_cooldown()
        self.check()
        if self.cooldown_monotonic_deadline is None:
            return False
        stamp, elapsed = self.clock(), self.monotonic()
        # A forward wall-clock adjustment cannot make the provider wait elapse.
        delay = max(0,(aware_time(hold['not_before'])-stamp).total_seconds(),self.cooldown_monotonic_deadline-elapsed)
        remaining = min((self.deadline-stamp).total_seconds(),self.hard_deadline-elapsed)
        if delay > MAX_AUTOMATIC_WAIT_SECONDS or delay >= remaining:
            return False
        self.wait(delay,sleep)
        return self.monitor.reserve_rate_limit_retry(self.worker)


def _binding(plan, connection, observed, expires, scopes=(), verified=False):
    return ReadBinding(digest(asdict(plan)), connection['app_id'], connection['principal_id'],
                       observed.isoformat(), expires.isoformat(), expires.isoformat(), tuple(scopes), verified, verified, verified)


def _poll_sources(intent, guard, sleep, *, allow_retry=True):
    for _, transport in guard.monitor.sources.values():
        transport.record_rate_limit = guard.record_rate_limit
        if allow_retry:
            transport.retry_rate_limit = partial(guard.retry_rate_limit,sleep)
    for cycle in range(intent['cycles']):
        guard.check()
        if not guard.monitor.tick(guard.worker,intent['pages_per_source'],abort_errors=GLOBAL_ERRORS,
                                  page_live_cycle=intent['schema']==PAGE_LIVE_SCHEMA,
                                  abort_account_permission=intent['schema']=='workspace-monitor-intent-v1'): break
        if cycle + 1 < intent['cycles']: guard.wait(intent['interval_seconds'],sleep)


def _collect_page_live(intent, guard, developer_credential, page_credential, *, reserve, sleep, verified=None,
                       allow_retry=True):
    """Join Page preflight and collection for a reviewed run. Public dispatch remains gated."""
    intent = strict_json(canonical(intent))
    plans = validate_monitor_intent(intent)
    if (intent['schema'] != PAGE_LIVE_SCHEMA or type(guard) is not _RunGuard
            or guard.intent_hash != digest(intent) or guard.scope_check is None or not callable(reserve)):
        fail(PAGE_SCOPE_CHANGED)
    monitor = guard.monitor
    # Check the claimed budget and sources before the first credential exchange.
    guard.check()
    state = monitor.status()
    descriptors = {key: asdict(plan) for key, plan in plans.items()}
    if (state['mode'] != 'READ_ONLY' or state['cycle_count'] or state['requests_reserved']
            or state['request_budget'] != intent['request_budget'] - startup_get_limit(intent)
            or {key: row['plan'] for key, row in state['sources'].items()} != descriptors):
        fail(PAGE_SCOPE_CHANGED)
    metadata = verify_page_credentials(intent['connection'],developer_credential,page_credential,
                                       include_metrics=bool(set(plans) & set(METRIC_CHANNELS)),
                                       clock=guard.clock,deadline=guard.deadline,guard=guard.check,reserve=reserve,
                                       on_rate_limit=guard.record_preflight_rate_limit,
                                       verify_role=intent.get(ROLE_OPTION,False),time_remaining=guard.time_remaining)
    guard.restrict_expiry(metadata['page']['effective_read_expires_at'])
    if verified is not None:
        verified(metadata)
    roles_verified = metadata['insights_role_verified']
    blocked = {} if roles_verified else {key:metadata['page']['insights_state'] for key in plans if key in METRIC_CHANNELS}
    connection = intent['connection']
    checked_at = metadata['actor_observed_at']
    expiry = guard.deadline.isoformat()
    sources = {}; brokers = []
    try:
        for key, plan in plans.items():
            if key in blocked: continue
            metric = key in METRIC_CHANNELS
            scopes = ('pages_read_engagement','read_insights') if metric else ('pages_read_engagement',)
            observed = metadata['insights_role_observation']['observed_at'] if metric else checked_at
            binding = _PageCollectorBinding(
                plan_hash=digest(asdict(plan)), app_id=connection['app_id'], principal_id=connection['principal_id'],
                observed_at=observed, expires_at=expiry, review_expires_at=expiry,
                granted_scopes=scopes, app_binding_verified=True, asset_ownership_verified=True,
                read_authorized=True, page_id=connection['page_id'], analyze_task_verified=metric and roles_verified,
            )
            broker = _MemoryBroker(page_credential,binding,guard)
            brokers.append(broker)
            sources[key] = (plan,GraphReadTransport(plan,binding,broker,clock=guard.clock,time_remaining=guard.time_remaining))
        monitor.attach_verified_sources(guard.worker,sources,blocked=blocked,
                                        insights_role=metadata.get('insights_role_observation'))
        _poll_sources(intent,guard,sleep,allow_retry=allow_retry)
    finally:
        for broker in brokers: broker._credential = None
    return metadata


def _collect_account(intent, plans, viewed, guard, credential, *, reserve, sleep, verified):
    connection = intent['connection']
    reserve('DEBUG_TOKEN')
    raw = _debug(credential,guard.check,clock=guard.clock,on_rate_limit=guard.record_preflight_rate_limit,
                 time_remaining=guard.time_remaining)
    guard.check()
    metadata = verified_metadata(raw,connection,guard.clock(),guard.deadline)
    raw = None
    if not set(viewed['required_scopes']) <= set(metadata['scopes']): fail('MONITOR_SCOPE_METADATA_MISSING')
    guard.restrict_expiry(metadata['effective_read_expires_at'])
    sources = {}; brokers = []
    try:
        for key, plan in plans.items():
            binding = _binding(plan,connection,aware_time(metadata['observed_at']),guard.deadline,metadata['scopes'],True)
            broker = _MemoryBroker(credential,binding,guard); brokers.append(broker)
            context = None
            if key == 'account' and intent['schema']=='workspace-monitor-intent-v1':
                context = viewed['workspace_account_timezone'],viewed['workspace_account_currency']
            sources[key] = (plan, GraphReadTransport(plan,binding,broker,clock=guard.clock,expected_account_context=context,
                                                     time_remaining=guard.time_remaining))
        guard.check()
        reserve('NAMED_ACCOUNT_GET')
        sources['account'][1].record_rate_limit = guard.record_rate_limit
        read = ReadSession(*sources['account']).collect(1)
        guard.check()
        if read['state'] != 'COMPLETE' or not {'currency', 'timezone_name', 'account_status'} <= set(read['rows'][0]):
            error = read['last_error']
            if error=='PERMISSION' and intent['schema']=='workspace-monitor-intent-v1':
                fail(NAMED_ACCOUNT_PERMISSION_DENIED)
            fail(error if error in CHECK_ERRORS or error in ACCOUNT_SCOPE_ERRORS else 'ACCOUNT_READ_REJECTED')
        account = read['rows'][0]
        if account['account_status'] != 1:
            fail(ACCOUNT_STATUS_REVIEW_REQUIRED)
        # Standalone plans have no workspace context. Use the named account
        # probe as the baseline for every account read in this run.
        sources['account'][1].expected_account_context = account['timezone_name'], account['currency']
        verified(metadata,account)
        guard.monitor.attach_verified_sources(guard.worker,sources)
        _poll_sources(intent,guard,sleep)
    finally:
        for broker in brokers: broker._credential = None


def _local_token(prompt):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning:
        fail('MONITOR_SECRET_INPUT_UNAVAILABLE')


def _state_root(directory, intent, resume):
    root = checked_path(directory, link_error='MONITOR_STATE_SYMLINK', volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if not resume:
        root = new_directory(root)
        # Establish the Journal ownership marker before adding auxiliary files.
        Journal(root, mode='READ_ONLY'); _write(root, 'runner-intent.json', intent)
    else:
        if any(p.is_symlink() for p in (root, *root.parents)): fail('MONITOR_STATE_SYMLINK')
        if not (root / 'operator-state.json').is_file(): fail('MONITOR_RESUME_STATE_REQUIRED')
        previous = load_monitor_intent(root / 'runner-intent.json')
        if previous != intent: fail('MONITOR_SCOPE_CHANGED_USE_NEW_DIRECTORY')
    return root


def _resume_scope_evidence(root, intent, state):
    from .workspace_runner import verify_recorded_intent
    try:
        return verify_recorded_intent(root,intent,run_id=state.get('confirmed_run_id'))
    except (InputError, OSError):
        fail('MONITOR_RESUME_SCOPE_EVIDENCE_REQUIRED')


def run_monitor(intent, directory, *, resume=False, record_directory=None, workspace=None, clock=utcnow,
                monotonic=time.monotonic, sleep=time.sleep, contract_probe=False):
    """Actual run requires prior user authorization; tests replace every IO boundary."""
    if type(resume) is not bool: fail('MONITOR_RESUME_FLAG')
    if contract_probe and resume:
        fail('PAGE_LIVE_CONTRACT_CHECK_CANNOT_RESUME')
    intent = strict_json(canonical(intent)); plans = validate_monitor_intent(intent)
    page_live = intent['schema']==PAGE_LIVE_SCHEMA
    viewed = preview_monitor(intent, record_directory=record_directory, workspace=workspace, clock=clock,
                             contract_probe=contract_probe)
    if page_live and LIVE_RELEASE_BLOCKER in viewed['blockers']:
        fail(LIVE_RELEASE_BLOCKER)
    if viewed['blockers']: fail('MONITOR_PREPARATION_INCOMPLETE')
    existing_owner = False
    if resume:
        resume_root = _state_root(directory,intent,True)
        prior_monitor = ReadMonitor(resume_root,mode='READ_ONLY',clock=lambda:clock().isoformat())
        existing = prior_monitor.status()
        if existing.get('rate_limit_pending'): fail('RATE_LIMIT_WAIT_REQUIRED')
        existing_owner = existing['phase']!='NOT_CONFIGURED'
        if existing_owner:
            owner = Journal(resume_root/'runner-owner',mode='READ_ONLY',existing_only=True)
            try:
                with owner.locked(existing_only=True):
                    _resume_scope_evidence(resume_root,intent,prior_monitor.status())
            except FileNotFoundError:
                fail('MONITOR_OWNER_LOCK_UNAVAILABLE')
    if not sys.stdin.isatty() or not sys.stdout.isatty(): fail('CONNECTION_INTERACTIVE_TERMINAL_REQUIRED')
    reviewed_at = clock(); confirmation_deadline = reviewed_at + timedelta(minutes=5)
    print(canonical(viewed))
    action = 'VERIFY LIVE CONTRACT' if contract_probe else 'MONITOR'
    phrase = action + ' ' + digest(intent)[:12] + ' MAX ' + str(intent['request_budget']) + ' GETS'
    if contract_probe:
        print('本人确认现成直播、具名资产及当次只读权限。单轮合同检查，无自动补试或周期监控。')
    else:
        print('本人先批准此具名读取范围。时长含秘密输入及连接核验；暂停/停止后须重新确认。无自动续期或写入。')
    if input('核对计划后输入 ' + phrase + '：') != phrase: fail('CONNECTION_NOT_CONFIRMED')
    approved_at = _assert_window(reviewed_at, confirmation_deadline, clock)
    deadline = approved_at + timedelta(seconds=intent['duration_seconds'])
    connection = intent['connection']; record = None
    if connection['credential_source'] == 'OS_REFERENCE':
        record = RecordGuard(record_directory, connection, viewed['connection']['credential_record']['record_sha256'])
        record.check()
    scope_check = None; identity = None; page_binding_sha256 = None; account_profile = None
    if intent['schema']=='workspace-monitor-intent-v1':
        from .client_workspace import checked_account_workspace, workspace_identity
        scope_check = partial(checked_account_workspace,workspace,intent,clock)
        account_profile = scope_check()
        identity = workspace_identity(account_profile)
    elif page_live:
        from .client_workspace import check_page_live_workspace, page_live_scope, workspace_identity
        record_hash = (viewed['connection']['credential_record'] or {}).get('record_sha256')
        profile = check_page_live_workspace(workspace,intent,record_hash)
        identity = workspace_identity(profile)
        page_binding_sha256 = page_live_scope(profile,connection,include_check=False)
    root = resume_root if resume else _state_root(directory,intent,False)
    owner = Journal(root / 'runner-owner', mode='READ_ONLY',existing_only=existing_owner)
    # Separate lock from collector state: status/pause/stop remain available.
    with owner.locked(existing_only=existing_owner):
        monitor = ReadMonitor(root, mode='READ_ONLY', clock=lambda: clock().isoformat())
        prior_scope = None
        if resume:
            _state_root(root,intent,True)
            existing = monitor.status()
            if existing.get('rate_limit_pending'): fail('RATE_LIMIT_WAIT_REQUIRED')
            if existing['phase']!='NOT_CONFIGURED':
                prior_scope = _resume_scope_evidence(root,intent,existing)
            elif existing_owner:
                fail('MONITOR_RESUME_SCOPE_EVIDENCE_REQUIRED')
        initial = {key: (plan, GraphReadTransport(plan, _binding(plan, connection, approved_at, deadline), clock=clock))
                   for key, plan in plans.items()}
        audit = new_directory(root / 'runs' / uuid4().hex)
        _write(audit, 'PLAN.json', viewed)
        scope_purpose = dict(execution_purpose=LIVE_CONTRACT_PROBE) if contract_probe else {}
        workspace_binding = {} if identity is None else dict(workspace_identity_sha256=identity)
        _write(audit, 'CONSENT.json', dict(intent_sha256=digest(intent), approved_at=approved_at.isoformat(),
               run_deadline=deadline.isoformat(), method='LOCAL_TERMINAL_SCOPE_CONFIRMATION',
               independently_authenticated_human=False, authorization_reusable=False, **scope_purpose, **workspace_binding))
        worker = monitor.claim(initial,intent['interval_seconds'],viewed['collector_attempt_limit'],resume=resume,
                               workspace_identity_sha256=identity,
                               track_account_cycles='account_cycle_pagination' in viewed,
                               page_run_id=audit.name if page_live else None,
                               account_run_id=None if page_live else audit.name,foreground_owner=owner,**scope_purpose)
        credential = page_credential = None
        guard = None
        native_attempted = False; preflight = 0; metadata = None; checked = False
        preflight_stage = None
        result = dict(status='MONITOR_RUN_FAILED', error_code=None, preflight_account_read_verified=False, **scope_purpose)
        if page_live:
            result.update(preflight_page_actor_verified=False,page_native_access_attempted=False)

        def reserve(stage):
            nonlocal preflight, preflight_stage
            if preflight >= viewed['initial_get_attempts']: fail('MONITOR_RUN_FAILURE')
            _write(audit,f'ATTEMPT-{preflight+1}.json',dict(stage=stage,reserved_at=clock().isoformat()))
            preflight += 1
            preflight_stage = stage

        def verified(value, account=None):
            nonlocal metadata, checked
            proof = dict(metadata=value,observed_at=clock().isoformat(),
                         ownership_basis='OPERATOR_DECLARATION_PLUS_ACCESS_NOT_INDEPENDENT_LEGAL_OWNERSHIP')
            if account is not None:
                proof['account'] = account
            _write(audit,'CONNECTION.json',proof)
            metadata = value
            checked = True
            result['preflight_page_actor_verified' if page_live else 'preflight_account_read_verified'] = True

        try:
            if page_live:
                guard = _RunGuard.for_page(intent,monitor,worker,approved_at,deadline,clock,monotonic,
                                           workspace=workspace,record_directory=record_directory)
            else:
                guard = _RunGuard(monitor,worker,record,approved_at,deadline,clock,monotonic,scope_check)
            guard.check(); _assert_window(reviewed_at, confirmation_deadline, clock)
            if connection['credential_source'] == 'SESSION_INPUT':
                credential = _local_token('本人本机Token（不回显、不保存；不要发到聊天）：')
            else:
                native_attempted = True
                native_binding = initial['page' if page_live else 'account'][1].binding
                with NativeCredentialBroker(connection['credential_reference'], native_binding, enabled=True).lease(native_binding) as value:
                    credential = value
                value = None
            guard.check(); _assert_window(reviewed_at, confirmation_deadline, clock); _credential_format(credential)
            if page_live:
                result['page_native_access_attempted'] = True
                page_plan, page_transport = initial['page']
                page_binding = page_transport.binding
                with NativeCredentialBroker(connection['credential_reference'],page_binding,enabled=True,
                                            page_plan=page_plan).lease(page_binding) as value:
                    page_credential = value
                value = None
                guard.check(); _assert_window(reviewed_at,confirmation_deadline,clock)
                _collect_page_live(intent,guard,credential,page_credential,reserve=reserve,sleep=sleep,verified=verified,
                                   allow_retry=not contract_probe)
            else:
                _collect_account(intent,plans,viewed,guard,credential,reserve=reserve,sleep=sleep,verified=verified)
            result['status'] = 'MONITOR_RUN_ENDED'
        except KeyboardInterrupt:
            result.update(status='INTERRUPTED', error_code='USER_INTERRUPT')
            monitor.finish(worker, interrupted=True)
        except Exception as exc:
            code = str(exc) if type(exc) is InputError and str(exc) in ERRORS else 'MONITOR_RUN_FAILURE'
            result['error_code'] = code
            if code == 'MONITOR_CONTROL_CHANGED': result['status'] = 'MONITOR_RUN_ENDED'
            else:
                reason = ('MONITOR_SESSION_EXPIRED' if code == 'MONITOR_SESSION_EXPIRED' else
                          'CREDENTIAL_REFERENCE_REVIEW_REQUIRED' if code in RECORD_ERRORS else
                          code if code in (*GRAPH_AUTH_ERRORS,'CONNECTION_EXPIRED_OR_CLOCK_SKEW','RATE_LIMIT',
                                           'RETURNED_ACCOUNT_MISMATCH',NAMED_ACCOUNT_PERMISSION_DENIED,*ACCOUNT_SCOPE_ERRORS,
                                           *PAGE_IDENTITY_STAGES) else
                          'CONNECTION_PREFLIGHT_FAILED' if not checked else 'MONITOR_RUN_FAILURE')
                monitor.finish(worker, interrupted=True, reason=reason)
        finally:
            credential = page_credential = None
            if guard is not None: guard.preserve_cooldown()
            monitor.finish(worker)
        report, state = save_report_with_state(monitor, audit / 'report')
        fatal_committed = (state['last_error'] in (NAMED_ACCOUNT_PERMISSION_DENIED,'RETURNED_ACCOUNT_MISMATCH',
                                                  PAGE_COLLECTOR_IDENTITY_MISMATCH)
                           or page_live and state['last_error'] in GRAPH_AUTH_ERRORS)
        if (state['phase']=='INTERRUPTED' or fatal_committed) and result['status']=='MONITOR_RUN_ENDED':
            fatal = next((row['last_error'] for row in state['sources'].values() if row['last_error'] in GLOBAL_ERRORS), state['last_error'])
            result.update(status='MONITOR_RUN_FAILED', error_code=fatal)
        for key in ('account_cycle_coverage','account_cycle_pagination','page_live_cycle_coverage'):
            if key in state: result[key] = state[key]
        if 'resume_recovery' in state:
            result['resume_recovery'] = state['resume_recovery']
        coverage = state.get('account_cycle_coverage') or state.get('page_live_cycle_coverage')
        blocker = report.get('analysis_blocker')
        if blocker is not None: result['analysis_blocker'] = blocker
        if (result['status']=='MONITOR_RUN_ENDED' and (any(row['phase']=='PAUSED' for row in state['sources'].values())
                or blocker is not None or coverage is not None and coverage['state']=='INCOMPLETE')):
            result['status'] = 'MONITOR_REVIEW_REQUIRED'
        if intent['schema']=='workspace-monitor-intent-v1':
            from .client_workspace import record_monitor_account_failure
            result['workspace_health_update'] = record_monitor_account_failure(
                workspace,intent,record,result['error_code'],clock,initial_profile=account_profile)
        elif page_live:
            issue = page_authentication_issue(result['error_code'],preflight_stage,checked,result['page_native_access_attempted'])
            permission = page_permission_issue(result['error_code'],preflight_stage)
            if issue is not None:
                result['page_authentication_issue'] = issue
                result['workspace_health_update'] = 'NOT_APPLICABLE'
                if issue['credential']=='DEVELOPER_USER' and result['error_code'] in PAGE_DEVELOPER_REJECTION_ERRORS:
                    from .client_workspace import record_page_live_developer_rejection
                    result['workspace_health_update'] = record_page_live_developer_rejection(
                        workspace,intent,record,approved_at,clock)
            elif permission is not None:
                result['page_permission_issue'] = permission
                result['workspace_health_update'] = 'NOT_APPLICABLE'
                if permission['credential']=='DEVELOPER_USER':
                    from .client_workspace import record_page_live_developer_rejection
                    result['workspace_health_update'] = record_page_live_developer_rejection(
                        workspace,intent,record,approved_at,clock,named_permission=True)
        evidence_paths = {
            'result': (audit / 'RESULT.json').relative_to(root).as_posix(),
            'receipt': (audit / 'RECEIPT.json').relative_to(root).as_posix(),
            'report_markdown': (audit / 'report' / 'REPORT.md').relative_to(root).as_posix(),
            'report_json': (audit / 'report' / 'REPORT.json').relative_to(root).as_posix(),
        }
        result.update(run_id=audit.name, evidence_paths=evidence_paths,
                      monitor_phase=state['phase'], monitor_last_error=state['last_error'],
                      source_results={key: row['result'] for key, row in state['sources'].items()},
                      metadata=metadata, native_access_attempted=native_attempted,
                      initial_get_attempts_reserved=preflight, collector_attempts_reserved=state['requests_reserved'],
                      total_get_attempts_reserved=preflight + state['requests_reserved'],
                      request_count_semantics='RESERVED_NOT_PROOF_OF_TRANSMISSION', report=report,
                      secret_persisted=False, remote_write_authorized=False, resumed=resume)
        if 'rate_limit' in state:
            result.update(rate_limit=state['rate_limit'],rate_limit_pending=state['rate_limit_pending'])
        if 'workspace_identity_sha256' in report:
            result['workspace_identity_sha256'] = report['workspace_identity_sha256']
        if prior_scope is not None:
            result['resume_scope_evidence'] = prior_scope
        _write(audit, 'RESULT.json', result)
        files = [dict(path=str(path.relative_to(audit)), bytes=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                 for path in sorted(audit.rglob('*')) if path.is_file()]
        _write(audit, 'RECEIPT.json', dict(status=result['status'], files=files, authorization_reusable=False,
                                         real_live_verified=False, independent_human_identity_verified=False, **scope_purpose))
        for kind in PAGE_RUN_FAILURES:
            issue = result.get(f'page_{kind}_issue')
            if issue is None:
                continue
            result_sha256 = next(item['sha256'] for item in files if item['path']=='RESULT.json')
            monitor.record_page_failure(audit.name,kind,dict(binding_sha256=page_binding_sha256,
                error_code=result['error_code'],issue=issue,
                workspace_health_update=result['workspace_health_update'],result_sha256=result_sha256,
                receipt_sha256=hashlib.sha256((audit/'RECEIPT.json').read_bytes()).hexdigest()))
        return result
