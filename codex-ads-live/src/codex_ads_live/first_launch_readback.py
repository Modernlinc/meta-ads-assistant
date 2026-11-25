"""Read one independently authorized object for a recorded creation attempt."""

from dataclasses import dataclass
import re

from .first_launch_host import CREATION_READBACK_SCHEMA, HEX, NATIVE_ID, validate_creation_binding
from .meta_write_mapping import OBJECTIVES, WEBSITE_CONVERSION_OBJECTIVES, utcnow
from .read_adapter import error_category
from .validation import fail, validate_https_destination
from .write_snapshots import SnapshotPlan


LEVELS = {
    'CREATE_CAMPAIGN': 'CAMPAIGN',
    'CREATE_ADSET': 'ADSET',
    'CREATE_CREATIVE': 'CREATIVE',
    'CREATE_AD': 'AD',
}
READ_FIELDS = {
    'CAMPAIGN': 'id,account_id,status,objective',
    'ADSET': 'id,account_id,status,campaign_id,daily_budget,optimization_goal,promoted_object',
    'CREATIVE': 'id,account_id,object_story_spec',
    'AD': 'id,account_id,status,campaign_id,adset_id,creative{id}',
}


@dataclass(frozen=True, kw_only=True)
class CreationReadPlan(SnapshotPlan):
    attempt_id: str
    creation_binding_sha256: str

    def __post_init__(self):
        super().__post_init__()
        if self.level not in READ_FIELDS or any(
            type(value) is not str or not HEX.fullmatch(value)
            for value in (self.attempt_id, self.creation_binding_sha256)
        ):
            fail('FIRST_LAUNCH_GRAPH_READ_SCOPE')

    def request(self, after=None):
        request = super().request(after)
        request['params']['fields'] = READ_FIELDS[self.level]
        return request


@dataclass(frozen=True)
class CreationReadTarget:
    binding_sha256: str
    attempt_id: str
    object_id: str
    evidence_ref: str


class GraphCreationReadback:
    """Use a separately approved one-object GET. Unknown attempts stay open."""

    def __init__(self, resolve_target, transport_for, *, clock=utcnow):
        self._resolve_target = resolve_target
        self._transport_for = transport_for
        self._clock = clock

    def __call__(self, binding, attempt_id, acknowledged_id):
        from .http_read import GraphReadTransport

        validate_creation_binding(binding, allow_legacy=True)
        if type(attempt_id) is not str or not HEX.fullmatch(attempt_id):
            fail('FIRST_LAUNCH_GRAPH_READ_SCOPE')
        # The host may recover an acknowledgement already tied to this attempt.
        # A name match or account listing is insufficient to select an object.
        target = self._resolve_target(binding, attempt_id, acknowledged_id)
        if target is None:
            return None
        if (type(target) is not CreationReadTarget or target.binding_sha256 != binding.sha256
                or target.attempt_id != attempt_id or acknowledged_id not in (None, target.object_id)
                or type(target.evidence_ref) is not str
                or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', target.evidence_ref)):
            fail('FIRST_LAUNCH_GRAPH_READ_TARGET_MISMATCH')
        plan = CreationReadPlan(LEVELS[binding.operation], target.object_id, binding.account_id,
                                attempt_id=attempt_id, creation_binding_sha256=binding.sha256)
        transport = self._transport_for(binding, plan)
        if (type(transport) is not GraphReadTransport or transport.plan != plan
                or transport.binding.app_id != binding.app_id or transport.retry_rate_limit is not None):
            fail('FIRST_LAUNCH_GRAPH_READ_GRANT_MISMATCH')
        status, body = transport.get(plan.request())
        state, observed = _project(plan, binding.page_id, status, body)
        observation = dict(schema=CREATION_READBACK_SCHEMA, stage_sha256=binding.stage_sha256,
                           state=state, evidence_ref=target.evidence_ref,
                           observed_at=self._clock().isoformat(), object=observed)
        return dict(binding_sha256=binding.sha256, attempt_id=attempt_id,
                    source='INDEPENDENT_HOST_READBACK', observation=observation)


def _native_id(value):
    if type(value) is not str or not NATIVE_ID.fullmatch(value):
        fail('FIRST_LAUNCH_GRAPH_READ_OBJECT_ID')
    return value


def _project(plan, page_id, status, body):
    if type(status) is not int or not 100 <= status < 600 or type(body) is not dict:
        fail('FIRST_LAUNCH_GRAPH_READ_ENVELOPE')
    if not 200 <= status < 300 or 'error' in body:
        fail(error_category(status, body))
    if ('paging' in body or 'data' in body or body.get('id') != plan.object_id
            or body.get('account_id') != plan.account_id):
        fail('FIRST_LAUNCH_GRAPH_READ_OBJECT_MISMATCH')
    projected = dict(id=plan.object_id, account_id=plan.account_id)
    if plan.level == 'CREATIVE':
        story = body.get('object_story_spec')
        if type(story) is not dict or story.get('page_id') != page_id:
            fail('FIRST_LAUNCH_GRAPH_READ_CREATIVE_MISMATCH')
        story_kind = [key for key in ('link_data', 'video_data') if type(story.get(key)) is dict]
        if len(story_kind) != 1:
            fail('FIRST_LAUNCH_GRAPH_READ_CREATIVE_MISMATCH')
        creative = story[story_kind[0]]
        image = creative.get('image_hash')
        if type(image) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', image):
            fail('FIRST_LAUNCH_GRAPH_READ_CREATIVE_IMAGE')
        if story_kind[0] == 'video_data':
            cta = creative.get('call_to_action')
            value = cta.get('value') if type(cta) is dict else None
            url = value.get('link') if type(value) is dict else None
            projected.update(video_id=_native_id(creative.get('video_id')), thumbnail_hash=image)
        else:
            url = creative.get('link')
            projected.update(image_hash=image)
        validate_https_destination(url, 'FIRST_LAUNCH_GRAPH_READ_CREATIVE_URL')
        if (len(url) > 1000 or '\\' in url
                or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)):
            fail('FIRST_LAUNCH_GRAPH_READ_CREATIVE_URL')
        projected.update(page_id=page_id, destination_url=url)
        return 'CREATIVE_OBSERVED', projected
    state = body.get('status')
    if state not in ('PAUSED', 'DRAFT'):
        fail('FIRST_LAUNCH_GRAPH_READ_STATUS')
    projected['configured_status'] = state
    if plan.level == 'CAMPAIGN':
        objective = body.get('objective')
        if objective not in OBJECTIVES:
            fail('FIRST_LAUNCH_GRAPH_READ_OBJECTIVE')
        projected['objective'] = objective
    else:
        projected['campaign_id'] = _native_id(body.get('campaign_id'))
        if plan.level == 'ADSET':
            projected.update(_adset_observation(body))
        else:
            creative = body.get('creative')
            if type(creative) is not dict:
                fail('FIRST_LAUNCH_GRAPH_READ_CREATIVE_RELATION')
            projected.update(adset_id=_native_id(body.get('adset_id')), creative_id=_native_id(creative.get('id')))
    return state, projected


def _adset_observation(body):
    daily = body.get('daily_budget')
    goal = body.get('optimization_goal')
    if (type(daily) is not str or not re.fullmatch(r'[1-9][0-9]{0,8}', daily)
            or goal not in ('LINK_CLICKS', 'OFFSITE_CONVERSIONS')):
        fail('FIRST_LAUNCH_GRAPH_READ_ADSET')
    result = dict(daily_budget_minor=int(daily), optimization_goal=goal)
    if goal == 'OFFSITE_CONVERSIONS':
        promoted = body.get('promoted_object')
        if type(promoted) is not dict or promoted.get('custom_event_type') not in tuple(WEBSITE_CONVERSION_OBJECTIVES):
            fail('FIRST_LAUNCH_GRAPH_READ_ADSET_EVENT')
        result['promoted_object'] = dict(pixel_id=_native_id(promoted.get('pixel_id')),
                                         custom_event_type=promoted['custom_event_type'])
    return result
