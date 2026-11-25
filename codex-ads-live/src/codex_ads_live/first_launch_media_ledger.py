"""Protected media approvals, send ownership, and reusable platform assets."""

from contextlib import contextmanager
import os
import re
import stat

from . import first_launch_authority as authority
from .first_launch_media import HEX, IMAGE_HASH, NATIVE_ID, validate_scope
from .journal import digest
from .meta_write_mapping import utcnow
from .validation import aware_time, fail

GENERATION = re.compile(r'[0-9]{8}')


@contextmanager
def locked():
    """Serialize administrator approvals with the daemon's seal and claim."""
    import fcntl
    authority.protected(authority.STATE)
    path = authority.STATE / 'media-ledger.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail('FIRST_LAUNCH_MEDIA_LEDGER_UNTRUSTED')
        authority.protected(path)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _generations(key):
    if type(key) is not str or not HEX.fullmatch(key):
        fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')
    prefix = 'media-approval-' + key + '-'
    return sorted(path for path in authority.STATE.glob(prefix + '*.json')
                  if GENERATION.fullmatch(path.stem.removeprefix(prefix)))


def _read_approval(path, key, generation=None):
    row = authority._read(path, trust=True)
    if (row['kind'] != 'media' or row['key'] != key or digest(row['scope']) != key
            or row.get('generation') != generation or not HEX.fullmatch(row['profile_sha256'])):
        fail('FIRST_LAUNCH_MEDIA_APPROVAL_RECORD_MISMATCH')
    validate_scope(row['scope'])
    return row


def _generation_from_path(path):
    return int(path.stem.rsplit('-', 1)[-1])


def approval(key, attempt_id=None):
    legacy = authority._record('media', key)
    versions = _generations(key)
    if attempt_id is None and versions:
        return _read_approval(versions[-1], key, _generation_from_path(versions[-1]))
    for path in reversed(versions) if attempt_id is not None else ():
        row = _read_approval(path, key, _generation_from_path(path))
        if authority._expected_attempt(row) == attempt_id:
            return row
    if legacy.exists() or legacy.is_symlink():
        row = _read_approval(legacy, key)
        if attempt_id is None or authority._expected_attempt(row) == attempt_id:
            return row
    if attempt_id is not None:
        fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
    return None


def _phase(attempt_id, suffix):
    return authority.STATE / ('media-' + attempt_id + '-' + suffix + '.json')


def _present(path):
    # A malformed or dangling entry is never treated as the absence of a write.
    return os.path.lexists(path)


def observe(row, *, seal=False):
    """Only a protected ledger check may certify that a dispatch never sent."""
    attempt_id = authority._expected_attempt(row)
    receipt = authority.STATE / ('receipt-' + attempt_id + '.json')
    if _present(receipt):
        return authority._read(receipt, trust=True)
    for name in ('image_ack', 'video_finish_ack'):
        path = _phase(attempt_id, name)
        if _present(path):
            return authority._read(path, trust=True)
    started = _phase(attempt_id, 'video_started')
    if _present(started):
        return dict(authority._read(started, trust=True), status='OUTCOME_UNKNOWN')
    if any(authority.STATE.glob('media-' + attempt_id + '-*.json')):
        return dict(status='OUTCOME_UNKNOWN')
    claim = authority._claim_key(row)
    if _present(claim):
        fact = authority._read(claim, trust=True)
        if fact.get('classification') == 'NOT_SENT' and fact.get('attempt_id') == attempt_id:
            return dict(status='NOT_SENT', sealed=True, attempt_id=attempt_id, sealed_at=fact['sealed_at'])
        return dict(status='OUTCOME_UNKNOWN')
    if not seal:
        return dict(status='UNCLAIMED')
    stamped = utcnow().isoformat()
    authority._write(claim, dict(classification='NOT_SENT', attempt_id=attempt_id, sealed_at=stamped,
                                  review_sha256=row['key']))
    return dict(status='NOT_SENT', sealed=True, attempt_id=attempt_id, sealed_at=stamped)


def receipt(profile, key, attempt_id):
    with locked():
        row = approval(key, attempt_id)
        authority._match(profile, row['scope'])
        if row['profile_sha256'] != digest(profile):
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        return observe(row, seal=True)


def approve_upload(profile, row, media_file, confirm):
    """Preserve every generation. An old authority cannot open any R2 slot."""
    key = row['key']
    with locked():
        previous = approval(key)
        if previous is not None:
            authority._match(profile, previous['scope'])
            if previous['profile_sha256'] != digest(profile):
                fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
            previous_status = observe(previous)['status']
            if previous_status not in ('NOT_SENT', 'UNCLAIMED'):
                fail('FIRST_LAUNCH_MEDIA_ORIGINAL_ATTEMPT_RECOVERY_ONLY')
            if (previous_status == 'UNCLAIMED'
                    and aware_time(previous['expires_at']) > utcnow()):
                fail('FIRST_LAUNCH_AUTHORITY_APPROVAL_ALREADY_PENDING')
        generation = (previous.get('generation', 0) if previous is not None else 0) + 1
    confirm(generation, authority._expected_attempt(previous) if previous is not None else None)
    with locked():
        if approval(key) != previous:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
        if previous is not None and observe(previous, seal=True)['status'] != 'NOT_SENT':
            fail('FIRST_LAUNCH_MEDIA_ORIGINAL_ATTEMPT_RECOVERY_ONLY')
        authority._stage_media(profile, row['scope'], media_file)
        approved_at = utcnow()
        if approved_at >= aware_time(row['expires_at']):
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_EXPIRED')
        # A previous R1 approval is already sealed above. Reserve that same
        # legacy claim filename even if R1 never saw this scope.
        legacy_claim = authority._claim_key(dict(kind='media', key=key))
        if not _present(legacy_claim):
            authority._write(legacy_claim, dict(classification='R2_GENERATION_REQUIRED', scope_sha256=key))
        if generation >= 10**8:
            fail('FIRST_LAUNCH_MEDIA_APPROVAL_LIMIT')
        row = dict(row, generation=generation, approved_at=approved_at.isoformat())
        authority._write(authority.STATE / f'media-approval-{key}-{generation:08d}.json', row)
        return row


def _target(scope):
    identifier = digest(['managed-media', scope['account_id'], scope['media_type'], scope['asset_sha256']])
    base = authority.STATE / ('media-target-' + identifier + '.json')
    prefix = base.stem + '-'
    changes = sorted(path for path in authority.STATE.glob(prefix + '*.json')
                     if GENERATION.fullmatch(path.stem.removeprefix(prefix)))
    if changes and not _present(base):
        fail('FIRST_LAUNCH_MEDIA_TARGET_HISTORY_UNVERIFIED')
    return base, changes


def _owner(path):
    owner = authority._read(path, trust=True)
    attempt_id = owner['attempt_id']
    if type(attempt_id) is not str or not HEX.fullmatch(attempt_id):
        fail('FIRST_LAUNCH_MEDIA_TARGET_HISTORY_UNVERIFIED')
    if 'scope_sha256' in owner:
        return approval(owner['scope_sha256'], attempt_id)
    # R1 stored only the attempt ID. Find the corresponding protected legacy
    # approval; a missing, damaged or ambiguous association is not reusable.
    matches = []
    for item in authority.STATE.glob('media-*.json'):
        key = item.stem.removeprefix('media-')
        if HEX.fullmatch(key):
            row = _read_approval(item, key)
            if authority._expected_attempt(row) == attempt_id:
                matches.append(row)
    if len(matches) != 1:
        fail('FIRST_LAUNCH_MEDIA_TARGET_HISTORY_UNVERIFIED')
    return matches[0]


def begin_upload(row, recheck):
    """Claim the account/file before any remote write. Keep all prior owners."""
    with locked():
        recheck()
        base, changes = _target(row['scope'])
        path = changes[-1] if changes else base
        if _present(path):
            original = _owner(path)
            if observe(original)['status'] != 'NOT_SENT':
                fail('FIRST_LAUNCH_MEDIA_TARGET_ALREADY_TRACKED')
            number = _generation_from_path(changes[-1]) + 1 if changes else 1
            if number >= 10**8:
                fail('FIRST_LAUNCH_MEDIA_TARGET_HISTORY_LIMIT')
            path = base.with_name(f'{base.stem}-{number:08d}.json')
        attempt_id = authority._expected_attempt(row)
        authority._write(path, dict(attempt_id=attempt_id, scope_sha256=row['key']))
        authority._write(authority._claim_key(row), dict(attempt_id=attempt_id, review_sha256=row['key'],
                                                        consumed_at=utcnow().isoformat()))


def origin(profile, scope):
    """Resolve a positively acknowledged asset in the same enrolled scope."""
    authority._match(profile, scope)
    base, changes = _target(scope)
    path = changes[-1] if changes else base
    if not _present(path):
        fail('FIRST_LAUNCH_MEDIA_REUSE_ORIGINAL_REQUIRED')
    prior = _owner(path)
    source_scope = prior['scope']
    shared = ('workspace_sha256', 'business_id', 'app_id', 'account_id', 'page_id', 'media_type', 'asset_sha256')
    original_profile = dict(profile, plan_sha256=source_scope['plan_sha256'])
    if (prior['profile_sha256'] != digest(original_profile)
            or any(source_scope[key] != scope[key] for key in shared)
            or source_scope['plan_sha256'] == scope['plan_sha256']):
        fail('FIRST_LAUNCH_MEDIA_REUSE_SCOPE_MISMATCH')
    evidence = observe(prior)
    field, pattern, state = (('video_id', NATIVE_ID, 'PROCESSING') if scope['media_type'] == 'VIDEO'
                             else ('image_hash', IMAGE_HASH, 'ACKNOWLEDGED'))
    value = evidence.get(field)
    if evidence['status'] != state or type(value) is not str or not pattern.fullmatch(value):
        fail('FIRST_LAUNCH_MEDIA_REUSE_ORIGINAL_UNRESOLVED')
    return dict(attempt_id=authority._expected_attempt(prior), scope_sha256=prior['key'], asset={field: value})


def _binding_path(key):
    return authority.STATE / ('media-binding-' + key + '.json')


def bind_reuse(profile, row, observed, check):
    with locked():
        check()
        original = origin(profile, row['scope'])
        if (original != row['origin'] or observed != dict(original['asset'], status='READY')):
            fail('FIRST_LAUNCH_MEDIA_REUSE_SCOPE_MISMATCH')
        path = _binding_path(row['key'])
        if _present(path):
            return _read_binding(profile, row['key'])
        binding = dict(scope=row['scope'], scope_sha256=row['key'], profile_sha256=digest(profile),
                       source=original, media=observed, observed_at=utcnow().isoformat())
        authority._write(path, binding)
        return binding


def _read_binding(profile, key):
    binding = authority._read(_binding_path(key), trust=True)
    scope = binding['scope']
    validate_scope(scope)
    authority._match(profile, scope)
    source = origin(profile, scope)
    if (binding['profile_sha256'] != digest(profile) or binding['scope_sha256'] != key or digest(scope) != key
            or binding['source'] != source or binding['media'] != dict(source['asset'], status='READY')):
        fail('FIRST_LAUNCH_MEDIA_REUSE_SCOPE_MISMATCH')
    return binding


def creative_record(profile, key):
    if type(key) is not str or not HEX.fullmatch(key):
        fail('FIRST_LAUNCH_MANAGED_MEDIA_SCOPE_MISMATCH')
    with locked():
        if _present(_binding_path(key)):
            binding = _read_binding(profile, key)
            return binding['scope'], binding['media']
        row = approval(key)
        if row is None or row['profile_sha256'] != digest(profile):
            fail('FIRST_LAUNCH_MANAGED_MEDIA_SCOPE_MISMATCH')
        authority._match(profile, row['scope'])
        return row['scope'], observe(row)
