"""One fixed Graph POST for a trusted first-launch action."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Callable, ContextManager, Protocol
import re
import ssl
from time import monotonic
from urllib.parse import parse_qsl, urlencode

from .first_launch_activation_host import (ActivationGuard, ActivationPermit, _permit_valid,
                                           activation_attempt_id, activation_request)
from .first_launch_host import (FORM_CONTENT_TYPE, HEX, NATIVE_ID, CreationBinding, CreationPermit,
                                _active_permit, creation_attempt_id, graph_post_request,
                                validate_creation_binding)
from .graph_network import graph_https_connection
from .http_read import GraphReadTransport, arm_http_deadline
from .journal import digest
from .meta_write_mapping import OPTIONAL_PARAMETERS, PARAMETERS, utcnow
from .validation import aware_time, fail, strict_json
from .write_snapshots import SnapshotPlan, normalize_snapshot


@dataclass(frozen=True)
class ScopedGraphGrant:
    binding_sha256: str
    attempt_id: str
    write_grant_sha256: str
    permissions: tuple[str, ...]
    expires_at: str
    authorization: str = field(repr=False)
    appsecret_proof: str | None = field(default=None, repr=False)


class GraphCreationAuthority(Protocol):
    """Verify actual scope and atomically consume the approval in the host."""

    def current_permit(self, binding: CreationBinding) -> CreationPermit: ...

    def claim_once(self, binding: CreationBinding, permit: CreationPermit,
                   attempt_id: str) -> ContextManager[ScopedGraphGrant]: ...


class GraphCreationProvider:
    """Adapter from independent host authorities to the first-launch ledger.

    The host owns identity, consent, eligibility, grant revocation, one-use
    claims and the separate read channel. This class owns the bounded POST.
    """

    def __init__(self, authority: GraphCreationAuthority, readback: Callable, *, pre_send=None, clock=utcnow):
        self._authority = authority
        self._readback = readback
        self._pre_send = pre_send
        self._clock = clock

    def authorize_creation(self, binding):
        validate_creation_binding(binding)
        return self._authority.current_permit(binding)

    def recheck_creation(self, binding, permit):
        return self.authorize_creation(binding)

    def read_creation(self, binding, attempt_id, object_id):
        validate_creation_binding(binding, allow_legacy=True)
        return self._readback(binding, attempt_id, object_id)

    def send_paused_creation(self, binding, permit, attempt_id, form_body):
        validate_creation_binding(binding)
        _check_form(binding, form_body)
        now = self._clock()
        _active_permit(permit, binding, now)
        if attempt_id != creation_attempt_id(binding, digest(asdict(permit))):
            fail('FIRST_LAUNCH_HOST_ATTEMPT_MISMATCH')
        if self._pre_send is not None:
            self._pre_send(binding, form_body)
        expires_at = aware_time(permit.expires_at)

        def recheck():
            active = self._authority.current_permit(binding)
            if active != permit:
                fail('FIRST_LAUNCH_HOST_APPROVAL_CHANGED')
            _active_permit(active, binding, self._clock())
            return aware_time(permit.approved_at), expires_at

        receipt = _send_scoped_post(binding.path, form_body, clock=self._clock, expires_at=expires_at,
                                    recheck=recheck, claim=lambda: self._authority.claim_once(binding, permit, attempt_id),
                                    binding_sha256=binding.sha256, attempt_id=attempt_id,
                                    write_grant_sha256=permit.write_grant_sha256)
        identifier = receipt.get('id') if receipt is not None else None
        return identifier if type(identifier) is str and NATIVE_ID.fullmatch(identifier) else None


@dataclass(frozen=True, kw_only=True)
class ActivationReadPlan(SnapshotPlan):
    review_sha256: str
    attempt_id: str

    def __post_init__(self):
        super().__post_init__()
        if self.level != 'AD' or any(
            type(value) is not str or not HEX.fullmatch(value) for value in (self.review_sha256, self.attempt_id)
        ):
            fail('FIRST_LAUNCH_GRAPH_ACTIVATION_READ_SCOPE')


class GraphActivationAuthority(Protocol):
    """Verify current effects, approvals, write rights and independent read rights."""

    def current_guard(self, review: dict) -> ActivationGuard: ...

    def activation_permit(self, review: dict, request_sha256: str, effect_sha256: str) -> ActivationPermit: ...

    def claim_activation_once(self, review: dict, permit: ActivationPermit,
                              attempt_id: str) -> ContextManager[ScopedGraphGrant]: ...

    def activation_read_access(self, review: dict, plan: ActivationReadPlan) -> tuple[GraphReadTransport, str] | None: ...


class GraphActivationProvider:
    """Send only a separately approved one-Ad state change and read its state."""

    def __init__(self, authority: GraphActivationAuthority, *, clock=utcnow):
        self._authority = authority
        self._clock = clock

    def current_guard(self, review):
        return self._authority.current_guard(deepcopy(review))

    def activation_permit(self, review, request_sha256, effect_sha256):
        return self._authority.activation_permit(deepcopy(review), request_sha256, effect_sha256)

    def send_activation(self, review, guard, permit, attempt_id, request):
        expected = activation_request(review)
        if request != expected:
            fail('FIRST_LAUNCH_GRAPH_ACTIVATION_REQUEST_MISMATCH')
        request_sha256 = digest(expected)
        _permit_valid(review, request_sha256, guard, permit, self._clock())
        if permit.write_grant_sha256 is None:
            fail('FIRST_LAUNCH_GRAPH_ACTIVATION_GRANT_REQUIRED')
        if attempt_id != activation_attempt_id(review, request_sha256, guard.effect_sha256, digest(asdict(permit))):
            fail('FIRST_LAUNCH_GRAPH_ACTIVATION_ATTEMPT_MISMATCH')

        def recheck():
            current_guard = self.current_guard(review)
            if type(current_guard) is not ActivationGuard or current_guard.effect_sha256 != permit.effect_sha256:
                fail('FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_MISMATCH')
            active = self.activation_permit(review, request_sha256, current_guard.effect_sha256)
            if active != permit:
                fail('FIRST_LAUNCH_ACTIVATION_APPROVAL_CHANGED')
            _permit_valid(review, request_sha256, current_guard, active, self._clock())
            active_since = max(aware_time(value) for value in (
                current_guard.observed_at, current_guard.enforced_start_at, active.approved_at
            ))
            return active_since, _activation_deadline(current_guard, active)

        receipt = _send_scoped_post(expected['path'], expected['body'], clock=self._clock,
                                    expires_at=_activation_deadline(guard, permit), recheck=recheck,
                                    claim=lambda: self._authority.claim_activation_once(deepcopy(review), permit, attempt_id),
                                    binding_sha256=review['review_sha256'], attempt_id=attempt_id,
                                    write_grant_sha256=permit.write_grant_sha256)
        if (receipt is not None and receipt.get('success') is True
                and ('id' not in receipt or receipt['id'] == review['binding']['ad_id'])):
            return True
        return None

    def read_activation(self, review, attempt_id):
        activation_request(review)
        scope = review['binding']
        plan = ActivationReadPlan('AD', scope['ad_id'], scope['account_id'],
                                  review_sha256=review['review_sha256'], attempt_id=attempt_id)
        access = self._authority.activation_read_access(deepcopy(review), plan)
        if access is None:
            return None
        transport, evidence_ref = access
        if (type(transport) is not GraphReadTransport or transport.plan != plan or transport.retry_rate_limit is not None
                or transport.binding.app_id != scope['app_id'] or type(evidence_ref) is not str
                or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', evidence_ref)):
            fail('FIRST_LAUNCH_GRAPH_ACTIVATION_READ_SCOPE')
        status, body = transport.get(plan.request())
        row = normalize_snapshot(plan, status, body)
        if (row['campaign_id'], row['adset_id'], row['creative']['id']) != (
            scope['campaign_id'], scope['adset_id'], scope['creative_id']
        ) or row['status'] not in ('ACTIVE', 'PAUSED'):
            fail('FIRST_LAUNCH_ACTIVATION_READBACK_MISMATCH')
        return dict(attempt_id=attempt_id, review_sha256=review['review_sha256'], account_id=plan.account_id,
                    ad_id=plan.object_id, configured_status=row['status'], effective_status=row['effective_status'],
                    observed_at=self._clock().isoformat(), evidence_ref=evidence_ref)


def _activation_deadline(guard, permit):
    return min(aware_time(value) for value in (guard.valid_until, guard.enforced_stop_at, permit.expires_at))


def _send_scoped_post(path, form_body, *, clock, expires_at, recheck, claim,
                      binding_sha256, attempt_id, write_grant_sha256):
    budget = min(15, (expires_at - clock()).total_seconds())
    if budget <= 0:
        fail('FIRST_LAUNCH_GRAPH_GRANT_EXPIRED')
    deadline = monotonic() + budget
    connection = graph_https_connection(budget, ssl.create_default_context())
    try:
        # Authenticate the fixed TLS destination before claiming the one-use grant.
        connection.connect()
        if monotonic() >= deadline:
            raise TimeoutError
        active_since, reviewed_until = recheck()
        active_until = min(expires_at, reviewed_until)
        if not active_since <= clock() < active_until or monotonic() >= deadline:
            fail('FIRST_LAUNCH_GRAPH_GRANT_EXPIRED')
        with claim() as grant:
            now = clock()
            if now < active_since:
                fail('FIRST_LAUNCH_GRAPH_GRANT_EXPIRED')
            expiry = _grant_expiry(binding_sha256, attempt_id, write_grant_sha256, grant, now, active_until)
            remaining = min(deadline - monotonic(), (expiry - now).total_seconds())
            return _post(connection, path, form_body, grant, remaining)
    finally:
        connection.close()


def _post(connection, path, form_body, grant, budget):
    body = form_body
    if grant.appsecret_proof is not None:
        body += '&' + urlencode({'appsecret_proof': grant.appsecret_proof})
    headers = {'Authorization': grant.authorization, 'Content-Type': FORM_CONTENT_TYPE,
               'Accept': 'application/json'}
    try:
        arm_http_deadline(connection, budget)
        connection.sock.settimeout(budget)
        connection.request('POST', path, body=body, headers=headers)
    finally:
        headers.clear()
    with connection.getresponse() as response:
        if not 200 <= response.status < 300:
            return None
        limit = 128 * 1024
        raw = response.read(limit + 1)
    if len(raw) > limit:
        fail('FIRST_LAUNCH_GRAPH_RECEIPT_TOO_LARGE')
    receipt = strict_json(raw.decode('utf-8'), max_chars=limit)
    if type(receipt) is not dict or 'error' in receipt:
        return None
    return receipt


def _check_form(binding, form_body):
    if (type(form_body) is not str or len(form_body) > 64_000 or not form_body.isascii()
            or digest(graph_post_request(binding.path, form_body)) != binding.request_sha256):
        fail('FIRST_LAUNCH_GRAPH_FORM_MISMATCH')
    try:
        fields = parse_qsl(form_body, keep_blank_values=True, strict_parsing=True, max_num_fields=16,
                           encoding='utf-8', errors='strict')
    except ValueError:
        fail('FIRST_LAUNCH_GRAPH_FORM_MISMATCH')
    form = dict(fields)
    if len(fields) != len(form):
        fail('FIRST_LAUNCH_GRAPH_FORM_MISMATCH')
    operation = binding.operation
    if operation == 'CREATE_CREATIVE':
        if set(form) != {'name', 'object_story_spec'}:
            fail('FIRST_LAUNCH_GRAPH_FORM_MISMATCH')
        story = strict_json(form['object_story_spec'], max_chars=16_000)
        if type(story) is not dict or story.get('page_id') != binding.page_id:
            fail('FIRST_LAUNCH_GRAPH_CREATIVE_PAGE_MISMATCH')
        branches = set(story) - {'page_id'}
        if len(branches) != 1 or not branches <= {'link_data', 'video_data'}:
            fail('FIRST_LAUNCH_GRAPH_CREATIVE_MEDIA_MISMATCH')
        branch = next(iter(branches))
        data = story[branch]
        allowed = ({'image_hash', 'link', 'message', 'name', 'description', 'call_to_action'} if branch == 'link_data'
                   else {'video_id', 'image_hash', 'message', 'title', 'link_description', 'call_to_action'})
        if type(data) is not dict or set(data) - allowed or not {'image_hash', 'call_to_action'} <= data.keys():
            fail('FIRST_LAUNCH_GRAPH_CREATIVE_MEDIA_MISMATCH')
        return
    allowed = set(PARAMETERS[operation])
    required = allowed - set(OPTIONAL_PARAMETERS.get(operation, ()))
    if not required <= form.keys() <= allowed or form['status'] != 'PAUSED':
        fail('FIRST_LAUNCH_GRAPH_PAUSED_FORM_REQUIRED')


def _grant_expiry(binding_sha256, attempt_id, write_grant_sha256, grant, now, active_until):
    if (type(grant) is not ScopedGraphGrant
            or (grant.binding_sha256, grant.attempt_id, grant.write_grant_sha256)
            != (binding_sha256, attempt_id, write_grant_sha256)
            or type(grant.permissions) is not tuple or 'ads_management' not in grant.permissions):
        fail('FIRST_LAUNCH_GRAPH_GRANT_SCOPE_MISMATCH')
    if type(grant.authorization) is not str or not re.fullmatch(r'Bearer [A-Za-z0-9._~+/=-]{1,8192}', grant.authorization):
        fail('FIRST_LAUNCH_GRAPH_AUTHORIZATION_FORMAT')
    proof = grant.appsecret_proof
    if proof is not None and (type(proof) is not str or not re.fullmatch('[a-f0-9]{64}', proof)):
        fail('FIRST_LAUNCH_GRAPH_AUTHORIZATION_FORMAT')
    expiry = min(active_until, aware_time(grant.expires_at))
    if now >= expiry:
        fail('FIRST_LAUNCH_GRAPH_GRANT_EXPIRED')
    return expiry
