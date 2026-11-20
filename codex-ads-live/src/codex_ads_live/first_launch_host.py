"""Trusted-host contract for one reserved, paused creation.

Only a host with an independent approval, eligibility and transport provider can
call this path. Neither CLI arguments nor local progress files install a host.
"""

from dataclasses import asdict, dataclass
from datetime import timedelta
import re
from typing import Protocol

from .journal import digest
from .meta_write_mapping import CREATE_EDGES, SUPPORTED_CURRENCIES
from .read_adapter import GRAPH_AUTH_ERRORS
from .validation import InputError, aware_time, fail, object_only


HEX = re.compile('[a-f0-9]{64}')
NATIVE_ID = re.compile('[1-9][0-9]{0,39}')
REFERENCE = re.compile(r'[A-Za-z0-9_-]{1,80}')
FORM_CONTENT_TYPE = 'application/x-www-form-urlencoded'
CREATION_READBACK_SCHEMA = 'first-launch-readback-v1'
CREATION_READBACK_REMOTE_FAILURES = frozenset(
    (*GRAPH_AUTH_ERRORS, 'PERMISSION', 'OBJECT_OR_PARAMETER', 'RATE_LIMIT', 'SERVICE_UNAVAILABLE', 'UNCLASSIFIED_ERROR')
)


def graph_post_request(path, form_body):
    return dict(provider='GRAPH_MARKETING_API', method='POST', path=path,
                content_type=FORM_CONTENT_TYPE, body=form_body)


def _hash(value):
    if type(value) is not str or not HEX.fullmatch(value):
        fail('FIRST_LAUNCH_HOST_BINDING_INVALID')


def _id(value):
    if type(value) is not str or not NATIVE_ID.fullmatch(value):
        fail('FIRST_LAUNCH_HOST_OBJECT_ID_INVALID')


@dataclass(frozen=True)
class CreationBinding:
    workspace_sha256: str
    app_id: str
    business_id: str
    account_id: str
    page_id: str
    plan_sha256: str
    stage_sha256: str
    draft_sha256: str
    request_sha256: str
    dependency_sha256: str
    material_sha256: str | None
    operation: str
    path: str
    currency: str
    purpose: str = 'PAUSED_FIRST_LAUNCH_CREATION'
    configuration_sha256: str | None = None

    @property
    def sha256(self):
        values = asdict(self)
        if self.configuration_sha256 is None:
            del values['configuration_sha256']
        return digest(values)


@dataclass(frozen=True)
class CreationPermit:
    """Non-secret proof of scope issued by the host's independent authority."""

    binding_sha256: str
    approval_ref: str
    write_grant_sha256: str
    eligibility_sha256: str
    approved_at: str
    expires_at: str


class CreationProvider(Protocol):
    """Combines trusted host authorities without exposing their credential data.

    Verify a real, single-action approval for this request and its current write
    authority, budget, rights and asset eligibility on each authorization. Use
    the original attempt for bounded independent readback. The provider must
    consume its approval once across all hosts and disallow redirects or retries.
    """

    def authorize_creation(self, binding: CreationBinding) -> CreationPermit: ...

    def recheck_creation(self, binding: CreationBinding, permit: CreationPermit) -> CreationPermit: ...

    def send_paused_creation(self, binding: CreationBinding, permit: CreationPermit, attempt_id: str,
                             form_body: str) -> str | None: ...

    def read_creation(self, binding: CreationBinding, attempt_id: str, object_id: str | None) -> dict | None: ...


def _active_permit(permit, binding, now):
    if type(permit) is not CreationPermit or permit.binding_sha256 != binding.sha256:
        fail('FIRST_LAUNCH_HOST_APPROVAL_SCOPE_MISMATCH')
    if type(permit.approval_ref) is not str or not REFERENCE.fullmatch(permit.approval_ref):
        fail('FIRST_LAUNCH_HOST_APPROVAL_REFERENCE_INVALID')
    _hash(permit.write_grant_sha256)
    _hash(permit.eligibility_sha256)
    approved, expires = (aware_time(value) for value in (permit.approved_at, permit.expires_at))
    if not approved <= now < expires or expires-approved > timedelta(minutes=15):
        fail('FIRST_LAUNCH_HOST_APPROVAL_EXPIRED')


def validate_creation_binding(binding, *, allow_legacy=False):
    if type(binding) is not CreationBinding:
        fail('FIRST_LAUNCH_HOST_BINDING_INVALID')
    for value in (binding.workspace_sha256,binding.plan_sha256,binding.stage_sha256,
                  binding.draft_sha256,binding.request_sha256,binding.dependency_sha256):
        _hash(value)
    if binding.configuration_sha256 is not None or not allow_legacy:
        _hash(binding.configuration_sha256)
    if binding.material_sha256 is not None:
        _hash(binding.material_sha256)
    for value in (binding.app_id,binding.business_id,binding.account_id,binding.page_id):
        _id(value)
    edge = CREATE_EDGES.get(binding.operation) if type(binding.operation) is str else None
    if (edge is None or binding.path != f'/v27.0/act_{binding.account_id}/{edge}'
            or binding.purpose != 'PAUSED_FIRST_LAUNCH_CREATION' or binding.currency not in SUPPORTED_CURRENCIES):
        fail('FIRST_LAUNCH_HOST_BINDING_INVALID')


def creation_attempt_id(binding, permit_sha256):
    return digest([binding.sha256, permit_sha256])


def validate_attempt(attempt):
    keys = ('binding_sha256','attempt_id','permit_sha256','reserved_at','acknowledged_object_id')
    object_only(attempt, (*keys,'binding'), keys)
    for key in ('binding_sha256','attempt_id','permit_sha256'):
        _hash(attempt[key])
    if attempt['attempt_id'] != digest([attempt['binding_sha256'],attempt['permit_sha256']]):
        fail('FIRST_LAUNCH_HOST_BINDING_INVALID')
    aware_time(attempt['reserved_at'])
    if attempt['acknowledged_object_id'] is not None:
        _id(attempt['acknowledged_object_id'])
    if 'binding' in attempt:
        frozen = attempt['binding']
        fields = tuple(CreationBinding.__dataclass_fields__)
        object_only(frozen,fields,fields)
        binding = CreationBinding(**frozen)
        validate_creation_binding(binding)
        if binding.sha256 != attempt['binding_sha256']:
            fail('FIRST_LAUNCH_HOST_BINDING_INVALID')
        return binding
    return None


def _authorize(provider, binding, clock):
    try:
        permit = provider.authorize_creation(binding)
    except Exception:
        fail('FIRST_LAUNCH_HOST_APPROVAL_UNAVAILABLE')
    _active_permit(permit,binding,clock())
    return permit


def dispatch_creation(provider, binding, form_body, stage, save, assert_current, clock):
    if stage.get('host_attempt') is not None or stage['readback'] is not None:
        fail('FIRST_LAUNCH_HOST_RECOVER_ORIGINAL_ONLY')
    validate_creation_binding(binding)
    permit = _authorize(provider,binding,clock)
    assert_current()

    # Once the attempt is durable, even a lost acknowledgement or a clock
    # change leaves the original attempt for recovery. There is no resend.
    permit_sha = digest(asdict(permit))
    attempt = dict(binding_sha256=binding.sha256, permit_sha256=permit_sha,
                   attempt_id=creation_attempt_id(binding,permit_sha),
                   reserved_at=clock().isoformat(), acknowledged_object_id=None, binding=asdict(binding))
    stage['host_attempt'] = attempt
    save()
    try:
        confirmed = provider.recheck_creation(binding,permit)
    except Exception:
        fail('FIRST_LAUNCH_HOST_APPROVAL_UNAVAILABLE')
    if confirmed != permit:
        fail('FIRST_LAUNCH_HOST_APPROVAL_CHANGED')
    _active_permit(permit, binding, clock())
    assert_current()
    _active_permit(permit, binding, clock())
    try:
        object_id = provider.send_paused_creation(binding,permit,attempt['attempt_id'],form_body)
    except Exception:
        fail('FIRST_LAUNCH_HOST_OUTCOME_UNKNOWN')
    if object_id is not None:
        _id(object_id)
        attempt['acknowledged_object_id'] = object_id
        save()


def recover_creation(provider, binding, stage):
    attempt = stage.get('host_attempt')
    if attempt is None or attempt['binding_sha256'] != binding.sha256:
        fail('FIRST_LAUNCH_HOST_ATTEMPT_REQUIRED')
    known_id = attempt['acknowledged_object_id']
    try:
        record = provider.read_creation(binding,attempt['attempt_id'],known_id)
    except InputError as exc:
        code = str(exc)
        if code in CREATION_READBACK_REMOTE_FAILURES:
            fail('FIRST_LAUNCH_HOST_READBACK_' + code)
        fail('FIRST_LAUNCH_HOST_READBACK_UNAVAILABLE')
    except Exception:
        fail('FIRST_LAUNCH_HOST_READBACK_UNAVAILABLE')
    if record is None:
        return None
    keys = ('binding_sha256','attempt_id','source','observation')
    object_only(record, keys, keys)
    if (record['binding_sha256'] != binding.sha256 or record['attempt_id'] != attempt['attempt_id']
            or record['source'] != 'INDEPENDENT_HOST_READBACK'):
        fail('FIRST_LAUNCH_HOST_READBACK_MISMATCH')
    return record['observation']
