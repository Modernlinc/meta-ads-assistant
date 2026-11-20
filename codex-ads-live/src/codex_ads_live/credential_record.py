"""Non-secret enrollment instructions and irreversible local disable records.

Never reads or writes the OS credential store. The owner provisions that store
separately. This local journal is an accidental-corruption boundary, not a
signature, global revocation service or protection against a hostile local user.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from .journal import Journal, digest
from .validation import InputError, aware_time, fail, object_only, strict_json

KEYS = ('schema', 'reference', 'app_id', 'principal_id', 'platform', 'created_at', 'state', 'disabled_at')
ERRORS = {'CREDENTIAL_RECORD_REQUIRED', 'CREDENTIAL_RECORD_INVALID', 'CREDENTIAL_RECORD_BUSY',
          'CREDENTIAL_RECORD_DISABLED', 'CREDENTIAL_RECORD_BINDING_MISMATCH',
          'CREDENTIAL_RECORD_PLATFORM_MISMATCH', 'CREDENTIAL_RECORD_CHANGED'}


def utcnow(): return datetime.now(timezone.utc)


def validate(value):
    object_only(value, KEYS, KEYS)
    if value['schema'] != 'credential-record-v1': fail('CREDENTIAL_RECORD_INVALID')
    if type(value['reference']) is not str or not re.fullmatch(r'conn_[0-9a-f]{32}', value['reference']):
        fail('CREDENTIAL_RECORD_INVALID')
    for key in ('app_id', 'principal_id'):
        if type(value[key]) is not str or not re.fullmatch(r'[0-9]{1,40}', value[key]): fail('CREDENTIAL_RECORD_INVALID')
    if value['platform'] not in ('macos', 'windows'): fail('CREDENTIAL_RECORD_INVALID')
    created = aware_time(value['created_at'])
    if value['state'] == 'PREPARED':
        if value['disabled_at'] is not None: fail('CREDENTIAL_RECORD_INVALID')
    elif value['state'] == 'DISABLED_LOCAL':
        if aware_time(value['disabled_at']) < created: fail('CREDENTIAL_RECORD_INVALID')
    else: fail('CREDENTIAL_RECORD_INVALID')


def public_record(value):
    validate(value)
    return dict(record=value, record_sha256=digest(value),
                service_or_target='ModernLinc.CodexAdsLive.' + value['reference'],
                account_label=value['app_id'] + '.' + value['principal_id'],
                owner_entry='NOT_OBSERVED', credential_store_accessed=False,
                credential_presence_verified=False, meta_revocation_verified=False,
                check_authorization_granted=False)


def prepare_record(directory, app_id, principal_id, platform, *, clock=utcnow):
    value = dict(schema='credential-record-v1', reference='conn_' + uuid4().hex,
                 app_id=app_id, principal_id=principal_id, platform=platform,
                 created_at=clock().isoformat(), state='PREPARED', disabled_at=None)
    validate(value)
    from .monitor_tools import new_directory
    root = new_directory(directory)
    journal = Journal(root, mode='READ_ONLY')
    with journal.locked(): journal.save(value, validate)
    return public_record(value)


def _open(directory, *, existing_only=False):
    from .local_paths import checked_path
    root = checked_path(directory, link_error='CREDENTIAL_RECORD_INVALID', volume_error='CREDENTIAL_RECORD_INVALID')
    marker = root / 'operator-state.json'
    if not root.is_dir() or marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1000:
        fail('CREDENTIAL_RECORD_INVALID')
    return Journal(root, mode='READ_ONLY', existing_only=existing_only)


def _load(journal):
    # Only creation and optional disable exist. Never recover a damaged disabled
    # tail by treating the earlier PREPARED snapshot as an active record.
    snapshots = journal.snapshots()
    if not 1 <= len(snapshots) <= 2: fail('CREDENTIAL_RECORD_INVALID')
    values = []
    for index, path in enumerate(snapshots, start=1):
        if path.name != f'snapshot-{index:010}.json' or path.stat().st_size > 4096:
            fail('CREDENTIAL_RECORD_INVALID')
        envelope = strict_json(path.read_text(encoding='utf-8'))
        object_only(envelope, ('payload', 'sha256'), ('payload', 'sha256'))
        value = envelope['payload']; validate(value)
        if envelope['sha256'] != digest(value): fail('CREDENTIAL_RECORD_INVALID')
        if value['state'] != ('PREPARED' if index == 1 else 'DISABLED_LOCAL'): fail('CREDENTIAL_RECORD_INVALID')
        if values and any(value[key] != values[0][key] for key in KEYS if key not in ('state', 'disabled_at')):
            fail('CREDENTIAL_RECORD_INVALID')
        values.append(value)
    return values[-1]


def _safe_failure(exc):
    code = str(exc) if type(exc) is InputError else ''
    raise InputError('CREDENTIAL_RECORD_BUSY' if code == 'STATE_BUSY' else 'CREDENTIAL_RECORD_INVALID') from None


def read_record(directory, *, existing_only=False):
    try:
        journal = _open(directory,existing_only=existing_only)
        with journal.locked(existing_only=existing_only): return _load(journal)
    except (InputError, OSError, ValueError, TypeError) as exc: _safe_failure(exc)


def disable_record(directory, *, clock=utcnow):
    try:
        journal = _open(directory)
        with journal.locked():
            value = _load(journal)
            if value['state'] != 'DISABLED_LOCAL':
                value = {**value, 'state': 'DISABLED_LOCAL', 'disabled_at': clock().isoformat()}
                journal.save(value, validate)
            return public_record(value)
    except (InputError, OSError, ValueError, TypeError) as exc: _safe_failure(exc)


class RecordGuard:
    """Recheck one exact local record, without holding a lock across network IO."""
    def __init__(self, directory, intent, expected_hash):
        self.directory = Path(directory).absolute()
        self.app_id = intent['app_id']; self.principal_id = intent['principal_id']
        self.reference = intent['credential_reference']; self.expected_hash = expected_hash

    def check(self, *, existing_only=False):
        value = read_record(self.directory,existing_only=existing_only)
        if value['state'] == 'DISABLED_LOCAL': fail('CREDENTIAL_RECORD_DISABLED')
        if (value['app_id'], value['principal_id'], value['reference']) != (self.app_id, self.principal_id, self.reference):
            fail('CREDENTIAL_RECORD_BINDING_MISMATCH')
        if {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform) != value['platform']:
            fail('CREDENTIAL_RECORD_PLATFORM_MISMATCH')
        if digest(value) != self.expected_hash: fail('CREDENTIAL_RECORD_CHANGED')
        return value
