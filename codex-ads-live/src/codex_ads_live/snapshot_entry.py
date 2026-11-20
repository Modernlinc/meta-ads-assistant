"""Owner-confirmed, bounded snapshot collection. No write execution or renewal."""
from dataclasses import asdict
from datetime import timedelta
import getpass
import hashlib
import sys
import time
import warnings

from .connection import (blank_intent, validate_intent, preview as connection_preview, utcnow,
                         _assert_window, _credential_format, _debug, verified_metadata, _write,
                         _MemoryBroker, CHECK_ERRORS)
from .credential_record import RecordGuard
from .http_read import GraphReadTransport, ReadBinding
from .journal import canonical, digest
from .local_paths import checked_path
from .meta_write_mapping import SNAPSHOT_OPERATIONS, blank_draft, missing_website_event_fields
from .monitor_tools import new_directory
from .native_credentials import NativeCredentialBroker
from .read_adapter import GRAPH_AUTH_ERRORS
from .validation import InputError, aware_time, fail, object_only, strict_json
from .write_snapshots import (FIELDS, SnapshotPlan, validate_allowlist, collect_snapshots,
                              validate_bundle, export_snapshot_review, validate_snapshot_draft)

KEYS = ('schema', 'connection', 'draft', 'allowlist', 'duration_seconds')
ERRORS = CHECK_ERRORS | {
    'SNAPSHOT_ENTRY_FAILED', 'SNAPSHOT_ENTRY_SESSION_EXPIRED', 'SNAPSHOT_ENTRY_SCOPE_CHANGED',
    'SNAPSHOT_ENTRY_REQUEST_BUDGET', 'SNAPSHOT_ENTRY_SCOPE_NOT_GRANTED',
    *('SNAPSHOT_' + code for code in GRAPH_AUTH_ERRORS), 'SNAPSHOT_PERMISSION', 'SNAPSHOT_RATE_LIMIT', 'SNAPSHOT_OBJECT_OR_PARAMETER',
    'SNAPSHOT_SERVICE_UNAVAILABLE', 'SNAPSHOT_UNCLASSIFIED_ERROR', 'SNAPSHOT_CAPTURE_DEADLINE',
    'SNAPSHOT_CHANGED_DURING_CAPTURE', 'SNAPSHOT_POST_READ_AUTHORIZATION_FAILED', 'SNAPSHOT_READ_BINDING_CHANGED',
    'SNAPSHOT_OBJECT_MISMATCH', 'SNAPSHOT_ACCOUNT_MISMATCH', 'SNAPSHOT_REQUIRED_FIELD_MISSING',
    'SNAPSHOT_NOT_ALLOWLISTED', 'SNAPSHOT_PARENT_RELATION_MISMATCH', 'SNAPSHOT_CREATIVE_PAGE_MISMATCH',
    'SNAPSHOT_SINGLE_OBJECT_REQUIRED', 'SNAPSHOT_RESPONSE_ENVELOPE', 'SNAPSHOT_BUDGET_REPRESENTATION',
    'SNAPSHOT_STATUS_UNKNOWN', 'SNAPSHOT_ACCOUNT_STATUS', 'SNAPSHOT_CURRENCY', 'SNAPSHOT_TIMEZONE',
    'SNAPSHOT_SPECIAL_CATEGORIES', 'SNAPSHOT_ENUM_FORMAT', 'SNAPSHOT_SECRET_PATTERN_REFUSED',
    'SNAPSHOT_DECLARATION_CONFLICT', 'SNAPSHOT_BUDGET_MODE_UNKNOWN', 'SNAPSHOT_PARENT_BUDGET_MODE_UNKNOWN',
    'SNAPSHOT_NOT_EXISTING_DAILY_BUDGET', 'SNAPSHOT_CAMPAIGN_BUDGET_REQUIRES_OTHER_PROFILE',
    'SNAPSHOT_STALE_OR_FUTURE', 'META_WRITE_BEFORE_STATUS', 'META_WRITE_MINOR_UNITS', 'META_WRITE_CURRENCY_PROFILE',
    'SNAPSHOT_ENTRY_TIME', 'SNAPSHOT_CAPTURE_WINDOW', 'SNAPSHOT_BUNDLE_HASH_MISMATCH',
}


def blank_snapshot_intent(operation):
    if operation not in SNAPSHOT_OPERATIONS:
        fail('SNAPSHOT_OPERATION_UNSUPPORTED')
    return dict(schema='snapshot-read-intent-v1', connection=blank_intent(), draft=blank_draft(operation),
                allowlist=dict(account_id=None, **{level: [] for level in FIELDS if level != 'ACCOUNT'}), duration_seconds=180)


def validate_snapshot_intent(value):
    object_only(value, KEYS, KEYS)
    if value['schema'] != 'snapshot-read-intent-v1': fail('SNAPSHOT_ENTRY_SCHEMA')
    if len(canonical(value).encode('utf-8')) > 32000: fail('SNAPSHOT_ENTRY_TOO_LARGE')
    draft = validate_snapshot_draft(value['draft'])
    connection = validate_intent(value['connection'])
    allowed = validate_allowlist(value['allowlist'])
    if not connection['account_id'] == draft['account_id'] == allowed['account_id']: fail('SNAPSHOT_ENTRY_ACCOUNT_SCOPE')
    if type(value['duration_seconds']) is not int or not 1 <= value['duration_seconds'] <= 300: fail('SNAPSHOT_ENTRY_DURATION')
    op = draft['operation']; params = draft['parameters']; target = draft['target_id']
    if any(v is None for key,v in params.items() if key != 'promoted_object') or missing_website_event_fields(params):
        fail('SNAPSHOT_ENTRY_PARAMETERS_INCOMPLETE')
    # One action, one exact node at each required level. Unknown ancestors must
    # be identified first; returned IDs never expand the confirmed allowlist.
    required = {}; levels = []
    if op == 'CREATE_ADSET': required = {'CAMPAIGN': params['campaign_id']}; levels = ['CAMPAIGN']
    elif op == 'CREATE_AD':
        required = dict(ADSET=params['adset_id'], CAMPAIGN=None, CREATIVE=params['creative']['creative_id'], PAGE=draft['context']['page_id'])
        levels = ['ADSET', 'CAMPAIGN', 'CREATIVE', 'PAGE']
        if required['PAGE'] is None: fail('SNAPSHOT_ENTRY_PAGE_REQUIRED')
    elif not op.startswith('CREATE_'):
        level = draft['target_level']
        if target is None or level not in ('CAMPAIGN','ADSET','AD'): fail('SNAPSHOT_ENTRY_TARGET_REQUIRED')
        levels = {'CAMPAIGN':['CAMPAIGN'], 'ADSET':['ADSET','CAMPAIGN'], 'AD':['AD','ADSET','CAMPAIGN']}[level]
        required = {item: target if item == level else None for item in levels}
    for level, ids in allowed.items():
        if level == 'account_id': continue
        if level not in required:
            if ids: fail('SNAPSHOT_ENTRY_UNUSED_ALLOWLIST')
        elif len(ids) != 1 or (required[level] is not None and ids != [required[level]]): fail('SNAPSHOT_ENTRY_EXACT_ALLOWLIST_REQUIRED')
    plans = [SnapshotPlan('ACCOUNT', allowed['account_id'], account_id=allowed['account_id'])]
    for level in levels:
        node = allowed[level][0]
        plans.append(SnapshotPlan(level,node,page_id=node) if level=='PAGE' else SnapshotPlan(level,node,account_id=allowed['account_id']))
    return plans


def load_snapshot_intent(path):
    path = checked_path(path, link_error='SNAPSHOT_ENTRY_PATH_LINK', volume_error='SNAPSHOT_ENTRY_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 32000: fail('SNAPSHOT_ENTRY_FILE')
    raw = path.read_bytes()
    if len(raw) > 32000: fail('SNAPSHOT_ENTRY_TOO_LARGE')
    value = strict_json(raw.decode('utf-8')); validate_snapshot_intent(value); return value


def prepare_snapshot(operation, directory):
    value = blank_snapshot_intent(operation); root = new_directory(directory)
    _write(root, 'snapshot-intent.json', value)
    return dict(status='BLANK_SNAPSHOT_INTENT_CREATED', network_used=False, credential_storage_accessed=False,
                authority='PREPARATION_ONLY_NOT_AUTHORIZATION')


def preview_snapshot(intent, *, record_directory=None):
    intent = strict_json(canonical(intent)); plans = validate_snapshot_intent(intent)
    connection = connection_preview(intent['connection'], record_directory=record_directory)
    binding = dict(intent=intent, credential_record_sha256=connection['credential_record']['record_sha256'] if connection['credential_record'] else None,
                   requests=[connection['requests'][0]] + [p.request() for p in plans] * 2)
    return dict(schema='snapshot-read-preview-v1', status='PREPARATION_BLOCKED' if connection['blockers'] else 'READY_FOR_LOCAL_SCOPE_CONFIRMATION',
                plan_sha256=digest(binding), intent_sha256=digest(intent), intent=intent, credential_record=connection['credential_record'],
                blockers=connection['blockers'], requests=binding['requests'], max_get_attempts=1+2*len(plans),
                required_scopes=['ads_read'] + (['pages_read_engagement'] if any(p.level=='PAGE' for p in plans) else []),
                session_seconds=intent['duration_seconds'], collector_seconds=60, network_used=False,
                native_access=intent['connection']['credential_source']=='OS_REFERENCE', writes=False,
                scope='DEBUG_PLUS_ONE_NAMED_DOUBLE_SWEEP_ONLY_NOT_MONITOR_OR_WRITE_AUTHORIZATION')


class _SnapshotGuard:
    def __init__(self, started, deadline, clock, monotonic, record):
        self.started=started; self.deadline=deadline; self.clock=clock; self.monotonic=monotonic; self.record=record
        self.tick=monotonic(); self.hard_deadline=self.tick+(deadline-started).total_seconds()

    def check(self):
        stamp=self.clock(); tick=self.monotonic()
        if not self.started <= stamp < self.deadline or not self.tick <= tick < self.hard_deadline: fail('SNAPSHOT_ENTRY_SESSION_EXPIRED')
        if self.record is not None: self.record.check()

    def restrict(self, expires):
        self.deadline=min(self.deadline,aware_time(expires))
        self.hard_deadline=min(self.hard_deadline,self.monotonic()+(self.deadline-self.clock()).total_seconds())
        self.check()

    def time_remaining(self):
        return min((self.deadline-self.clock()).total_seconds(), self.hard_deadline-self.monotonic())


def _error(exc):
    return str(exc) if type(exc) is InputError and str(exc) in ERRORS else 'SNAPSHOT_ENTRY_FAILED'


def run_snapshot(intent, directory, *, record_directory=None, clock=utcnow, monotonic=time.monotonic):
    """Actual invocation requires fresh user authorization; tests replace every IO boundary."""
    intent = strict_json(canonical(intent)); plans = validate_snapshot_intent(intent)
    viewed = preview_snapshot(intent, record_directory=record_directory)
    if viewed['blockers']: fail('SNAPSHOT_ENTRY_PREPARATION_INCOMPLETE')
    if not sys.stdin.isatty() or not sys.stdout.isatty(): fail('CONNECTION_INTERACTIVE_TERMINAL_REQUIRED')
    reviewed_at = clock(); confirmation_deadline = reviewed_at + timedelta(minutes=5); confirmation_tick = monotonic()
    print(canonical(viewed))
    phrase = 'SNAPSHOT ' + viewed['plan_sha256'][:12] + ' MAX ' + str(viewed['max_get_attempts']) + ' GETS'
    print('须本人事先批准此完整计划。仅本次凭据借用、认证检查和固定对象双轮GET；不继承旧授权，不写广告。')
    if input('核对后输入 ' + phrase + '：') != phrase: fail('CONNECTION_NOT_CONFIRMED')
    started = _assert_window(reviewed_at, confirmation_deadline, clock)
    if not 0 <= monotonic()-confirmation_tick < 300: fail('CONNECTION_REVIEW_EXPIRED')
    connection = intent['connection']; record = None
    if connection['credential_source']=='OS_REFERENCE':
        record = RecordGuard(record_directory, connection, viewed['credential_record']['record_sha256']); record.check()
    guard = _SnapshotGuard(started,started+timedelta(seconds=intent['duration_seconds']),clock,monotonic,record)
    root = new_directory(directory); _write(root, 'PLAN.json', viewed)
    _write(root, 'CONSENT.json', dict(plan_sha256=viewed['plan_sha256'], confirmed_at=started.isoformat(),
           deadline=guard.deadline.isoformat(), max_get_attempts=viewed['max_get_attempts'],
           method='LOCAL_TERMINAL_SCOPE_CONFIRMATION', independently_authenticated_human=False,
           authorization_reusable=False, remote_write_authorized=False))
    credential=None; brokers=[]; metadata=None; reserved=0; native_attempted=False
    result=dict(status='SNAPSHOT_CAPTURE_FAILED', error_code=None, complete_snapshot_saved=False,
                review_status=None, review_error_code=None, remote_write_authorized=False, dispatchable=False)

    def reserve(stage, plan=None):
        nonlocal reserved
        guard.check()
        if reserved >= viewed['max_get_attempts']: fail('SNAPSHOT_ENTRY_REQUEST_BUDGET')
        if plan is not None and plan not in plans: fail('SNAPSHOT_ENTRY_SCOPE_CHANGED')
        number=reserved+1
        _write(root, 'ATTEMPT-'+str(number)+'.json', dict(stage=stage, reserved_at=clock().isoformat(),
               request=plan.request() if plan is not None else viewed['requests'][0], may_have_reached_server=True))
        reserved=number

    try:
        guard.check()
        if connection['credential_source']=='SESSION_INPUT':
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                credential=getpass.getpass('本人本机Token（不回显、不保存；不要发到聊天）：')
        else:
            native_attempted=True
            initial=ReadBinding(digest(asdict(plans[0])),connection['app_id'],connection['principal_id'],
                                started.isoformat(),guard.deadline.isoformat(),guard.deadline.isoformat(),(),False,False,False)
            with NativeCredentialBroker(connection['credential_reference'],initial,enabled=True).lease(initial) as value:
                credential=value
            value=None
        guard.check(); _credential_format(credential)
        reserve('DEBUG_TOKEN'); raw=_debug(credential,guard.check,time_remaining=guard.time_remaining); guard.check()
        metadata=verified_metadata(raw,connection,clock(),guard.deadline); raw=None
        if not set(viewed['required_scopes']) <= set(metadata['scopes']): fail('SNAPSHOT_ENTRY_SCOPE_NOT_GRANTED')
        guard.restrict(metadata['effective_read_expires_at'])

        def factory(plan):
            guard.check()
            if plan not in plans: fail('SNAPSHOT_ENTRY_SCOPE_CHANGED')
            binding=ReadBinding(digest(asdict(plan)),connection['app_id'],connection['principal_id'],metadata['observed_at'],
                                guard.deadline.isoformat(),guard.deadline.isoformat(),tuple(metadata['scopes']),True,True,True)
            broker=_MemoryBroker(credential,binding,guard); brokers.append(broker)
            return GraphReadTransport(plan,binding,broker,clock=clock,time_remaining=guard.time_remaining)

        captured=collect_snapshots(intent['draft'],intent['allowlist'],factory,clock=clock,monotonic=monotonic,
                                   before_request=lambda plan: reserve('SNAPSHOT_OBJECT_GET',plan))
        guard.check()
        # Validate the whole read contract before persistence. Capture integrity
        # errors are not downstream write-profile review failures.
        bundle=strict_json(captured.payload)
        validate_bundle(intent['draft'],bundle,clock=clock); guard.check()
        _write(root,'SNAPSHOTS.json',bundle); result['complete_snapshot_saved']=True
        try:
            review=export_snapshot_review(intent['draft'],captured,root/'review',clock=clock)
            result.update(status='SNAPSHOT_CAPTURED_REVIEW_BLOCKED',review_status=review['status'],review_sha256=review['review_sha256'])
        except InputError as exc:
            result.update(status='SNAPSHOT_CAPTURED_REVIEW_BLOCKED',review_error_code=_error(exc))
            _write(root,'REVIEW_ERROR.json',dict(error_code=_error(exc),dispatchable=False))
    except KeyboardInterrupt: result.update(status='INTERRUPTED',error_code='USER_INTERRUPT')
    except Exception as exc: result['error_code']=_error(exc)
    finally:
        for broker in brokers: broker._credential=None
        credential=None
    result.update(metadata=metadata, get_attempts_reserved=reserved, native_access_attempted=native_attempted,
                  plan_sha256=viewed['plan_sha256'], intent_sha256=digest(intent),
                  count_semantics='RESERVED_ATTEMPTS_NOT_PROOF_OF_TRANSMISSION', credential_material_persisted=False,
                  independently_authenticated_human=False, authorization_reusable=False,
                  scope='THIS_ONE_SNAPSHOT_RUN_NOT_MONITOR_OR_WRITE_AUTHORIZATION')
    _write(root,'RESULT.json',result)
    files=[dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
           for p in sorted(root.rglob('*')) if p.is_file()]
    _write(root,'RECEIPT.json',dict(status=result['status'],files=files,authorization_reusable=False,
                                 remote_write_authorized=False,credential_material_in_artifacts=False))
    return result
