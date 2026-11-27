"""Dormant HTTPS GET transport. Default credential broker always refuses.

No OAuth, environment lookup, keychain access, refresh or real write tools.
Binding flags describe reviewed metadata, not independently verified grants.
The interactive connection check supplies a short-lived in-memory broker after
debug metadata validation; the bounded foreground runner uses fresh confirmation
and metadata per start/resume. Both real paths remain unaccepted on live assets.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import http.client
import io
import json
import re
import ssl
import time
from urllib.parse import urlencode

from .journal import digest
from .rate_limit import cooldown
from .read_adapter import LIVE_PENDING, RECENT_LIVE_COMMENTS, PageLiveReadPlan, ReadPlan, error_category
from .validation import InputError, aware_time, fail, finite_number, strict_json
from .credential_record import ERRORS as RECORD_ERRORS
from .graph_network import HOST, NETWORK_FAILURE, PROXY_UNAVAILABLE, graph_https_connection

MAX_RESPONSE_BYTES = 1_000_000
ACCOUNT_TIMEZONE_CHANGED = 'WORKSPACE_ACCOUNT_TIMEZONE_CHANGED'
ACCOUNT_CURRENCY_CHANGED = 'WORKSPACE_ACCOUNT_CURRENCY_CHANGED'
ACCOUNT_STATUS_REVIEW_REQUIRED = 'WORKSPACE_ACCOUNT_STATUS_REVIEW_REQUIRED'
ACCOUNT_SCOPE_ERRORS = (ACCOUNT_TIMEZONE_CHANGED, ACCOUNT_CURRENCY_CHANGED, ACCOUNT_STATUS_REVIEW_REQUIRED)
PAGE_SCOPE_CHANGED = 'WORKSPACE_PAGE_LIVE_SCOPE_CHANGED'
PAGE_METRICS_BLOCKERS = frozenset({'READ_INSIGHTS_SCOPE_REQUIRED', 'NAMED_ANALYZE_TASK_EVIDENCE_REQUIRED',
                                  'DEBUG_PAGE_TARGET_NOT_GRANTED', 'DEBUG_PAGE_TARGET_INVALID',
                                  'PAGE_ROLE_LIST_SCOPE_REQUIRED', 'PAGE_ROLE_LOOKUP_PERMISSION_REJECTED',
                                  'PAGE_ROLE_LOOKUP_UNAVAILABLE', 'PAGE_ROLE_LOOKUP_INVALID',
                                  'PAGE_ANALYZE_ALIAS_REVIEW_REQUIRED', 'NAMED_ANALYZE_TASK_NOT_LISTED',
                                  'NAMED_PAGE_TASK_NOT_OBSERVED', 'PAGE_ROLE_LOOKUP_INCOMPLETE'})
PUBLIC_ERRORS = RECORD_ERRORS | {
    'READ_REQUEST_CONTRACT', 'READ_REQUEST_CHANGED', 'INVALID_CURSOR', 'READ_PLAN_BINDING_CHANGED',
    'CONNECTION_ID_REQUIRED', 'CONNECTION_REVIEW_REQUIRED', 'CONNECTION_EXPIRED_OR_CLOCK_SKEW',
    'SCOPE_METADATA_INVALID', 'MINIMUM_READ_SCOPE_NOT_VERIFIED', 'REAL_LIVE_CONTRACT_PENDING',
    'SECURE_CREDENTIAL_BROKER_NOT_CONFIGURED', 'CREDENTIAL_FORMAT_INVALID', 'HTTP_REDIRECT_REFUSED',
    'HTTP_RESPONSE_TOO_LARGE', 'HTTP_RESPONSE_INVALID_JSON', 'HTTP_RESPONSE_NOT_OBJECT',
    NETWORK_FAILURE, PROXY_UNAVAILABLE, 'NATIVE_CREDENTIAL_ACCESS_NOT_ENABLED', 'CREDENTIAL_BINDING_CHANGED',
    'NATIVE_CREDENTIAL_PLATFORM_UNSUPPORTED', 'NATIVE_CREDENTIAL_UNAVAILABLE', 'NATIVE_CREDENTIAL_SIZE',
    'NATIVE_CREDENTIAL_ENCODING', 'NATIVE_CREDENTIAL_IDENTITY_MISMATCH',
    'MONITOR_SESSION_EXPIRED', 'MONITOR_CONTROL_CHANGED',
    'SNAPSHOT_ENTRY_SESSION_EXPIRED', 'WORKSPACE_CHANGED_DURING_CHECK',
    'WORKSPACE_MONITOR_SCOPE_CHANGED', *ACCOUNT_SCOPE_ERRORS, PAGE_SCOPE_CHANGED,
}


class CredentialBroker:
    """Implement via an authorized OS credential service, never a JSON file."""
    def recheck(self, binding):
        """Optional local lifecycle check; does not grant access or refresh."""
        pass

    @contextmanager
    def lease(self, binding):
        fail('SECURE_CREDENTIAL_BROKER_NOT_CONFIGURED')
        yield  # pragma: no cover - keeps the unavailable interface a context manager


@dataclass(frozen=True)
class ReadBinding:
    plan_hash: str
    app_id: str
    principal_id: str
    observed_at: str
    expires_at: str
    review_expires_at: str
    granted_scopes: tuple
    app_binding_verified: bool
    asset_ownership_verified: bool
    read_authorized: bool

    def validate(self, plan, now):
        self._validate_review(plan, now)
        if plan.account_id and 'ads_read' not in self.granted_scopes:
            fail('MINIMUM_READ_SCOPE_NOT_VERIFIED')
        if plan.operation in LIVE_PENDING: fail('REAL_LIVE_CONTRACT_PENDING')
        if plan.operation == 'page' and 'pages_read_engagement' not in self.granted_scopes:
            fail('MINIMUM_READ_SCOPE_NOT_VERIFIED')

    def _validate_review(self, plan, now):
        if self.plan_hash != digest(asdict(plan)): fail('READ_PLAN_BINDING_CHANGED')
        for value in (self.app_id, self.principal_id):
            if type(value) is not str or not re.fullmatch(r'[0-9]{1,40}', value): fail('CONNECTION_ID_REQUIRED')
        if any(value is not True for value in (self.app_binding_verified, self.asset_ownership_verified, self.read_authorized)):
            fail('CONNECTION_REVIEW_REQUIRED')
        if not aware_time(self.observed_at) <= now < min(aware_time(self.expires_at), aware_time(self.review_expires_at)):
            fail('CONNECTION_EXPIRED_OR_CLOCK_SKEW')
        scopes = self.granted_scopes
        if type(scopes) is not tuple or len(scopes) > 64 or any(type(x) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', x) for x in scopes):
            fail('SCOPE_METADATA_INVALID')


@dataclass(frozen=True)
class _PageCollectorBinding(ReadBinding):
    """Only produced after dual-token and named Page actor preflight."""

    page_id: str
    analyze_task_verified: bool = False

    def validate(self, plan, now):
        self._validate_review(plan, now)
        if plan.account_id is not None or plan.page_id != self.page_id:
            fail(PAGE_SCOPE_CHANGED)
        if 'pages_read_engagement' not in self.granted_scopes:
            fail('MINIMUM_READ_SCOPE_NOT_VERIFIED')
        if plan.operation == 'post_insights':
            if self.analyze_task_verified is not True or 'read_insights' not in self.granted_scopes:
                fail('REAL_LIVE_CONTRACT_PENDING')
        elif plan.operation not in ('page', 'live', RECENT_LIVE_COMMENTS, 'live_post'):
            fail('REAL_LIVE_CONTRACT_PENDING')


class _DeadlineReader(io.RawIOBase):
    def __init__(self, socket, mode, deadline):
        self._socket = socket
        self._deadline = deadline
        self._stream = None
        self._stream = socket.makefile(mode, buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._socket.settimeout(remaining)
        count = self._stream.readinto(buffer)
        if time.monotonic() >= self._deadline:
            raise TimeoutError
        return count

    def close(self):
        try:
            if self._stream is not None:
                self._stream.close()
        finally:
            super().close()


class _DeadlineResponseSocket:
    """Supply a bounded buffered reader to the standard HTTP response parser."""
    def __init__(self, socket, deadline):
        self.socket = socket
        self.deadline = deadline

    def makefile(self, mode):
        return io.BufferedReader(_DeadlineReader(self.socket, mode, self.deadline))


def arm_http_deadline(connection, seconds):
    """Use one monotonic receive deadline across status, headers and body."""
    if seconds <= 0:
        raise TimeoutError
    deadline = time.monotonic() + seconds
    connection.timeout = seconds

    def response(socket, *args, **kwargs):
        return http.client.HTTPResponse(_DeadlineResponseSocket(socket, deadline), *args, **kwargs)

    connection.response_class = response


class GraphReadTransport:
    def __init__(self, plan, binding, broker=None, timeout=15, clock=None, *, expected_account_context=None,
                 time_remaining=None):
        from .first_launch_graph import ActivationReadPlan
        from .metric_adapter import METRIC_PLAN_TYPES
        from .native_insights import NativeInsightsPlan, ReportRunPlan
        from .first_launch_preview import PreviewReadPlan
        from .first_launch_readback import CreationReadPlan
        from .write_snapshots import SnapshotPlan
        if (type(plan) not in (ReadPlan, PageLiveReadPlan, SnapshotPlan, CreationReadPlan, PreviewReadPlan,
                               ActivationReadPlan, NativeInsightsPlan, ReportRunPlan, *METRIC_PLAN_TYPES)
                or type(binding) not in (ReadBinding, _PageCollectorBinding)):
            fail('READ_CONNECTION_CONTRACT')
        if not finite_number(timeout) or not 1 <= timeout <= 30: fail('HTTP_TIMEOUT_BUDGET')
        object_pattern = r'[1-9][0-9]{0,39}_[1-9][0-9]{0,39}' if plan.operation in ('post_insights','live_post') else r'[0-9]{1,40}'
        if (not re.fullmatch(object_pattern,plan.object_id)
                or any(value is not None and not re.fullmatch(r'[0-9]{1,40}',value)
                       for value in (plan.account_id,plan.page_id,getattr(plan,'live_id',None),getattr(plan,'video_id',None),
                                     getattr(plan,'expected_live_video_id',None)))):
            fail('REAL_TRANSPORT_NUMERIC_SCOPE_REQUIRED')
        self.plan = plan; self.binding = binding
        self.broker = broker if broker is not None else CredentialBroker()
        if not isinstance(self.broker, CredentialBroker): fail('UNREVIEWED_CREDENTIAL_INTERFACE')
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.time_remaining = time_remaining
        self.expected_account_context = expected_account_context
        self.halted = False
        self.record_rate_limit = None
        self.retry_rate_limit = None

    def get(self, request):
        if type(request) is not dict or request.get('method') != 'GET':
            fail('READ_REQUEST_CONTRACT')
        return self._exchange(request)

    def submit_report(self, request):
        from .native_insights import ReportRunPlan
        if (type(self.plan) is not ReportRunPlan or self.plan.action != 'SUBMIT'
                or self.halted or request != self.plan.request()):
            fail('READ_REQUEST_CONTRACT')
        self.halted = True
        status, report, _ = self._request(request)
        return status, report

    def _exchange(self, request):
        if self.halted: fail('EXPLICIT_CONNECTION_REVIEW_REQUIRED')
        try:
            status, body, hold = self._request(request)
            if hold is not None and self.retry_rate_limit is not None and self.retry_rate_limit(hold):
                # Retry just this fixed GET, including its original in-memory cursor.
                status, body, _ = self._request(request)
            if not 200 <= status < 300 or 'error' in body:
                self.halted = True
            return status, body
        except InputError as exc:
            self.halted = True
            code = str(exc)
            raise InputError(code if code in PUBLIC_ERRORS else 'CREDENTIAL_OR_TRANSPORT_FAILURE') from None
        except Exception:
            # Provider exceptions may contain credential material; never echo.
            self.halted = True
            raise InputError('CREDENTIAL_OR_TRANSPORT_FAILURE') from None

    def _request(self, request):
        if type(request) is not dict or set(request) != {'method', 'path', 'params'} or type(request['params']) is not dict:
            fail('READ_REQUEST_CONTRACT')
        expected = self.plan.request(request['params'].get('after'))
        if request != expected: fail('READ_REQUEST_CHANGED')
        self.binding.validate(self.plan, self.clock())
        params = {key: json.dumps(value, separators=(',', ':')) if type(value) in (dict, list, bool) else value
                  for key, value in expected['params'].items()}
        method = expected['method']
        encoded = urlencode(params)
        path = expected['path'] + ('?' + encoded if method == 'GET' and encoded else '')
        with self.broker.lease(self.binding) as credential:
            if type(credential) is not str or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,8192}', credential):
                fail('CREDENTIAL_FORMAT_INVALID')
            # A broker may block for consent; recheck expiry before transmission.
            self.binding.validate(self.plan, self.clock())
            connection = response = None
            headers = {'Authorization': 'Bearer ' + credential, 'Accept': 'application/json'}
            try:
                connection = graph_https_connection(self.timeout, ssl.create_default_context())
                self.broker.recheck(self.binding)
                now = self.clock()
                self.binding.validate(self.plan, now)
                expiry = min(aware_time(self.binding.expires_at),aware_time(self.binding.review_expires_at))
                budget = min(self.timeout, (expiry-now).total_seconds())
                if self.time_remaining is not None:
                    budget = min(budget,self.time_remaining())
                arm_http_deadline(connection,budget)
                if method == 'POST':
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                    connection.request(method, path, body=encoded, headers=headers)
                else:
                    connection.request(method, path, headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400: fail('HTTP_REDIRECT_REFUSED')
                body = {} if response.status==429 and self.record_rate_limit is not None else read_json_response(response)
                status = response.status
                hold = None
                if self.record_rate_limit is not None and error_category(status,body)=='RATE_LIMIT':
                    # Record only the sanitized delay before the lease rechecks the current scope.
                    observed = cooldown(response,self.clock())
                    if self.record_rate_limit(observed):
                        hold = observed
            except (TimeoutError, OSError, http.client.HTTPException):
                raise InputError(NETWORK_FAILURE) from None
            finally:
                headers.clear()
                credential = None  # Python cannot promise memory zeroization.
                try:
                    if response is not None: response.close()
                finally:
                    if connection is not None: connection.close()

        # The lease must finish its scope checks before any response can be used.
        self.binding.validate(self.plan,self.clock())
        if (self.plan.operation == 'account' and self.expected_account_context is not None
                and 200 <= status < 300 and 'error' not in body
                and body.get('account_id')==self.plan.account_id and body.get('id')=='act_'+self.plan.account_id):
            timezone_name,currency = self.expected_account_context
            if body.get('timezone_name') != timezone_name:
                fail(ACCOUNT_TIMEZONE_CHANGED)
            if body.get('currency') != currency:
                fail(ACCOUNT_CURRENCY_CHANGED)
            account_status = body.get('account_status')
            if type(account_status) is not int or account_status != 1:
                fail(ACCOUNT_STATUS_REVIEW_REQUIRED)
        return status, body, hold

def read_json_response(response):
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES: fail('HTTP_RESPONSE_TOO_LARGE')
        try: body = strict_json(raw.decode('utf-8'))
        except (UnicodeError, ValueError): fail('HTTP_RESPONSE_INVALID_JSON')
        if type(body) is not dict: fail('HTTP_RESPONSE_NOT_OBJECT')
        return body
    except InputError:
        if response.status not in (401,403,404,429) and not 500 <= response.status < 600:
            raise
        # These HTTP statuses carry a usable category even without Graph JSON.
        return {}
