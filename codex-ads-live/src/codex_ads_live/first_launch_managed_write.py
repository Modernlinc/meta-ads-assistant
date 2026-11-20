"""Administrator-only execution of the exact request stored during consent."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
import json
import os
import re
import stat
import subprocess
import sys
from time import monotonic
from urllib.parse import parse_qs, urlencode

from .connection import _debug, _get_connection_json, debug_expiries_and_scopes, valid_debug_data
from .first_launch_activation_host import ActivationPermit, activation_request
from .first_launch_authority import (
    STATE, WRITE_KEYCHAIN, _claim_key, _expected_attempt, _match, _usable_record, _write, protected,
)
from .first_launch_controls import observe_guard, read_account
from .first_launch_graph import GraphActivationProvider, GraphCreationProvider, ScopedGraphGrant, _check_form
from .first_launch_host import CreationBinding, CreationPermit, validate_creation_binding
from .meta_write_mapping import utcnow
from .native_credentials import credential_labels
from .validation import aware_time, fail


def _admin_credential(profile):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        fail('FIRST_LAUNCH_AUTHORITY_REQUIRES_MACOS_ADMIN')
    protected(WRITE_KEYCHAIN)
    private = WRITE_KEYCHAIN.parent
    directory_stat = private.lstat()
    if not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_mode & 0o077:
        fail('FIRST_LAUNCH_WRITE_KEYCHAIN_NOT_PRIVATE')
    file_stat = WRITE_KEYCHAIN.lstat()
    if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_mode & 0o077 or file_stat.st_nlink != 1):
        fail('FIRST_LAUNCH_WRITE_KEYCHAIN_NOT_PRIVATE')
    # macOS ACL entries can grant access beyond POSIX mode. Reject any
    # extended ACL on the dedicated directory or keychain, including inherited
    # entries. No user input controls paths, options, locale or command.
    for path in (private, WRITE_KEYCHAIN):
        mode = subprocess.run(['/bin/ls', '-dle', str(path)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                              env={'LC_ALL': 'C'}, check=True, timeout=3)
        lines = mode.stdout.splitlines()
        if len(lines) != 1 or not lines[0].split() or b'+' in lines[0].split()[0]:
            fail('FIRST_LAUNCH_WRITE_KEYCHAIN_NOT_PRIVATE')
    service, account = credential_labels(profile['write_reference'], profile['app_id'], profile['principal_id'])
    # Explicit private keychain. Never fall back to a customer's login/default keychain.
    try:
        found = subprocess.run(
            ['/usr/bin/security', 'find-generic-password', '-s', service, '-a', account, '-w', str(WRITE_KEYCHAIN)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, check=True, timeout=10,
        )
        token = found.stdout.rstrip(b'\r\n').decode('ascii')
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise ValueError('FIRST_LAUNCH_MANAGED_CREDENTIAL_UNAVAILABLE') from None
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,8192}', token):
        fail('FIRST_LAUNCH_MANAGED_CREDENTIAL_INVALID')
    return token


class _ManagedAttempt:
    """Per-call authority, never sent over the client socket."""

    def __init__(self, profile, row, attempt_id):
        self.profile = profile
        self.row = row
        self.attempt_id = attempt_id
        self.scope = row['scope'] if row['kind'] == 'create' else row['scope']['binding']
        self._token = None
        self._scopes = ()
        self.deadline = min(aware_time(row['expires_at']), utcnow() + timedelta(seconds=90))
        self._monotonic_end = monotonic() + 90

    def _remaining(self):
        return min(15, (self.deadline - utcnow()).total_seconds(), self._monotonic_end - monotonic())

    def _assert_current(self):
        if self._remaining() <= 0:
            fail('FIRST_LAUNCH_MANAGED_CREDENTIAL_EXPIRED')
        row = _usable_record(self.profile, self.row['kind'], self.row['key'])
        if row != self.row or _expected_attempt(row) != self.attempt_id:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')

    def _get(self, path):
        return _get_connection_json(path, self._token, self._assert_current, time_remaining=self._remaining)

    def _check_account_role(self):
        tasks = read_account(self._get, self.scope).get('user_tasks')
        if type(tasks) is not list or not any(task in ('ADVERTISE', 'MANAGE') for task in tasks):
            fail('FIRST_LAUNCH_LOCAL_ACCOUNT_WRITE_ROLE_UNVERIFIED')

    def open(self):
        self._assert_current()
        self._token = _admin_credential(self.profile)
        body = _debug(self._token, self._assert_current, time_remaining=self._remaining)
        data = valid_debug_data(body)
        if (data.get('app_id'), data.get('user_id'), data.get('type'), data.get('profile_id')) != (
            self.profile['app_id'], self.profile['principal_id'], 'USER', None,
        ):
            fail('FIRST_LAUNCH_LOCAL_APP_OR_USER_MISMATCH')
        expiries, scopes = debug_expiries_and_scopes(data, utcnow())
        if 'ads_management' not in scopes:
            fail('FIRST_LAUNCH_LOCAL_WRITE_SCOPE_NOT_GRANTED')
        self.deadline = min(self.deadline, *expiries)
        self._scopes = tuple(sorted(scopes))
        self._check_account_role()

    def close(self):
        self._token = None

    def current_permit(self, binding):
        if asdict(binding) != self.row['scope']:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
        self._assert_current()
        return CreationPermit(**self.row['permit'])

    def current_guard(self, review):
        if review != self.row['scope']:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
        self._assert_current()
        # The administrator process reads actual effects again before the single dispatch.
        return observe_guard(review, self._get, self.deadline)

    def activation_permit(self, review, request_sha256, effect_sha256):
        self._assert_current()
        permit = ActivationPermit(**self.row['permit'])
        if (review != self.row['scope'] or permit.request_sha256 != request_sha256
                or permit.effect_sha256 != effect_sha256):
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')
        return permit

    def verify_image(self, binding, form):
        if binding.operation != 'CREATE_CREATIVE':
            return
        story = parse_qs(form, strict_parsing=True)['object_story_spec'][0]
        image = json.loads(story)['link_data']['image_hash']
        query = urlencode(dict(fields='hash', hashes=json.dumps([image]), limit=2))
        row = self._get('/v27.0/act_' + binding.account_id + '/adimages?' + query)
        data = row.get('data')
        if (type(data) is not list or len(data) != 1 or type(data[0]) is not dict
                or data[0].get('hash') != image or (row.get('paging') or {}).get('next')):
            fail('FIRST_LAUNCH_LOCAL_ACCOUNT_IMAGE_UNVERIFIED')

    @contextmanager
    def _claim(self, key, permit, attempt_id):
        if (key != self.row['key'] or attempt_id != self.attempt_id or asdict(permit) != self.row['permit']):
            fail('FIRST_LAUNCH_AUTHORITY_ATTEMPT_MISMATCH')
        self._assert_current()
        # O_EXCL plus fsync; a crash at any later point leaves an unresolved,
        # already-consumed attempt. Retrying the POST is never authorized.
        _write(_claim_key(self.row), dict(attempt_id=attempt_id, review_sha256=key, consumed_at=utcnow().isoformat()))
        yield ScopedGraphGrant(key, attempt_id, permit.write_grant_sha256, self._scopes,
                               self.deadline.isoformat(), 'Bearer ' + self._token)

    def claim_once(self, binding, permit, attempt_id):
        return self._claim(binding.sha256, permit, attempt_id)

    def claim_activation_once(self, review, permit, attempt_id):
        return self._claim(review['review_sha256'], permit, attempt_id)


def execute_approved(profile, row, attempt_id):
    """Only kind/key/attempt select this record. No client URL, form or token is accepted."""
    _match(profile, row['scope'] if row['kind'] == 'create' else
           dict(row['scope']['binding'], workspace_sha256=row['scope']['binding']['workspace_identity_sha256']))
    attempt = _ManagedAttempt(profile, row, attempt_id)
    try:
        if row['kind'] == 'create':
            binding = CreationBinding(**row['scope'])
            validate_creation_binding(binding)
            form = row['approved_form']
            _check_form(binding, form)
            attempt.open()
            sender = GraphCreationProvider(attempt, None, pre_send=attempt.verify_image)
            object_id = sender.send_paused_creation(binding, attempt.current_permit(binding), attempt_id, form)
            result = dict(object_id=object_id)
        else:
            review = row['scope']
            if row['kind'] != 'activate' or review['review_blockers'] or review['preview_kind'] != 'AD':
                fail('FIRST_LAUNCH_AUTHORITY_OPERATION_REFUSED')
            request = activation_request(review)
            attempt.open()
            guard = attempt.current_guard(review)
            permit = ActivationPermit(**row['permit'])
            acknowledged = GraphActivationProvider(attempt).send_activation(review, guard, permit, attempt_id, request)
            result = dict(acknowledged=acknowledged)
        _write(STATE / ('receipt-' + attempt_id + '.json'), result)
        return result
    finally:
        attempt.close()
