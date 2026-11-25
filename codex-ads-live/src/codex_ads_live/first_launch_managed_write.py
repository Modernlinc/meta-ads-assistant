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
from urllib.parse import parse_qs

from .connection import _debug, _get_connection_json, debug_expiries_and_scopes, valid_debug_data
from .first_launch_activation_host import ActivationPermit, activation_request
from .first_launch_authority import (
    STATE, WRITE_KEYCHAIN, _claim_key, _expected_attempt, _match, _usable_record, _write, match_creative_media,
    protected, read_key, staged_media, _read,
)
from .first_launch_controls import observe_guard, read_account
from .first_launch_graph import GraphActivationProvider, GraphCreationProvider, ScopedGraphGrant, _check_form, _send_scoped_post
from .first_launch_host import CreationBinding, CreationPermit, validate_creation_binding
from .journal import digest
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

    def __init__(self, profile, row, attempt_id, recovery_read=None):
        self.profile = profile
        self.row = row
        self.attempt_id = attempt_id
        self.scope = row['scope']['binding'] if row['kind'] == 'activate' else row['scope']
        self._token = None
        self._scopes = ()
        self.recovery_read = recovery_read
        expires = recovery_read['expires_at'] if recovery_read is not None else row['expires_at']
        self.deadline = min(aware_time(expires), utcnow() + timedelta(seconds=90))
        self._monotonic_end = monotonic() + 90

    def _remaining(self):
        return min(15, (self.deadline - utcnow()).total_seconds(), self._monotonic_end - monotonic())

    def _assert_current(self):
        if self._remaining() <= 0:
            fail('FIRST_LAUNCH_MANAGED_CREDENTIAL_EXPIRED')
        kind, key, expected = self.row['kind'], self.row['key'], self.row
        if self.recovery_read is not None:
            kind, key, expected = 'read', self.recovery_read['key'], self.recovery_read
        row = _usable_record(self.profile, kind, key)
        if row != expected or _expected_attempt(self.row) != self.attempt_id:
            fail('FIRST_LAUNCH_AUTHORITY_REVIEW_SCOPE_CHANGED')

    def _get(self, path):
        return _get_connection_json(path, self._token, self._assert_current, time_remaining=self._remaining)

    def _check_account_role(self):
        tasks = read_account(self._get, self.scope,
                             check_currency=self.row['kind'] not in ('media', 'media-reuse')).get('user_tasks')
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
        from .first_launch_media import read_media
        story = parse_qs(form, strict_parsing=True)['object_story_spec'][0]
        story = json.loads(story)
        video = story.get('video_data')
        approved = match_creative_media(self.profile, binding, self.row.get('media_scope_sha256'), video)
        if video is not None:
            if (approved.get('video_id') != video.get('video_id')
                    or read_media(binding.account_id, approved, self._get)['status'] != 'READY'):
                fail('FIRST_LAUNCH_MANAGED_VIDEO_NOT_READY')
            image = video['image_hash']
        else:
            image = story['link_data']['image_hash']
            if approved is not None and approved.get('image_hash') != image:
                fail('FIRST_LAUNCH_MANAGED_IMAGE_MISMATCH')
        read_media(binding.account_id, {'image_hash': image}, self._get)

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

    def upload_file(self):
        from .first_launch_media import file_bytes, post_media, upload, verify_bytes
        from .first_launch_media_ledger import begin_upload
        scope = self.scope
        path = staged_media(scope)
        protected(path)
        data = file_bytes(path, scope['media_type'], owner=0)
        verify_bytes(scope, data)
        begin_upload(self.row, self._assert_current)

        def record(phase, evidence):
            sequence = '-' + str(evidence['index']) if 'index' in evidence else ''
            node = STATE / ('media-' + self.attempt_id + '-' + phase.lower() + sequence + '.json')
            _write(node, evidence)

        def send(host, path, fields, part):
            return post_media(host, path, fields, part, self._token, self._remaining)

        return upload(scope, data, send, record)

    def update_object(self):
        from .first_launch_update import _target, current_value, observe, request
        scope = self.scope
        target = digest([scope['account_id'], _target(scope)[1]])
        prefix = 'update-target-' + target + '-'
        for prior in STATE.glob(prefix + '*.json'):
            other = _read(prior, trust=True)['attempt_id']
            if not (STATE / ('update-applied-' + other + '.json')).exists():
                fail('FIRST_LAUNCH_UPDATE_TARGET_HAS_UNRESOLVED_ATTEMPT')
        path, form = request(scope)
        key = self.row['key']
        grant_sha = digest([key, self.attempt_id, 'ads_management'])

        def recheck():
            self._assert_current()
            actual = observe(scope, self._get, preflight=True)
            if current_value(scope, actual) != scope['before']:
                fail('FIRST_LAUNCH_UPDATE_BEFORE_STATE_CHANGED')
            return aware_time(self.row['approved_at']), self.deadline

        @contextmanager
        def claim():
            self._assert_current()
            _write(STATE / (prefix + self.attempt_id + '.json'), dict(attempt_id=self.attempt_id))
            _write(_claim_key(self.row), dict(attempt_id=self.attempt_id, review_sha256=key,
                                             consumed_at=utcnow().isoformat()))
            yield ScopedGraphGrant(key, self.attempt_id, grant_sha, self._scopes,
                                   self.deadline.isoformat(), 'Bearer ' + self._token)

        reply = _send_scoped_post(path, form, clock=utcnow, expires_at=self.deadline, recheck=recheck,
                                  claim=claim, binding_sha256=key, attempt_id=self.attempt_id,
                                  write_grant_sha256=grant_sha)
        return dict(acknowledged=type(reply) is dict and reply.get('success') is True)


def recover_update(profile, row, attempt_id):
    from .first_launch_authority import IDENTITY
    from .first_launch_update import current_value, observe
    claim = _claim_key(row)
    settled = STATE / ('update-applied-' + attempt_id + '.json')
    if settled.exists():
        return _read(settled, trust=True)
    if not claim.exists():
        # The daemon handles one request at a time. Seal the original request
        # against a later dispatch before reporting that it did not send.
        _write(claim, dict(attempt_id=attempt_id, classification='NOT_SENT'))
        _write(settled, dict(status='NOT_SENT', attempt_id=attempt_id))
        return dict(status='NOT_SENT')
    if _read(claim, trust=True).get('classification') == 'NOT_SENT':
        _write(settled, dict(status='NOT_SENT', attempt_id=attempt_id))
        return dict(status='NOT_SENT')
    scope = row['scope']
    _match(profile, scope)
    key = read_key({name: profile[name] for name in IDENTITY}, 'maintenance')
    approved_read = _usable_record(profile, 'read', key)
    attempt = _ManagedAttempt(profile, row, attempt_id, recovery_read=approved_read)
    try:
        attempt.open()
        observed = observe(scope, attempt._get, preflight=False)
        if current_value(scope, observed) != scope['after']:
            return dict(status='OUTCOME_UNKNOWN', observation=observed)
        result = dict(status='READBACK_MATCHED', observation=observed, observed_at=utcnow().isoformat(),
                      attempt_id=attempt_id)
        _write(settled, result)
        return result
    finally:
        attempt.close()


def reuse_media(profile, row):
    from .first_launch_media import read_media
    from .first_launch_media_ledger import bind_reuse, locked, origin
    scope = row['scope']
    _match(profile, scope)
    attempt = _ManagedAttempt(profile, row, _expected_attempt(row))
    try:
        with locked():
            original = origin(profile, scope)
            if original != row['origin']:
                fail('FIRST_LAUNCH_MEDIA_REUSE_SCOPE_MISMATCH')
        attempt.open()
        observed = read_media(scope['account_id'], original['asset'], attempt._get)
        result = dict(state=observed['status'], media=observed, scope_sha256=row['key'],
                      origin_attempt_id=original['attempt_id'], upload_sent=False)
        if observed['status'] == 'READY':
            binding = bind_reuse(profile, row, observed, attempt._assert_current)
            result.update(state='REUSE_READY', bound_at=binding['observed_at'])
        return result
    finally:
        attempt.close()


def execute_approved(profile, row, attempt_id):
    """Only kind/key/attempt select this record. No client URL, form or token is accepted."""
    scope = row['scope']
    if row['kind'] == 'activate':
        scope = dict(scope['binding'], workspace_sha256=scope['binding']['workspace_identity_sha256'])
    _match(profile, scope)
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
        elif row['kind'] == 'media':
            attempt.open()
            result = attempt.upload_file()
        elif row['kind'] == 'update':
            attempt.open()
            result = attempt.update_object()
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
