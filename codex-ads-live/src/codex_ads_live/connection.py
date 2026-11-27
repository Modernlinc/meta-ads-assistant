"""Interactive connection check for one named ad account or Page. Never invoked by demos.

Two GET attempts maximum: self debug_token, then the named asset. No OAuth,
refresh, secret provisioning, write action or background monitoring. A terminal
confirmation records local scope consent, not a cryptographic human identity.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import http.client
from pathlib import Path
import re
import ssl
import sys
import warnings
from urllib.parse import urlencode

from .http_read import CredentialBroker, GraphReadTransport, ReadBinding, PUBLIC_ERRORS, arm_http_deadline, read_json_response
from .graph_network import NETWORK_FAILURE, graph_https_connection
from .journal import canonical, digest
from .native_credentials import NativeCredentialBroker
from .credential_record import ERRORS as RECORD_ERRORS, RecordGuard, read_record, public_record
from .rate_limit import cooldown
from .read_adapter import GRAPH_AUTH_ERRORS, ReadPlan, ReadSession, error_category
from .validation import InputError, aware_time, fail, object_only, strict_json

KEYS = ('schema', 'app_id', 'principal_id', 'account_id', 'credential_source', 'credential_reference',
        'app_developer_confirmed', 'asset_owner_confirmed', 'appsecret_proof_requirement')
CHECK_ERRORS = PUBLIC_ERRORS | RECORD_ERRORS | {
    *GRAPH_AUTH_ERRORS, 'PERMISSION', 'RATE_LIMIT', 'OBJECT_OR_PARAMETER', 'SERVICE_UNAVAILABLE', 'UNCLASSIFIED_ERROR',
    'DEBUG_METADATA_REQUIRED', 'DEBUG_TOKEN_INVALID', 'DEBUG_APP_OR_PRINCIPAL_MISMATCH', 'DEBUG_USER_TOKEN_REQUIRED',
    'DEBUG_EXPIRY_NOT_BOUNDED', 'DEBUG_SCOPE_INVALID', 'DEBUG_ADS_READ_NOT_GRANTED', 'CONNECTION_REVIEW_EXPIRED',
    'CONNECTION_CHECK_FAILURE', 'ACCOUNT_READ_REJECTED', 'DEBUG_DATA_ERROR', 'DEBUG_ISSUED_IN_FUTURE',
    'DEBUG_PAGE_READ_NOT_GRANTED', 'DEBUG_PAGE_TARGET_NOT_GRANTED', 'DEBUG_PAGE_TARGET_INVALID',
    'RETURNED_ACCOUNT_MISMATCH', 'RETURNED_OBJECT_MISMATCH', 'PAGE_READ_REJECTED',
}


def utcnow(): return datetime.now(timezone.utc)


def blank_intent():
    return dict(schema='connection-intent-v1', app_id=None, principal_id=None, account_id=None,
                credential_source='SESSION_INPUT', credential_reference=None, app_developer_confirmed=False,
                asset_owner_confirmed=False, appsecret_proof_requirement='UNKNOWN')


def validate_intent(value, *, page=False):
    asset_id = 'page_id' if page else 'account_id'
    keys = (*KEYS, 'page_id') if page else KEYS
    if page:
        keys = tuple(key for key in keys if key != 'account_id')
    object_only(value, keys, keys)
    if value['schema'] != ('page-connection-intent-v1' if page else 'connection-intent-v1'):
        fail('CONNECTION_INTENT_SCHEMA')
    for key in ('app_id', 'principal_id', asset_id):
        if type(value[key]) is not str or not re.fullmatch('[0-9]{1,40}', value[key]): fail('CONNECTION_NUMERIC_IDS_REQUIRED')
    if value['credential_source'] not in ('SESSION_INPUT', 'OS_REFERENCE'): fail('CONNECTION_CREDENTIAL_SOURCE')
    reference = value['credential_reference']
    if value['credential_source'] == 'OS_REFERENCE':
        if type(reference) is not str or not re.fullmatch('conn_[0-9a-f]{32}', reference): fail('CREDENTIAL_REFERENCE_INVALID')
    elif reference is not None: fail('CONNECTION_UNUSED_REFERENCE')
    for key in ('app_developer_confirmed', 'asset_owner_confirmed'):
        if type(value[key]) is not bool: fail('CONNECTION_REVIEW_FLAGS')
    if value['appsecret_proof_requirement'] not in ('UNKNOWN', 'REQUIRED', 'NOT_REQUIRED_CONFIRMED'):
        fail('CONNECTION_PROOF_REQUIREMENT')
    return value


def load_intent(path):
    from .local_paths import checked_path
    path = checked_path(path, link_error='CONNECTION_INTENT_SYMLINK', volume_error='CONNECTION_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 16000: fail('CONNECTION_INTENT_FILE')
    return validate_intent(strict_json(path.read_text(encoding='utf-8-sig')))


def account_plan(intent): return ReadPlan('account', intent['account_id'], account_id=intent['account_id'])


def connection_plan(intent, page):
    return ReadPlan('page', intent['page_id'], page_id=intent['page_id']) if page else account_plan(intent)


def connection_requests(intent, page):
    return [{'method': 'GET', 'path': '/v27.0/debug_token',
             'params': {'input_token': 'IN_MEMORY_ONLY_NEVER_IN_PLAN_OR_LOG'},
             'authentication': 'SAME_APP_DEVELOPER_USER_TOKEN'}, connection_plan(intent,page).request()]


def preview(intent, *, record_directory=None, page=False, record_existing_only=False):
    validate_intent(intent, page=page)
    proof = intent['appsecret_proof_requirement']
    blockers = []
    if not intent['app_developer_confirmed']: blockers.append('APP_DEVELOPER_USER_TOKEN_REQUIRED')
    if not intent['asset_owner_confirmed']: blockers.append('NAMED_ASSET_OWNER_CONFIRMATION_REQUIRED')
    if proof != 'NOT_REQUIRED_CONFIRMED': blockers.append('APPSECRET_PROOF_REQUIREMENT_UNRESOLVED_OR_UNSUPPORTED')
    record = None
    if intent['credential_source'] == 'OS_REFERENCE':
        if record_directory is None: blockers.append('CREDENTIAL_RECORD_REQUIRED')
        else:
            record = public_record(read_record(record_directory,existing_only=record_existing_only))
            try: RecordGuard(record_directory,intent,record['record_sha256']).check(existing_only=record_existing_only)
            except InputError as exc:
                if str(exc) not in RECORD_ERRORS: raise
                blockers.append(str(exc))
    elif record_directory is not None: fail('CONNECTION_UNUSED_RECORD')
    proof_action = {
        'UNKNOWN':'OPERATOR_REVIEW_SETTING_IN_SELECTED_APP',
        'REQUIRED':'CURRENT_CONNECTION_CANNOT_USE_REQUIRED_PROOF',
        'NOT_REQUIRED_CONFIRMED':'CONTINUE_WITH_NAMED_ACCESS_REVIEW',
    }
    proof_review = dict(declared_requirement=proof,account_setting_observed=False,
                        dashboard_path=['App Settings','Advanced','Security','Require App Secret'],
                        next_action=proof_action[proof])
    return dict(schema='connection-preview-v1', intent_sha256=digest(intent), intent=intent, app_proof_review=proof_review,
                credential_record=record,
                status='READY_FOR_LOCAL_SCOPE_CONFIRMATION' if not blockers else 'PREPARATION_BLOCKED', blockers=blockers,
                maximum_get_attempts=2, requests=connection_requests(intent,page),
                native_access=intent['credential_source'] == 'OS_REFERENCE',
                credential_storage_write=False, oauth_or_refresh=False, live_read=False, writes=False,
                metadata_flags_are_grant_proof=False, real_connection_verified=False)


def _write(root, name, value):
    import os
    with (root / name).open('x', encoding='utf-8') as handle:
        handle.write(canonical(value) + '\n'); handle.flush(); os.fsync(handle.fileno())


def prepare_connection(directory):
    from .monitor_tools import new_directory
    root = new_directory(directory)
    _write(root, 'intent.json', blank_intent())
    return dict(status='BLANK_CONNECTION_INTENT_CREATED', path=str(root / 'intent.json'),
                authority='PREPARATION_ONLY', credential_storage_accessed=False, network_used=False)


def _assert_window(started, deadline, clock):
    current = clock()
    if not started <= current < deadline: fail('CONNECTION_REVIEW_EXPIRED')
    return current


def _credential_format(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,8192}', value): fail('CREDENTIAL_FORMAT_INVALID')


class _MemoryBroker(CredentialBroker):
    def __init__(self, credential, binding, guard=None):
        self._credential = credential; self.binding_hash = digest(asdict(binding))
        self.guard = guard

    def recheck(self, binding):
        if digest(asdict(binding)) != self.binding_hash: fail('CREDENTIAL_BINDING_CHANGED')
        if self.guard is not None: self.guard.check()

    @contextmanager
    def lease(self, binding):
        self.recheck(binding)
        yield self._credential
        self.recheck(binding)


def _get_connection_json(path, credential, before_request, *, clock=utcnow, on_rate_limit=None, time_remaining=None):
    headers = {'Authorization': 'Bearer ' + credential, 'Accept': 'application/json'}
    connection = response = None
    try:
        connection = graph_https_connection(15, ssl.create_default_context())
        before_request()
        arm_http_deadline(connection,15 if time_remaining is None else min(15,time_remaining()))
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400: fail('HTTP_REDIRECT_REFUSED')
        if response.status==429 and on_rate_limit is not None:
            on_rate_limit(cooldown(response,clock()))
            fail('RATE_LIMIT')
        body = read_json_response(response)
        if 'error' in body or not 200 <= response.status < 300:
            error = error_category(response.status,body)
            if error == 'RATE_LIMIT' and on_rate_limit is not None:
                on_rate_limit(cooldown(response,clock()))
            fail(error)
        return body
    except (TimeoutError, OSError, http.client.HTTPException):
        raise InputError(NETWORK_FAILURE) from None
    finally:
        headers.clear(); path = None
        try:
            if response is not None: response.close()
        finally:
            if connection is not None: connection.close()


def _debug(credential, before_request, *, candidate=None, clock=utcnow, on_rate_limit=None, time_remaining=None):
    """Address the fixed Debug Token endpoint. The inspected token stays in RAM."""
    path = '/v27.0/debug_token?' + urlencode({'input_token': credential if candidate is None else candidate})
    return _get_connection_json(path,credential,before_request,clock=clock,on_rate_limit=on_rate_limit,
                                time_remaining=time_remaining)


def page_grant_binding(data, page_id, permission='pages_read_engagement'):
    if 'granular_scopes' not in data:
        return 'NO_EXPLICIT_PAGE_TARGET_LIST'
    rows = data['granular_scopes']
    if type(rows) is not list or len(rows)>64 or any(type(row) is not dict for row in rows):
        fail('DEBUG_PAGE_TARGET_INVALID')
    relevant = [row for row in rows if row.get('scope')==permission]
    if len(relevant)>1:
        fail('DEBUG_PAGE_TARGET_INVALID')
    if not relevant or relevant[0].get('target_ids') is None:
        return 'NO_EXPLICIT_PAGE_TARGET_LIST'
    targets = relevant[0]['target_ids']
    if type(targets) is not list or any(
            not ((type(target) is int and 1 <= target < 10**40)
                 or (type(target) is str and re.fullmatch('[1-9][0-9]{0,39}',target)))
            for target in targets):
        fail('DEBUG_PAGE_TARGET_INVALID')
    if page_id not in {str(target) for target in targets}:
        fail('DEBUG_PAGE_TARGET_NOT_GRANTED')
    return 'NAMED_PAGE_IN_DEBUG_TARGETS'


def valid_debug_data(body):
    data = body.get('data') if type(body) is dict else None
    if type(data) is not dict: fail('DEBUG_METADATA_REQUIRED')
    if 'error' in data: fail('DEBUG_DATA_ERROR')
    valid = data.get('is_valid')
    if type(valid) is not bool: fail('DEBUG_METADATA_REQUIRED')
    if not valid: fail('DEBUG_TOKEN_INVALID')
    return data


def valid_scope_name(value):
    return type(value) is str and re.fullmatch('[a-z][a-z0-9_]{0,63}', value) is not None


def debug_expiries_and_scopes(data, now):
    times = []
    for key in ('expires_at', 'data_access_expires_at'):
        value = data.get(key)
        if type(value) is not int or not 0 < value < 253402300799: fail('DEBUG_EXPIRY_NOT_BOUNDED')
        stamp = datetime.fromtimestamp(value, timezone.utc)
        if stamp <= now: fail('CONNECTION_EXPIRED_OR_CLOCK_SKEW')
        times.append(stamp)
    if 'issued_at' in data:
        if type(data['issued_at']) is not int or not 0 <= data['issued_at'] <= now.timestamp(): fail('DEBUG_ISSUED_IN_FUTURE')
    scopes = data.get('scopes')
    if type(scopes) is not list or not 1 <= len(scopes) <= 64 or not all(map(valid_scope_name, scopes)):
        fail('DEBUG_SCOPE_INVALID')
    if len(set(scopes)) != len(scopes): fail('DEBUG_SCOPE_INVALID')
    return times, scopes


def granular_scope_observation(data):
    if 'granular_scopes' not in data:
        return dict(state='NOT_RETURNED', names=None)
    rows = data['granular_scopes']
    if (type(rows) is not list or len(rows) > 64
            or any(type(row) is not dict or not valid_scope_name(row.get('scope')) for row in rows)):
        return dict(state='UNVERIFIED', names=None)
    return dict(state='REPORTED_NAMES', names=sorted({row['scope'] for row in rows}))


def recorded_ads_scope_is_minimal(metadata):
    observation = metadata.get('granular_scope_observation')
    return (metadata['reported_scopes_exactly_ads_read'] and observation is not None
            and observation['state'] in ('NOT_RETURNED','REPORTED_NAMES')
            and set(observation['names'] or []) <= {'ads_read'})


def verified_metadata(body, intent, now, deadline, *, page=False):
    data = valid_debug_data(body)
    if data.get('app_id') != intent['app_id'] or data.get('user_id') != intent['principal_id']:
        fail('DEBUG_APP_OR_PRINCIPAL_MISMATCH')
    if data.get('profile_id') is not None or data.get('type', 'USER') != 'USER': fail('DEBUG_USER_TOKEN_REQUIRED')
    times, scopes = debug_expiries_and_scopes(data,now)
    permission = 'pages_read_engagement' if page else 'ads_read'
    if permission not in scopes:
        fail('DEBUG_PAGE_READ_NOT_GRANTED' if page else 'DEBUG_ADS_READ_NOT_GRANTED')
    if page:
        binding = page_grant_binding(data, intent['page_id'])
        return dict(app_id=intent['app_id'], principal_id=intent['principal_id'], scopes=[permission],
                    page_grant_binding=binding, token_expires_at=times[0].isoformat(),
                    data_access_expires_at=times[1].isoformat(),
                    effective_read_expires_at=min(*times, deadline).isoformat(), observed_at=now.isoformat())
    return dict(app_id=intent['app_id'], principal_id=intent['principal_id'], scopes=sorted(scopes),
                token_expires_at=times[0].isoformat(), data_access_expires_at=times[1].isoformat(),
                effective_read_expires_at=min(*times, deadline).isoformat(), observed_at=now.isoformat(),
                reported_scopes_exactly_ads_read=scopes==['ads_read'],
                granular_scope_observation=granular_scope_observation(data))


def check_connection(intent, directory, clock=utcnow, *, record_directory=None, scope_check=None, page=False):
    """Requires the owner's prior authorization and interactive local confirmation.

    No noninteractive yes flag, secret argument, stdin pipe or environment lookup.
    Tests replace the terminal/native/HTTPS boundaries; never run real credentials.
    """
    intent = strict_json(canonical(intent))
    reviewed = preview(intent, record_directory=record_directory, page=page)
    if reviewed['blockers']: fail('CONNECTION_PREPARATION_INCOMPLETE')
    guard = (RecordGuard(record_directory, intent, reviewed['credential_record']['record_sha256'])
             if intent['credential_source'] == 'OS_REFERENCE' else None)
    if scope_check is not None:
        scope_check()
    if not sys.stdin.isatty() or not sys.stdout.isatty(): fail('CONNECTION_INTERACTIVE_TERMINAL_REQUIRED')
    started = clock(); deadline = started + timedelta(minutes=5)
    print(canonical(reviewed))
    asset = 'PAGE ' + intent['page_id'] if page else intent['account_id']
    phrase = 'READ ' + asset + ' WITH APP ' + intent['app_id']
    print('仅批准此处列出的本机秘密访问及最多2次GET，不续期、不写入。终端确认不是独立身份认证。')
    if input('确认以上范围，请输入 ' + phrase + '：') != phrase: fail('CONNECTION_NOT_CONFIRMED')
    _assert_window(started, deadline, clock)
    if guard is not None: guard.check()
    if scope_check is not None:
        scope_check()
    # Approval covers this exact immutable validated intent, not a later edit.
    from .monitor_tools import new_directory
    root = new_directory(directory); _write(root, 'PLAN.json', reviewed)
    _write(root, 'CONSENT.json', dict(intent_sha256=digest(intent), observed_at=clock().isoformat(),
                expires_at=deadline.isoformat(), method='LOCAL_TERMINAL_SCOPE_CONFIRMATION',
                independently_authenticated_human=False, remote_write_authorized=False))
    reserved = 0; metadata = None; credential = None; memory = None; native_access_attempted = False
    kind = 'PAGE' if page else 'ACCOUNT'
    result = dict(status='CONNECTION_CHECK_FAILED', error_code=None, oauth_performed=False,
                  credential_stored=False, live_action_authorized=False,
                  scope=f'THIS_SINGLE_{kind}_CHECK_ONLY_NOT_REUSABLE_AUTHORIZATION')
    success_key = 'real_page_read_verified' if page else 'real_account_read_verified'
    result[success_key] = False
    plan = connection_plan(intent, page)
    initial_binding = ReadBinding(digest(asdict(plan)), intent['app_id'], intent['principal_id'],
                                  started.isoformat(), deadline.isoformat(), deadline.isoformat(), (), False, False, False)

    def checkpoint():
        if scope_check is not None: scope_check()
        if guard is not None: guard.check()
        return _assert_window(started, deadline, clock)

    def time_remaining():
        return (deadline-clock()).total_seconds()

    @contextmanager
    def lease():
        nonlocal native_access_attempted
        if intent['credential_source'] == 'SESSION_INPUT':
            _assert_window(started, deadline, clock)
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                value = getpass.getpass('本机临时Token（不回显、不保存；不要粘贴到聊天）：')
            try: yield value
            finally: value = None
        else:
            _assert_window(started, deadline, clock); guard.check(); native_access_attempted = True
            if scope_check is not None:
                scope_check()
            with NativeCredentialBroker(intent['credential_reference'], initial_binding, enabled=True).lease(initial_binding) as value:
                guard.check()
                yield value
    try:
        with lease() as credential:
            _credential_format(credential)
            checkpoint()
            _write(root, 'ATTEMPT-1.json', dict(stage='DEBUG_TOKEN', reserved_at=clock().isoformat(), may_have_reached_server=True))
            reserved = 1
            raw = _debug(credential, checkpoint, time_remaining=time_remaining)
            metadata = verified_metadata(raw, intent, checkpoint(), deadline, page=page); raw = None
            binding = ReadBinding(digest(asdict(plan)), intent['app_id'], intent['principal_id'], metadata['observed_at'],
                                  metadata['effective_read_expires_at'], deadline.isoformat(), tuple(metadata['scopes']), True, True, True)
            request_guard = guard
            if scope_check is not None:
                class ScopedGuard:
                    def check(self):
                        scope_check()
                        if guard is not None:
                            guard.check()
                request_guard = ScopedGuard()
            memory = _MemoryBroker(credential, binding, request_guard)
            _assert_window(started, deadline, clock)
            _write(root, 'ATTEMPT-2.json', dict(stage=f'NAMED_{kind}_GET', reserved_at=clock().isoformat(), may_have_reached_server=True))
            reserved = 2
            read = ReadSession(plan, GraphReadTransport(plan, binding, memory, clock=clock,
                                                        time_remaining=time_remaining)).collect(1)
            observed_at = checkpoint()
            if read['state'] != 'COMPLETE':
                fail(read['last_error'] if read['last_error'] in CHECK_ERRORS else f'{kind}_READ_REJECTED')
            binding.validate(plan, observed_at)
            row = read['rows'][0]
            if page:
                result.update(status='NAMED_PAGE_READ_PASSED', real_page_read_verified=True, page=row,
                              page_observed_at=observed_at.isoformat(), page_role_platform_verified=False)
            else:
                if not {'currency', 'timezone_name', 'account_status'} <= set(row):
                    fail('ACCOUNT_READ_REJECTED')
                result.update(status='NAMED_ACCOUNT_READ_PASSED', real_account_read_verified=True, account=row,
                              account_observed_at=observed_at.isoformat(),
                              account_ownership_basis='OPERATOR_DECLARATION_PLUS_ACCESS_NOT_INDEPENDENT_LEGAL_OWNERSHIP')
    except KeyboardInterrupt: result.update(status='INTERRUPTED', error_code='USER_INTERRUPT')
    except Exception as exc:
        code = str(exc) if type(exc) is InputError else 'CONNECTION_CHECK_FAILURE'
        result['error_code'] = code if code in CHECK_ERRORS else 'CONNECTION_CHECK_FAILURE'
    finally:
        if memory is not None: memory._credential = None
        credential = None
    result.update(metadata=metadata, get_attempts_reserved=reserved, native_access_attempted=native_access_attempted,
                  intent_sha256=digest(intent), request_count_semantics='RESERVED_ATTEMPTS_NOT_PROOF_OF_TRANSMISSION',
                  current_connection_native_acceptance='NOT_ESTABLISHED_BY_PRIOR_FIXTURE',
                  independent_human_identity_verified=False, background_monitoring_enabled=False)
    result['credential_record_sha256'] = reviewed['credential_record']['record_sha256'] if guard is not None else None
    result['local_disable_scope'] = 'THIS_RECORD_CHECKPOINTS_ONLY_NO_REMOTE_REVOCATION'
    result['minimal_scope_evidence'] = ('DEBUG_METADATA_AND_THIS_ACCOUNT_GET_ONLY' if
        not page and result['real_account_read_verified'] and recorded_ads_scope_is_minimal(metadata) else 'NOT_ESTABLISHED')
    _write(root, 'RESULT.json', result)
    files = []
    for path in sorted(root.glob('*.json')):
        data = path.read_bytes(); files.append(dict(path=path.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    _write(root, 'RECEIPT.json', dict(status=result['status'], files=files, live_action_authorized=False,
                                    credential_material_in_artifacts=False, authorization_reusable=False))
    return result
