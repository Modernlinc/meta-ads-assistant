"""Load the fixed macOS authority and assemble the existing Graph adapters."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket
import stat
import subprocess
import sys
from time import monotonic
from uuid import uuid4

from .connection import _debug, _get_connection_json, debug_expiries_and_scopes, valid_debug_data
from .first_launch_activation_host import ActivationPermit
from .first_launch_authority import IDENTITY, IMPLEMENTATION, LIMIT, SOCKET_NAME, STATE, _decode, protected
from .first_launch_graph import GraphActivationProvider
from .first_launch_controls import observe_guard
from .first_launch_host import CreationPermit
from .first_launch_preview import GraphFirstLaunchPreview
from .first_launch_readback import CreationReadTarget, GraphCreationReadback
from .http_read import CredentialBroker, GraphReadTransport, ReadBinding
from .journal import canonical, digest
from .meta_write_mapping import utcnow
from .native_credentials import NativeCredentialBroker
from .validation import InputError, aware_time, fail


def scope_from_launch(launch):
    return dict(workspace_sha256=launch['workspace_identity_sha256'],
                **{key: launch[key] for key in IDENTITY if key != 'workspace_sha256'})


def load_host(workspace, directory):
    from .first_launch_progress import progress
    return LocalFirstLaunchHost(scope_from_launch(progress(workspace, directory)))


class _LeasedRead(CredentialBroker):
    def __init__(self, host, lease, binding):
        self.host = host
        self.authorization = lease
        self.binding = binding

    @contextmanager
    def lease(self, binding):
        if binding != self.binding:
            fail('FIRST_LAUNCH_LOCAL_READ_SCOPE_CHANGED')
        self.recheck(binding)
        yield self.authorization['token']
        self.recheck(binding)

    def recheck(self, binding):
        if binding != self.binding:
            fail('FIRST_LAUNCH_LOCAL_READ_SCOPE_CHANGED')
        self.host.assert_lease(self.authorization, 'read', digest(self.host.identity))


class LocalFirstLaunchHost:
    def __init__(self, identity):
        if set(identity) != set(IDENTITY):
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')
        self.identity = identity
        self.leases = {}
        self.guard = None
        self.rpc('status')

    def rpc(self, action, kind=None, key=None, attempt_id=None):
        try:
            if sys.platform != 'darwin':
                fail('FIRST_LAUNCH_LOCAL_AUTHORITY_PLATFORM_UNSUPPORTED')
            protected(STATE)
            node = SOCKET_NAME.lstat()
            if node.st_uid != 0 or not stat.S_ISSOCK(node.st_mode):
                fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SOCKET_UNTRUSTED')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(120 if action in ('execute', 'reconcile') else 3)
                client.connect(str(SOCKET_NAME))
                message = dict(action=action, identity=self.identity, kind=kind, key=key, attempt_id=attempt_id)
                client.sendall((canonical(message) + '\n').encode())
                with client.makefile('rb') as stream:
                    response = _decode(stream.readline(LIMIT))
        except InputError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise InputError('FIRST_LAUNCH_LOCAL_AUTHORITY_NOT_CONFIGURED_OR_RUNNING') from None
        if type(response) is not dict or response.get('ok') is not True or type(response.get('result')) is not dict:
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_APPROVAL_UNAVAILABLE')
        result = response['result']
        if action == 'status' and result != dict(implementation=IMPLEMENTATION, **self.identity):
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')
        return result

    def _scope(self, value):
        if any(value.get(key) != expected for key,expected in self.identity.items()):
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')

    def _activation_scope(self, review):
        binding = review['binding']
        self._scope(dict(binding, workspace_sha256=binding['workspace_identity_sha256']))
        return binding

    def _get(self, path, lease, *, assert_current=None):
        deadline = monotonic() + 15
        def check():
            if utcnow() >= lease['deadline'] or monotonic() >= deadline:
                fail('FIRST_LAUNCH_LOCAL_LEASE_EXPIRED')
            if assert_current is not None:
                assert_current()
        return _get_connection_json(path, lease['token'], check, time_remaining=lambda: min(
            deadline - monotonic(), (lease['deadline'] - utcnow()).total_seconds()))

    def _validated_lease(self, kind, key):
        if kind != 'read':
            fail('FIRST_LAUNCH_INDEPENDENT_READ_SCOPE_REQUIRED')
        row = self.rpc('get', kind, key)
        if row.get('scope_sha256') != key:
            fail('FIRST_LAUNCH_LOCAL_AUTHORITY_SCOPE_MISMATCH')
        previous = self.leases.get((kind,key))
        if previous is not None and previous['record'] == row and utcnow() < previous['deadline']:
            return previous
        now = utcnow()
        deadline = min(aware_time(row['expires_at']), now + timedelta(seconds=120))
        identifier = self.identity['app_id']
        binding = ReadBinding(key, identifier, row['principal_id'], now.isoformat(),
                              deadline.isoformat(), deadline.isoformat(), (), False, False, False)
        broker = NativeCredentialBroker(row['reference'], binding, enabled=True)
        with broker.lease(binding) as token:
            expires = lambda: (deadline - utcnow()).total_seconds()
            check = lambda: self._assert_record(row, kind, key, deadline)
            check()
            body = _debug(token, check, time_remaining=expires)
            data = valid_debug_data(body)
            if (data.get('app_id'), data.get('user_id'), data.get('type', 'USER'), data.get('profile_id')) != (
                    identifier, row['principal_id'], 'USER', None):
                fail('FIRST_LAUNCH_LOCAL_APP_OR_USER_MISMATCH')
            times, scopes = debug_expiries_and_scopes(data, utcnow())
            if 'ads_read' not in scopes or 'ads_management' in scopes:
                fail('FIRST_LAUNCH_INDEPENDENT_READ_SCOPE_REQUIRED')
            lease = dict(record=row, token=token, deadline=min(deadline, *times), scopes=tuple(sorted(scopes)))
        self.leases[kind,key] = lease
        return lease

    def _assert_record(self, row, kind, key, deadline):
        if utcnow() >= deadline or self.rpc('get', kind, key) != row:
            fail('FIRST_LAUNCH_LOCAL_LEASE_CHANGED')

    def assert_lease(self, lease, kind, key):
        self._assert_record(lease['record'], kind, key, lease['deadline'])

    def authorize_creation(self, binding):
        self._scope(asdict(binding))
        return CreationPermit(**self.rpc('get', 'create', binding.sha256)['permit'])

    def recheck_creation(self, binding, permit):
        return self.authorize_creation(binding)

    def send_paused_creation(self, binding, permit, attempt_id, form_body):
        # Only the protected authority's approved request can be sent to Meta.
        self._scope(asdict(binding))
        return self.rpc('execute', 'create', binding.sha256, attempt_id)['object_id']

    def read_creation(self, binding, attempt_id, object_id):
        return GraphCreationReadback(self._resolve_target, self._creation_transport)(binding, attempt_id, object_id)

    def read_transport(self, plan):
        if plan.account_id != self.identity['account_id']:
            fail('FIRST_LAUNCH_LOCAL_READ_SCOPE_CHANGED')
        key = digest(self.identity)
        lease = self._validated_lease('read', key)
        record = lease['record']
        binding = ReadBinding(digest(asdict(plan)), self.identity['app_id'], record['principal_id'],
                              record['approved_at'], lease['deadline'].isoformat(),
                              record['expires_at'], lease['scopes'], True, True, True)
        return GraphReadTransport(plan, binding, _LeasedRead(self, lease, binding))

    def _resolve_target(self, binding, attempt_id, acknowledged_id):
        self._scope(asdict(binding))
        if acknowledged_id is None:
            acknowledged_id = self.rpc('receipt', 'create', binding.sha256, attempt_id)['object_id']
        if acknowledged_id is not None:
            return CreationReadTarget(binding.sha256, attempt_id, acknowledged_id, 'EV-' + attempt_id[:32])
        return None

    def _creation_transport(self, binding, plan):
        self._scope(asdict(binding))
        return self.read_transport(plan)

    def creation(self):
        return self

    def _preview_transport(self, plan):
        if plan.app_id != self.identity['app_id'] or plan.workspace_sha256 != self.identity['workspace_sha256']:
            fail('FIRST_LAUNCH_LOCAL_READ_SCOPE_CHANGED')
        return self.read_transport(plan)

    def preview(self):
        return GraphFirstLaunchPreview(self._preview_transport, _preview_in_browser)

    def activation(self):
        return self

    def current_guard(self, review):
        self._activation_scope(review)
        key = digest(self.identity)
        lease = self._validated_lease('read', key)
        if (self.guard is not None and self.guard.review_sha256 == review['review_sha256']
                and (utcnow() - aware_time(self.guard.observed_at)).total_seconds() < 12):
            return self.guard
        def get(path):
            return self._get(path, lease, assert_current=lambda: self.assert_lease(lease, 'read', key))
        self.guard = observe_guard(review, get, lease['deadline'])
        return self.guard

    def activation_permit(self, review, request_sha256, effect_sha256):
        self._activation_scope(review)
        permit = ActivationPermit(**self.rpc('get', 'activate', review['review_sha256'])['permit'])
        if (permit.request_sha256, permit.effect_sha256) != (request_sha256, effect_sha256):
            fail('FIRST_LAUNCH_LOCAL_APPROVAL_CHANGED')
        return permit

    def send_activation(self, review, guard, permit, attempt_id, request):
        self._activation_scope(review)
        return self.rpc('execute', 'activate', review['review_sha256'], attempt_id)['acknowledged']

    def read_activation(self, review, attempt_id):
        return GraphActivationProvider(self).read_activation(review, attempt_id)

    def activation_read_access(self, review, plan):
        self._activation_scope(review)
        return self.read_transport(plan), 'EV-' + plan.attempt_id[:32]


def _preview_in_browser(plan, frame):
    """Present the validated frame in a short-lived, one-use local browser page."""
    nonce = uuid4().hex
    path = '/' + nonce
    body = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Meta ad preview</title>'
            + frame + '</html>').encode()

    class PreviewPage(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != path or self.headers.get('Host') != host or self.server.presented:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; frame-src https://www.facebook.com")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.server.presented = True

    with HTTPServer(('127.0.0.1', 0), PreviewPage) as server:
        host = '127.0.0.1:' + str(server.server_port)
        server.timeout = 1
        server.presented = False
        subprocess.run(['/usr/bin/open', 'http://' + host + path], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=5)
        until = monotonic() + 15
        while not server.presented and monotonic() < until:
            server.handle_request()
        if not server.presented:
            fail('FIRST_LAUNCH_PREVIEW_UNAVAILABLE')
    return 'EV-' + digest(dict(preview=asdict(plan), presented_at=utcnow().isoformat()))[:32]
