"""Local macOS authority for independently reviewed, one-use launch actions."""

import argparse
import ctypes
from dataclasses import asdict
from datetime import timedelta
import os
from pathlib import Path
import re
import socket
import stat
import sys

from .first_launch_activation_host import (
    ActivationGuard, ActivationPermit, _guard_valid, activation_attempt_id, activation_request,
)
from .first_launch_graph import _check_form
from .first_launch_host import CreationBinding, CreationPermit, HEX, NATIVE_ID, creation_attempt_id, validate_creation_binding
from .journal import canonical, digest
from .meta_write_mapping import utcnow
from .native_credentials import credential_labels
from .validation import InputError, aware_time, fail, object_only, strict_json


STATE = Path('/Library/Application Support/ModernLinc/CodexAdsLive/first-launch-v2')
SOCKET_NAME = STATE / 'authority.sock'
IMPLEMENTATION = 'MACOS_LOCAL_AUTHORITY_V2'
RPC_IMPLEMENTATION = 'MACOS_LOCAL_AUTHORITY_V2_MEDIA_R2'
WRITE_KEYCHAIN = STATE / 'private' / 'admin-write.keychain-db'
IDENTITY = ('workspace_sha256', 'plan_sha256', 'business_id', 'app_id', 'account_id', 'page_id')
READ_PURPOSES = {
    'launch': ['preview', 'one_object_readback', 'activation_controls'],
    'media': ['one_approved_media_readback'],
    'delivery-evidence': ['named_ad_creative', 'scoped_account_activity_filter'],
    'maintenance': ['named_object_controls', 'update_readback'],
    'rules': ['account_rule_list', 'named_rule_and_history'],
    'audiences': ['named_audience_lookup', 'named_lookalike_seed'],
}
LIMIT = 65536


def read_key(identity, purpose, operation_sha256=None):
    if purpose in ('media', 'delivery-evidence'):
        if type(operation_sha256) is not str or not HEX.fullmatch(operation_sha256):
            fail('FIRST_LAUNCH_EXACT_READ_OPERATION_REQUIRED')
        return digest([identity, purpose, operation_sha256])
    return digest(identity if purpose == 'launch' else [identity, purpose])


def protected(path):
    if not path.is_absolute():
        fail('FIRST_LAUNCH_AUTHORITY_PATH')
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
            fail('FIRST_LAUNCH_AUTHORITY_FILE_OWNERSHIP')


def _read(path, *, trust=False):
    if trust:
        protected(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            fail('FIRST_LAUNCH_AUTHORITY_EXPECTED_FILE')
        data = stream.read(LIMIT)
    return _decode(data)


def _decode(data):
    if not data or len(data) >= LIMIT:
        fail('FIRST_LAUNCH_AUTHORITY_MESSAGE_LIMIT')
    return strict_json(data.decode('utf-8'), max_chars=LIMIT)


def _write(path, value):
    data = (canonical(value) + '\n').encode()
    if len(data) >= LIMIT:
        fail('FIRST_LAUNCH_AUTHORITY_MESSAGE_LIMIT')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _match(profile, scope):
    if type(scope) is not dict or any(profile[key] != scope.get(key) for key in IDENTITY):
        fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')


def _eligibility_ref(value):
    if type(value) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', value):
        fail('FIRST_LAUNCH_AUTHORITY_REVIEW_EVIDENCE_REQUIRED')
    return value


def _record(kind, key):
    if kind not in ('read', 'create', 'activate', 'media', 'media-reuse', 'update', 'rule', 'audience') or type(key) is not str or not HEX.fullmatch(key):
        fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')
    return STATE / f'{kind}-{key}.json'


def _profile():
    profile = _read(STATE / 'profile.json', trust=True)
    _validate_profile(profile)
    return profile


def _validate_profile(profile):
    fields = (*IDENTITY, 'implementation', 'client_uid', 'principal_id', 'read_reference', 'write_reference')
    object_only(profile, fields, fields)
    if (profile['implementation'] != IMPLEMENTATION or type(profile['client_uid']) is not int
            or profile['client_uid'] <= 0):
        fail('FIRST_LAUNCH_LOCAL_AUTHORITY_PROFILE')
    for key in IDENTITY:
        pattern = HEX if key.endswith('_sha256') else NATIVE_ID
        if type(profile[key]) is not str or not pattern.fullmatch(profile[key]):
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_PROFILE')
    for key in ('read_reference', 'write_reference'):
        credential_labels(profile[key], profile['app_id'], profile['principal_id'])
    if profile['read_reference'] == profile['write_reference']:
        fail('FIRST_LAUNCH_INDEPENDENT_READ_REFERENCE_REQUIRED')


def _related_profile(profile, scope):
    plan = scope['plan_sha256']
    if type(plan) is not str or not HEX.fullmatch(plan):
        fail('FIRST_LAUNCH_LOCAL_AUTHORITY_PROFILE')
    related = dict(profile, plan_sha256=plan)
    _match(related, scope)
    return related


def _related_profile_path(profile):
    return STATE / ('plan-profile-' + profile['plan_sha256'] + '.json')


def _service_profile(profile, scope):
    related = _related_profile(profile, scope)
    if related != profile and _read(_related_profile_path(related), trust=True) != related:
        fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')
    return related


def _register_plan(profile, packet):
    related = _related_profile(profile, packet['identity'])
    if related == profile:
        fail('FIRST_LAUNCH_AUTHORITY_PLAN_ALREADY_REGISTERED')
    identity = {name: related[name] for name in IDENTITY}
    _confirm(dict(operation='REGISTER_PLAN', identity=identity, client_uid=related['client_uid']))
    _write(_related_profile_path(related), related)
    return dict(status='LOCAL_AUTHORITY_PLAN_REGISTERED', identity=identity)


def _claim_key(record):
    if record['kind'] == 'media' and 'generation' in record:
        return STATE / ('consumed-media-r2-' + _expected_attempt(record) + '.json')
    # Stable for the scope even when another process tries to use the same approval.
    return STATE / ('consumed-' + record['kind'] + '-' + record['key'] + '.json')


def _respond(profile, request):
    keys = ('action', 'identity', 'kind', 'key', 'attempt_id')
    object_only(request, keys, keys)
    profile = _service_profile(profile, request['identity'])
    action, kind, key = (request[name] for name in ('action', 'kind', 'key'))
    if action == 'status':
        return dict(implementation=RPC_IMPLEMENTATION, **{name: profile[name] for name in IDENTITY})
    if action == 'reuse' and kind == 'media-reuse' and request['attempt_id'] is None:
        from .first_launch_managed_write import reuse_media
        return reuse_media(profile, _usable_record(profile, kind, key))
    if action == 'reconcile' and kind == 'update':
        row = _read(_record(kind, key), trust=True)
        if row['profile_sha256'] != digest(profile) or request['attempt_id'] != _expected_attempt(row):
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        from .first_launch_managed_write import recover_update
        return recover_update(profile, row, request['attempt_id'])
    if action == 'receipt' and kind in ('rule','audience'):
        row = _read(_record(kind, key), trust=True)
        if (row['profile_sha256'] != digest(profile) or request['attempt_id'] != _expected_attempt(row)):
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        receipt = STATE / ('receipt-' + request['attempt_id'] + '.json')
        if receipt.exists():
            return _read(receipt, trust=True)
        claim = _claim_key(row)
        if not claim.exists():
            _write(claim, dict(attempt_id=request['attempt_id'], classification='NOT_SENT'))
            _write(receipt, dict(status='NOT_SENT', attempt_id=request['attempt_id']))
            return _read(receipt, trust=True)
        return dict(status='OUTCOME_UNKNOWN', attempt_id=request['attempt_id'])
    if action == 'receipt':
        if kind not in ('create', 'media') or type(request['attempt_id']) is not str or not HEX.fullmatch(request['attempt_id']):
            fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')
        if kind == 'media':
            from .first_launch_media_ledger import receipt
            return receipt(profile, key, request['attempt_id'])
        key_path = _record(kind, key)
        if not key_path.exists() and kind == 'create':
            return dict(object_id=None)
        row = _read(key_path, trust=True)
        expected = _expected_attempt(row)
        if row['profile_sha256'] != digest(profile) or request['attempt_id'] != expected:
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        receipt = STATE / ('receipt-' + expected + '.json')
        return _read(receipt, trust=True) if receipt.exists() else dict(object_id=None)
    if action not in ('get', 'execute'):
        fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')
    row = _usable_record(profile, kind, key)
    if action == 'execute':
        if kind not in ('create', 'activate', 'media', 'update', 'rule', 'audience') or request['attempt_id'] != _expected_attempt(row):
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        from .first_launch_managed_write import execute_approved
        return execute_approved(profile, row, request['attempt_id'])
    result = dict(approved_at=row['approved_at'], expires_at=row['expires_at'], evidence_ref=row['evidence_ref'],
                  scope_sha256=key, principal_id=profile['principal_id'])
    if kind == 'read':
        result['reference'] = profile['read_reference']
    elif kind in ('media', 'update', 'rule', 'audience'):
        result['attempt_id'] = _expected_attempt(row)
        if kind == 'media':
            result['generation'] = row.get('generation', 0)
    else:
        result['permit'] = row['permit']
    return result


def _expected_attempt(row):
    permit = row['permit']
    if row['kind'] == 'create':
        return creation_attempt_id(CreationBinding(**row['scope']), digest(permit))
    if row['kind'] == 'activate':
        return activation_attempt_id(row['scope'], permit['request_sha256'], permit['effect_sha256'], digest(permit))
    if row['kind'] == 'media':
        if 'generation' in row:
            return digest(['first-launch-media-attempt-v2', row['key'], row['profile_sha256'], row['generation']])
        return digest(['first-launch-media-attempt-v1', row['key'], row['profile_sha256']])
    if row['kind'] == 'media-reuse':
        return digest(['first-launch-media-reuse-v1', row['key'], row['profile_sha256'], row['origin']['attempt_id']])
    if row['kind'] in ('update', 'rule', 'audience'):
        return digest(['first-launch-' + row['kind'] + '-attempt-v1', row['key'], row['profile_sha256']])
    fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')


def match_creative_media(profile, binding, key, video):
    if key is None and video is None:
        return None
    from .first_launch_media_ledger import creative_record
    scope, observed = creative_record(profile, key)
    _match(profile, asdict(binding))
    expected_kind = 'VIDEO' if video is not None else 'IMAGE'
    if scope['media_type'] != expected_kind or scope['asset_sha256'] != binding.material_sha256:
        fail('FIRST_LAUNCH_MANAGED_MEDIA_SCOPE_MISMATCH')
    accepted = ('PROCESSING', 'READY') if video is not None else ('ACKNOWLEDGED', 'READY')
    if observed['status'] not in accepted:
        fail('FIRST_LAUNCH_MEDIA_ORIGINAL_ATTEMPT_RECOVERY_ONLY')
    return observed


def _usable_record(profile, kind, key):
    if kind == 'media':
        from .first_launch_media_ledger import approval
        row = approval(key)
    else:
        row = _read(_record(kind, key), trust=True)
    if row is None or row['kind'] != kind or row['key'] != key or row['profile_sha256'] != digest(profile):
        fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
    now = utcnow()
    if not aware_time(row['approved_at']) <= now < aware_time(row['expires_at']):
        fail('FIRST_LAUNCH_AUTHORITY_REVIEW_EXPIRED')
    if kind != 'read' and _claim_key(row).exists():
        fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_ALREADY_USED')
    return row


def _peer_uid(connection):
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        fail('FIRST_LAUNCH_AUTHORITY_PEER_UNVERIFIED')
    return uid.value


def _serve(profile):
    if os.path.lexists(SOCKET_NAME):
        fail('FIRST_LAUNCH_AUTHORITY_SOCKET_EXISTS')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_NAME))
        # getpeereid independently limits service to the enrolled local client UID.
        SOCKET_NAME.chmod(0o666)
        sock_info = SOCKET_NAME.lstat()
        try:
            server.listen(4)
            print('LOCAL_FIRST_LAUNCH_AUTHORITY_READY', flush=True)
            while True:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(2)
                    try:
                        if _peer_uid(connection) != profile['client_uid']:
                            fail('FIRST_LAUNCH_AUTHORITY_PEER_UNVERIFIED')
                        with connection.makefile('rb') as stream:
                            request = _decode(stream.readline(LIMIT))
                        response = dict(ok=True, result=_respond(profile, request))
                    except Exception:
                        response = dict(ok=False, error='FIRST_LAUNCH_AUTHORITY_SCOPE_OR_APPROVAL_UNAVAILABLE')
                    try:
                        connection.sendall((canonical(response) + '\n').encode())
                    except OSError:
                        pass
        finally:
            # Remove only the ephemeral socket created by this process.
            if os.path.lexists(SOCKET_NAME) and SOCKET_NAME.lstat().st_ino == sock_info.st_ino:
                SOCKET_NAME.unlink()


def _confirm(review):
    proof = digest(review)
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        fail('FIRST_LAUNCH_AUTHORITY_INTERACTIVE_APPROVER_REQUIRED')
    print(canonical(review), file=sys.stderr)
    phrase = 'APPROVE ' + proof
    print('Review the exact scope, then enter ' + phrase, file=sys.stderr, flush=True)
    if sys.stdin.readline().strip() != phrase:
        fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_NOT_CONFIRMED')


def _approve_media(profile, packet, args, now, evidence, expires):
    from .first_launch_media import validate_scope
    from .first_launch_media_ledger import approve_upload, locked, origin
    scope = packet['media_scope']
    validate_scope(scope)
    profile = _service_profile(profile, scope)
    key = digest(scope)
    row = dict(kind=args.kind, key=key, scope=scope, permit=None, profile_sha256=digest(profile),
               evidence_ref=evidence, approved_at=now.isoformat(), expires_at=expires.isoformat())
    review = dict(operation=args.kind, scope=scope, evidence_ref=evidence, expires_at=expires.isoformat())
    if args.kind == 'media':
        if args.media_file is None:
            fail('FIRST_LAUNCH_MEDIA_FILE_AND_TYPE_REQUIRED')
        def confirm(generation, prior):
            _confirm(dict(review, file=str(args.media_file), permission='ads_management',
                          generation=generation, prior_attempt_id=prior, allows_delivery=False))
        row = approve_upload(profile, row, args.media_file, confirm)
        return dict(status='LOCAL_AUTHORITY_REVIEW_SAVED', kind=args.kind, scope_sha256=key,
                    generation=row['generation'], attempt_id=_expected_attempt(row))

    path = _record(args.kind, key)
    with locked():
        original = origin(profile, scope)
        previous = _read(path, trust=True) if path.exists() else None
        if previous is not None and aware_time(previous['expires_at']) > now:
            fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_ALREADY_PENDING')
    _confirm(dict(review, origin=original, request_method='GET', credential_role='ENROLLED_ADMIN',
                  allows_upload=False, allows_delivery=False))
    with locked():
        if (path.exists() and _read(path, trust=True) != previous) or origin(profile, scope) != original:
            fail('FIRST_LAUNCH_MEDIA_REUSE_SCOPE_MISMATCH')
        confirmed = utcnow()
        if confirmed >= expires:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_EXPIRED')
        row.update(approved_at=confirmed.isoformat(), origin=original)
        if previous is None:
            _write(path, row)
        else:
            import tempfile
            with tempfile.TemporaryDirectory(dir=STATE) as temporary:
                staged = Path(temporary) / 'reuse-approval.json'
                _write(staged, row)
                os.replace(staged, path)
    return dict(status='LOCAL_AUTHORITY_REVIEW_SAVED', kind=args.kind, scope_sha256=key)


def _approve(profile, args):
    packet = _read(args.packet)
    kind = args.kind
    now, evidence = utcnow(), _eligibility_ref(args.evidence_ref)
    expires = now + timedelta(minutes=args.minutes)
    permit = None
    if args.media_file is not None and kind != 'media':
        fail('FIRST_LAUNCH_MEDIA_UNEXPECTED_FILE')
    if args.read_operation is not None and kind != 'read':
        fail('FIRST_LAUNCH_EXACT_READ_OPERATION_UNEXPECTED')
    if kind in ('media', 'media-reuse'):
        print(canonical(_approve_media(profile, packet, args, now, evidence, expires)))
        return
    if kind == 'read':
        profile = _service_profile(profile, packet['identity'])
        scope = {key: profile[key] for key in IDENTITY}
        operation = _read(args.read_operation) if args.read_operation is not None else None
        operation_scope = None
        if args.read_purpose == 'media' and operation is not None:
            from .first_launch_media import validate_scope
            operation_scope = operation['media_scope']
            validate_scope(operation_scope)
            _match(profile, operation_scope)
        elif args.read_purpose == 'delivery-evidence' and operation is not None:
            operation_scope = operation['plan']
            if (operation_scope['plan_sha256'] != scope['plan_sha256']
                    or operation_scope['workspace_identity_sha256'] != scope['workspace_sha256']
                    or operation_scope['ids']['account_id'] != scope['account_id']):
                fail('FIRST_LAUNCH_AUTHORITY_READ_OPERATION_SCOPE_MISMATCH')
        elif operation is not None:
            fail('FIRST_LAUNCH_EXACT_READ_OPERATION_UNEXPECTED')
        key = read_key(scope, args.read_purpose, digest(operation_scope) if operation_scope is not None else None)
        details = dict(scope=scope, operations=READ_PURPOSES[args.read_purpose],
                       permission='ads_read')
        if args.read_purpose == 'maintenance':
            details['managed_recovery_get_uses_enrolled_admin_role'] = True
        if operation_scope is not None:
            details['operation_scope'] = operation_scope
    elif kind == 'create':
        request = packet['host_approval_scope']
        binding = CreationBinding(**request['binding'])
        validate_creation_binding(binding)
        scope = asdict(binding)
        profile = _service_profile(profile, scope)
        _check_form(binding, request['form'])
        key = binding.sha256
        details = dict(binding=scope, form=request['form'], planned_fields=request['expected_object'])
        if binding.operation == 'CREATE_CAMPAIGN' and request.get('source_campaign_id') is not None:
            source = request['source_campaign_id']
            if type(source) is not str or not NATIVE_ID.fullmatch(source):
                fail('FIRST_LAUNCH_SOURCE_CAMPAIGN_REQUIRED')
            details['source_campaign_id'] = source
        if binding.operation == 'CREATE_AD':
            expected = request['expected_object']['campaign_id']
            if type(expected) is not str or not NATIVE_ID.fullmatch(expected):
                fail('FIRST_LAUNCH_EXPECTED_CAMPAIGN_REQUIRED')
            details['expected_campaign_id'] = expected
            if 'creative_asset_feed_sha256' in request:
                feed = request['creative_asset_feed_sha256']
                if type(feed) is not str or not HEX.fullmatch(feed):
                    fail('META_DYNAMIC_CREATIVE_SCOPE_CHANGED')
                details['creative_asset_feed_sha256'] = feed
        if binding.operation == 'CREATE_CREATIVE':
            from urllib.parse import parse_qs
            story = strict_json(parse_qs(request['form'], strict_parsing=True)['object_story_spec'][0])
            media_scope = request.get('media_scope_sha256')
            receipt = match_creative_media(profile, binding, media_scope, story.get('video_data'))
            if receipt is not None:
                details['managed_media'] = dict(scope_sha256=media_scope, receipt=receipt)
        permit = asdict(CreationPermit(key, evidence, digest([digest(profile), 'ads_management', key]),
                                     digest([scope, evidence]), now.isoformat(), expires.isoformat()))
    elif kind == 'audience':
        from .native_audience import create_request

        scope = packet['native_audience_scope']
        profile = _service_profile(profile, scope)
        wire = create_request(scope)
        key = digest(scope)
        if packet['scope_sha256'] != key:
            fail('NATIVE_AUDIENCE_REVIEW_SCOPE_CHANGED')
        details = dict(scope=scope, request=wire, permission='ads_management',
                       only_uses_existing_named_seed=True, no_list_upload=True)
    elif kind == 'rule':
        from .native_rules import mutation_request

        scope = packet['native_rule_scope']
        profile = _service_profile(profile, scope)
        wire = mutation_request(scope)
        key = digest(scope)
        if packet['scope_sha256'] != key:
            fail('NATIVE_RULE_REVIEW_SCOPE_CHANGED')
        details = dict(scope=scope, request=wire, account_wide_for_selected_entity_type=scope['action']=='create',
                       irreversible=scope['action']=='delete', permission='ads_management')
    elif kind == 'update':
        from .first_launch_update import request as update_request
        scope = packet['update_scope']
        profile = _service_profile(profile, scope)
        route, form = update_request(scope)
        age = now - aware_time(scope['observed_at'])
        if not timedelta(0) <= age <= timedelta(minutes=5):
            fail('FIRST_LAUNCH_UPDATE_BEFORE_OBSERVATION_STALE')
        expiry = aware_time(scope['observed_at']) + timedelta(minutes=5)
        expires = min(expires, expiry)
        key = digest(scope)
        details = dict(scope=scope, form=form, path=route, includes_enable=False)
    else:
        scope = packet['review']
        binding = scope['binding']
        profile = _service_profile(profile, dict(binding, workspace_sha256=binding['workspace_identity_sha256']))
        key = scope['review_sha256']
        if key != digest(binding) or scope['review_blockers'] or scope['preview_kind'] != 'AD':
            fail('FIRST_LAUNCH_AUTHORITY_ACTIVATION_REVIEW_REQUIRED')
        guard = ActivationGuard(**dict(packet['guard'], affected_ads=tuple(packet['guard']['affected_ads'])))
        _guard_valid(scope, guard, now)
        expires = min(expires, aware_time(guard.enforced_stop_at))
        details = dict(review=scope, exact_effect=guard.effect)
        permit = asdict(ActivationPermit('AUTHORIZE_AD_SPEND', key, digest(activation_request(scope)),
                                         guard.effect_sha256, evidence, now.isoformat(), expires.isoformat(),
                                         digest([digest(profile), 'ads_management', key])))
    path = _record(kind, key)
    if path.exists() and kind != 'read' and aware_time(_read(path, trust=True)['expires_at']) > now:
        fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_ALREADY_PENDING')
    _confirm(dict(operation=kind, details=details, evidence_ref=evidence, expires_at=expires.isoformat()))
    confirmed = utcnow()
    if confirmed >= expires:
        fail('FIRST_LAUNCH_AUTHORITY_REVIEW_EXPIRED')
    if kind == 'activate':
        _guard_valid(scope, guard, confirmed)
    if permit is not None:
        permit['approved_at'] = confirmed.isoformat()
    row = dict(kind=kind, key=key, scope=scope, permit=permit, profile_sha256=digest(profile),
               evidence_ref=evidence, approved_at=confirmed.isoformat(), expires_at=expires.isoformat())
    if kind == 'create':
        row['approved_form'] = request['form']
        for key in ('source_campaign_id', 'expected_campaign_id','creative_asset_feed_sha256'):
            if key in details:
                row[key] = details[key]
        if binding.operation == 'CREATE_CREATIVE' and media_scope is not None:
            row['media_scope_sha256'] = media_scope
    if path.exists():
        if kind != 'read' and _claim_key(row).exists():
            fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_ALREADY_USED')
        # Atomic replacement allows a fresh approval for an expired, unconsumed scope.
        import tempfile
        with tempfile.TemporaryDirectory(dir=STATE) as temporary:
            staged = Path(temporary) / 'approval.json'
            _write(staged, row)
            os.replace(staged, path)
    else:
        _write(path, row)
    print(canonical(dict(status='LOCAL_AUTHORITY_REVIEW_SAVED', kind=kind, scope_sha256=key)))


def staged_media(scope):
    return WRITE_KEYCHAIN.parent / ('media-' + digest(scope) + '.bin')


def _stage_media(profile, scope, path):
    from .first_launch_media import file_bytes, verify_bytes
    data = file_bytes(path, scope['media_type'], owner=profile['client_uid'])
    verify_bytes(scope, data)
    protected(WRITE_KEYCHAIN.parent)
    destination = staged_media(scope)
    if destination.exists():
        protected(destination)
        verify_bytes(scope, file_bytes(destination, scope['media_type'], owner=0))
        return
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('configure')
    setup.add_argument('--from-packet', type=Path, required=True)
    setup.add_argument('--client-uid', type=int, required=True)
    for field in ('principal-id', 'read-reference', 'write-reference'):
        setup.add_argument('--' + field, required=True)
    commands.add_parser('serve')
    enroll = commands.add_parser('register-plan')
    enroll.add_argument('--from-packet', type=Path, required=True)
    approve = commands.add_parser('approve')
    approve.add_argument('kind', choices=('read', 'create', 'activate', 'media', 'media-reuse', 'update', 'rule', 'audience'))
    approve.add_argument('--packet', type=Path, required=True)
    approve.add_argument('--media-file', type=Path)
    approve.add_argument('--read-purpose', choices=tuple(READ_PURPOSES), default='launch')
    approve.add_argument('--read-operation', type=Path)
    approve.add_argument('--evidence-ref', required=True)
    approve.add_argument('--minutes', type=int, choices=range(1, 16), default=10)
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'darwin' or os.geteuid() != 0:
            fail('FIRST_LAUNCH_AUTHORITY_REQUIRES_MACOS_ADMIN')
        protected(Path(__file__).resolve())
        if args.command == 'configure':
            packet = _read(args.from_packet)
            profile = dict(packet['identity'], implementation=IMPLEMENTATION, client_uid=args.client_uid,
                           principal_id=args.principal_id, read_reference=args.read_reference,
                           write_reference=args.write_reference)
            _validate_profile(profile)
            _confirm({key: value for key, value in profile.items()})
            for path in reversed(STATE.parents):
                if not path.exists():
                    path.mkdir(mode=0o755)
                protected(path)
            if not STATE.exists():
                STATE.mkdir(mode=0o755)
            protected(STATE)
            _write(STATE / 'profile.json', profile)
            print('LOCAL_FIRST_LAUNCH_AUTHORITY_CONFIGURED')
        elif args.command == 'serve':
            _serve(_profile())
        elif args.command == 'register-plan':
            print(canonical(_register_plan(_profile(), _read(args.from_packet))))
        else:
            _approve(_profile(), args)
        return 0
    except (InputError, OSError, ValueError, KeyError, TypeError):
        print('LOCAL_FIRST_LAUNCH_AUTHORITY_REQUEST_REJECTED', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
