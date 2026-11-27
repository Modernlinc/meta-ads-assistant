"""Require independent spending approval for exactly one recorded Ad."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import re
from typing import Protocol

from .first_launch_host import HEX, NATIVE_ID, REFERENCE, graph_post_request
from .journal import digest
from .read_adapter import STATES
from .validation import aware_time, fail, object_only


REVIEW_SCHEMA = 'first-launch-activation-review-v1'


@dataclass(frozen=True)
class ActivationGuard:
    review_sha256: str
    preview_observation_sha256: str
    account_id: str
    ad_id: str
    campaign_id: str
    adset_id: str
    creative_id: str
    ad_status: str
    campaign_status: str
    adset_status: str
    affected_ads: tuple[str, ...]
    currency: str
    timezone: str
    daily_budget_minor: int | None
    enforced_spend_cap_minor: int
    enforced_start_at: str
    enforced_stop_at: str
    spend_control_sha256: str
    observed_at: str
    valid_until: str
    selected_budget: dict | None = None

    @property
    def effect(self):
        effect = asdict(self)
        del effect['observed_at'], effect['valid_until']
        if effect['selected_budget'] is None:
            del effect['selected_budget']
        return effect

    @property
    def effect_sha256(self):
        return digest(self.effect)


@dataclass(frozen=True)
class ActivationPermit:
    purpose: str
    review_sha256: str
    request_sha256: str
    effect_sha256: str
    approval_ref: str
    approved_at: str
    expires_at: str
    write_grant_sha256: str | None = None


class ActivationProvider(Protocol):
    """Trusted host for fresh scope, independent consent and one-use execution.

    Prove the named Ad preview and enforced spend and stop controls independently.
    Issue the existing approval only. At send, recheck and consume it durably
    across hosts, binding the exact request and observed effect. Never retry.
    """

    def current_guard(self, review: dict) -> ActivationGuard: ...

    def activation_permit(self, review: dict, request_sha256: str,
                          effect_sha256: str) -> ActivationPermit: ...

    def send_activation(self, review: dict, guard: ActivationGuard, permit: ActivationPermit,
                        attempt_id: str, request: dict) -> bool | None: ...

    def read_activation(self, review: dict, attempt_id: str) -> dict | None: ...


def _hash(value):
    if type(value) is not str or not HEX.fullmatch(value):
        fail('FIRST_LAUNCH_ACTIVATION_HASH_REQUIRED')


def _guard_valid(review, guard, now):
    scope = review['binding']
    if (type(guard) is not ActivationGuard or guard.review_sha256 != review['review_sha256']
            or guard.selected_budget != scope.get('selected_budget')
            or any(getattr(guard, key) != scope[key] for key in (
                'preview_observation_sha256', 'account_id', 'ad_id', 'campaign_id', 'adset_id', 'creative_id',
                'currency', 'timezone', 'daily_budget_minor'))):
        fail('FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_MISMATCH')
    policy = guard.selected_budget
    if policy is None:
        if type(guard.daily_budget_minor) is not int:
            fail('FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_MISMATCH')
    else:
        fields = ('owner', 'schedule', 'budget_minor')
        object_only(policy, fields, fields)
        if (policy['owner'] not in ('CAMPAIGN', 'ADSET') or policy['schedule'] not in ('DAILY','LIFETIME')
                or type(policy['budget_minor']) is not int or not 0 < policy['budget_minor'] <= scope['maximum_spend_minor']
                or guard.daily_budget_minor != (policy['budget_minor'] if policy['schedule']=='DAILY' else None)):
            fail('FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_MISMATCH')
    if (guard.ad_status, guard.campaign_status, guard.adset_status) != ('PAUSED', 'ACTIVE', 'ACTIVE'):
        fail('FIRST_LAUNCH_ACTIVATION_PARENT_OR_AD_STATUS')
    if guard.affected_ads != (scope['ad_id'],):
        fail('FIRST_LAUNCH_ACTIVATION_EFFECT_SCOPE')
    _hash(guard.spend_control_sha256)
    if (type(guard.enforced_spend_cap_minor) is not int
            or not 0 < guard.enforced_spend_cap_minor <= scope['maximum_spend_minor']
            or not aware_time(scope['starts_at']) <= aware_time(guard.enforced_start_at)
                <= now < aware_time(guard.enforced_stop_at) <= aware_time(scope['ends_at'])):
        fail('FIRST_LAUNCH_ACTIVATION_SPEND_CONTROL_REQUIRED')
    if not aware_time(guard.observed_at) <= now < aware_time(guard.valid_until):
        fail('FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_EXPIRED')


def _permit_valid(review, request_sha256, guard, permit, now):
    _guard_valid(review, guard, now)
    if (type(permit) is not ActivationPermit or permit.purpose != 'AUTHORIZE_AD_SPEND'
            or (permit.review_sha256, permit.request_sha256, permit.effect_sha256)
            != (review['review_sha256'], request_sha256, guard.effect_sha256)
            or type(permit.approval_ref) is not str or not REFERENCE.fullmatch(permit.approval_ref)):
        fail('FIRST_LAUNCH_ACTIVATION_APPROVAL_MISMATCH')
    if permit.write_grant_sha256 is not None:
        _hash(permit.write_grant_sha256)
    if not aware_time(review['preview_observation']['observed_at']) <= aware_time(permit.approved_at) <= now < min(
        aware_time(permit.expires_at), aware_time(guard.enforced_stop_at)
    ):
        fail('FIRST_LAUNCH_ACTIVATION_APPROVAL_EXPIRED')


def validate_activation_attempt(attempt, chain_sha256):
    digest_fields = ('object_chain_sha256', 'review_sha256', 'request_sha256', 'effect_sha256',
                     'permit_sha256', 'attempt_id')
    keys = (*digest_fields, 'reserved_at', 'state', 'observation')
    object_only(attempt, (*keys, 'guard_effect', 'frozen_review'), keys)
    for key in digest_fields:
        _hash(attempt[key])
    if (attempt['object_chain_sha256'] != chain_sha256
            or attempt['attempt_id'] != digest([attempt[key] for key in digest_fields[:-1]])):
        fail('FIRST_LAUNCH_ACTIVATION_ATTEMPT_MISMATCH')
    aware_time(attempt['reserved_at'])
    if 'frozen_review' in attempt:
        review = attempt['frozen_review']
        fields = ('schema', 'status', 'binding', 'review_sha256', 'preview_observation', 'preview_evidence',
                  'preview_kind', 'review_blockers', 'parent_delivery_state',
                  'independent_activation_approval_verified', 'spend_authorized', 'dispatchable', 'network_used')
        object_only(review, fields, fields)
        binding = review['binding']
        if (review['schema'] != REVIEW_SCHEMA or type(binding) is not dict or review['preview_kind'] != 'AD'
                or review['status'] != 'FIRST_LAUNCH_ACTIVATION_REVIEW_PREPARED'
                or any(review[key] is not False for key in (
                    'independent_activation_approval_verified', 'spend_authorized', 'dispatchable', 'network_used'
                ))
                or review['review_blockers'] != [] or review['review_sha256'] != attempt['review_sha256']
                or digest(binding) != attempt['review_sha256'] or binding.get('object_chain_sha256') != chain_sha256
                or binding.get('preview_observation_sha256') != digest(review['preview_observation'])):
            fail('FIRST_LAUNCH_ACTIVATION_FROZEN_REVIEW_MISMATCH')
    if 'guard_effect' in attempt:
        effect = attempt['guard_effect']
        fields = tuple(key for key in ActivationGuard.__dataclass_fields__ if key not in ('observed_at', 'valid_until'))
        object_only(effect, fields, tuple(field for field in fields if field != 'selected_budget'))
        if (effect['review_sha256'] != attempt['review_sha256'] or digest(effect) != attempt['effect_sha256']
                or not aware_time(effect['enforced_start_at']) <= aware_time(attempt['reserved_at'])
                < aware_time(effect['enforced_stop_at'])):
            fail('FIRST_LAUNCH_ACTIVATION_EFFECT_MISMATCH')
    if attempt['state'] not in ('OUTCOME_UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED_ACTIVE', 'OBSERVED_PAUSED'):
        fail('FIRST_LAUNCH_ACTIVATION_ATTEMPT_STATE')
    row = attempt['observation']
    if row is not None:
        _validate_observation(row, attempt)
    if ((row is not None) != attempt['state'].startswith('OBSERVED_')
            or (row is not None and attempt['state'] != 'OBSERVED_' + row['configured_status'])):
        fail('FIRST_LAUNCH_ACTIVATION_ATTEMPT_STATE')


def _validate_observation(row, attempt):
    fields = ('attempt_id', 'review_sha256', 'account_id', 'ad_id', 'configured_status', 'observed_at', 'evidence_ref')
    object_only(row, (*fields, 'effective_status'), fields)
    effective = row.get('effective_status')
    if (any(row[key] != attempt[key] for key in ('attempt_id', 'review_sha256'))
            or any(type(row[key]) is not str or not NATIVE_ID.fullmatch(row[key]) for key in ('account_id', 'ad_id'))
            or row['configured_status'] not in ('ACTIVE', 'PAUSED')
            or (effective is not None and (type(effective) is not str or effective not in STATES))
            or aware_time(row['observed_at']) < aware_time(attempt['reserved_at'])
            or type(row['evidence_ref']) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', row['evidence_ref'])):
        fail('FIRST_LAUNCH_ACTIVATION_READBACK_MISMATCH')


def _ask_host(callback, *args, failure):
    try:
        return callback(*args)
    except Exception:
        fail(failure)


def activation_request(review):
    scope = review['binding']
    ad_id = scope.get('ad_id')
    if (digest(scope) != review['review_sha256'] or scope.get('parent_activation_included') is not False
            or type(ad_id) is not str or not NATIVE_ID.fullmatch(ad_id)):
        fail('FIRST_LAUNCH_ACTIVATION_EFFECT_SCOPE')
    return graph_post_request(f'/v27.0/{ad_id}', 'status=ACTIVE')


def activation_attempt_id(review, request_sha256, effect_sha256, permit_sha256):
    return digest([review['binding']['object_chain_sha256'], review['review_sha256'], request_sha256,
                   effect_sha256, permit_sha256])


def activation_operation(review, mode, state, save, provider, assert_current, clock):
    scope = review['binding']
    request = activation_request(review)
    request_sha256 = digest(request)
    attempt = state.get('activation_attempt')
    if attempt is not None and attempt['review_sha256'] != review['review_sha256']:
        fail('FIRST_LAUNCH_ACTIVATION_ORIGINAL_REVIEW_REQUIRED')
    if mode == 'dispatch':
        if attempt is not None:
            fail('FIRST_LAUNCH_ACTIVATION_RECOVER_ORIGINAL_ONLY')
        if review['review_blockers'] or review['preview_kind'] != 'AD':
            fail('FIRST_LAUNCH_ACTIVATION_REVIEW_BLOCKED')
        guard = _ask_host(provider.current_guard, deepcopy(review), failure='FIRST_LAUNCH_ACTIVATION_GUARD_UNAVAILABLE')
        _guard_valid(review, guard, clock())
        permit = _ask_host(provider.activation_permit, deepcopy(review), request_sha256, guard.effect_sha256,
                           failure='FIRST_LAUNCH_ACTIVATION_APPROVAL_UNAVAILABLE')
        _permit_valid(review, request_sha256, guard, permit, clock())
        assert_current()
        permit_sha256 = digest(asdict(permit))
        attempt = dict(object_chain_sha256=scope['object_chain_sha256'], review_sha256=review['review_sha256'],
                       request_sha256=request_sha256, effect_sha256=guard.effect_sha256, permit_sha256=permit_sha256,
                       attempt_id=activation_attempt_id(review, request_sha256, guard.effect_sha256, permit_sha256),
                       reserved_at=clock().isoformat(),
                       guard_effect=guard.effect, frozen_review=deepcopy(review),
                       state='OUTCOME_UNKNOWN', observation=None)
        state['activation_attempt'] = attempt
        save()
        fresh = _ask_host(provider.current_guard, deepcopy(review), failure='FIRST_LAUNCH_ACTIVATION_GUARD_UNAVAILABLE')
        _guard_valid(review, fresh, clock())
        current_permit = _ask_host(provider.activation_permit, deepcopy(review), request_sha256, fresh.effect_sha256,
                                   failure='FIRST_LAUNCH_ACTIVATION_APPROVAL_UNAVAILABLE')
        assert_current()
        _permit_valid(review, request_sha256, fresh, current_permit, clock())
        if current_permit != permit:
            fail('FIRST_LAUNCH_ACTIVATION_APPROVAL_CHANGED')
        acknowledged = _ask_host(provider.send_activation, deepcopy(review), fresh, current_permit,
                                 attempt['attempt_id'], request.copy(), failure='FIRST_LAUNCH_ACTIVATION_OUTCOME_UNKNOWN')
        if acknowledged is True:
            attempt['state'] = 'ACKNOWLEDGED'
            save()
    elif mode == 'recover':
        if attempt is None:
            fail('FIRST_LAUNCH_ACTIVATION_ATTEMPT_REQUIRED')
        row = _ask_host(provider.read_activation, deepcopy(review), attempt['attempt_id'],
                        failure='FIRST_LAUNCH_ACTIVATION_READBACK_UNAVAILABLE')
        assert_current()
        if row is not None:
            _validate_observation(row, attempt)
            previous = attempt['observation']
            if (row['ad_id'] != scope['ad_id'] or row['account_id'] != scope['account_id']
                    or aware_time(row['observed_at']) > clock()
                    or (previous is not None and aware_time(row['observed_at']) < aware_time(previous['observed_at']))):
                fail('FIRST_LAUNCH_ACTIVATION_READBACK_MISMATCH')
            if row != previous:
                attempt.update(state='OBSERVED_' + row['configured_status'], observation=row)
                save()
    return dict(status='FIRST_LAUNCH_ACTIVATION_SCOPE', review_sha256=review['review_sha256'],
                request=request, request_sha256=request_sha256, review_blockers=review['review_blockers'],
                attempt=attempt, parent_changes_included=False, delivery_verified=False,
                dispatchable=False, network_used=False if mode == 'plan' else None)
