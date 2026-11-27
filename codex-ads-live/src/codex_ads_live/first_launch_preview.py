"""Prepare or present one preview of a recorded first-launch object."""

from dataclasses import asdict, dataclass
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urlsplit

from .first_launch_host import CREATION_READBACK_REMOTE_FAILURES, HEX, NATIVE_ID
from .journal import digest
from .meta_write_mapping import _write_new, utcnow
from .monitor_tools import new_directory
from .read_adapter import error_category
from .validation import InputError, fail
from .write_snapshots import SnapshotPlan


PREVIEW_FORMAT = 'MOBILE_FEED_STANDARD'
PREVIEW_ORIGIN = 'www.facebook.com'
PREVIEW_PATH = '/ads/api/preview_iframe.php'


@dataclass(frozen=True, kw_only=True)
class PreviewReadPlan(SnapshotPlan):
    app_id: str
    workspace_sha256: str
    object_chain_sha256: str

    def __post_init__(self):
        super().__post_init__()
        identifiers = ((self.app_id, NATIVE_ID), (self.workspace_sha256, HEX), (self.object_chain_sha256, HEX))
        if self.level not in ('AD', 'CREATIVE') or any(
            type(value) is not str or not pattern.fullmatch(value) for value, pattern in identifiers
        ):
            fail('FIRST_LAUNCH_PREVIEW_SCOPE_MISMATCH')

    def request(self, after=None):
        request = super().request(after)
        return dict(method=request['method'], path=request['path'] + '/previews',
                    params=dict(ad_format=PREVIEW_FORMAT, fields='body', limit=1))


class GraphFirstLaunchPreview:
    """Present an authorized preview in the host's own approval surface."""

    def __init__(self, transport_for, present):
        self._transport_for = transport_for
        self._present = present

    def __call__(self, plan, assert_current):
        from .http_read import GraphReadTransport

        transport = self._transport_for(plan)
        if (type(transport) is not GraphReadTransport or transport.plan != plan
                or transport.binding.app_id != plan.app_id or transport.retry_rate_limit is not None):
            fail('FIRST_LAUNCH_PREVIEW_GRANT_MISMATCH')
        status, body = transport.get(plan.request())
        frame = _preview_frame(status, body)
        assert_current()
        return self._present(plan, frame)


class _PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag != 'iframe':
            return
        sources = [value for name, value in attrs if name == 'src']
        self.sources.append(sources[0] if len(sources) == 1 else None)


def _preview_frame(status, body):
    if type(status) is not int or not 100 <= status < 600 or type(body) is not dict:
        fail('FIRST_LAUNCH_PREVIEW_RESPONSE_MISMATCH')
    if not 200 <= status < 300 or 'error' in body:
        fail(error_category(status, body))
    data = body.get('data')
    if data == []:
        fail('FIRST_LAUNCH_PREVIEW_NOT_RETURNED')
    if (type(data) is not list or len(data) != 1 or type(data[0]) is not dict
            or type(data[0].get('body')) is not str or len(data[0]['body']) > 32_000):
        fail('FIRST_LAUNCH_PREVIEW_RESPONSE_MISMATCH')
    parser = _PreviewParser()
    parser.feed(data[0]['body'])
    parser.close()
    if len(parser.sources) != 1 or not parser.sources[0]:
        fail('FIRST_LAUNCH_PREVIEW_FRAME_UNSUPPORTED')
    source = parser.sources[0]
    if not source.isascii() or any(character.isspace() or ord(character) < 33 for character in source):
        fail('FIRST_LAUNCH_PREVIEW_FRAME_UNSUPPORTED')
    try:
        parts = urlsplit(source)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError:
        fail('FIRST_LAUNCH_PREVIEW_FRAME_UNSUPPORTED')
    arguments = dict(pairs)
    if (parts.scheme != 'https' or parts.netloc != PREVIEW_ORIGIN or parts.path != PREVIEW_PATH
            or parts.fragment or not {'d'} <= arguments.keys() <= {'d', 'locale'}
            or len(arguments) != len(pairs) or not arguments['d']):
        fail('FIRST_LAUNCH_PREVIEW_FRAME_UNSUPPORTED')
    return (f'<iframe src="{escape(source, quote=True)}" title="Meta ad preview" '
            'width="420" height="740" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>')


def preview(workspace, directory, *, creative_fallback=False, fetch=False, output=None, provider=None,
            ad_id=None, clock=utcnow):
    from .first_launch_progress import progress

    if output is not None and not fetch:
        fail('FIRST_LAUNCH_PREVIEW_OUTPUT_REQUIRES_FETCH')
    launch = progress(workspace, directory, ad_id=ad_id)
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    target = launch['preview_target']
    kind = 'CREATIVE' if creative_fallback else 'AD'
    object_id = target['fallback_creative_id'] if creative_fallback else target['ad_id']
    plan = PreviewReadPlan(kind, object_id, launch['account_id'], app_id=launch['app_id'],
                           workspace_sha256=launch['workspace_identity_sha256'],
                           object_chain_sha256=launch['object_chain_sha256'])
    observation = dict(launch['preview_observation_template'], kind=kind, object_id=object_id)
    result = dict(status='FIRST_LAUNCH_PREVIEW_PLANNED', plan=asdict(plan), request=plan.request(),
                  request_plan_sha256=digest(asdict(plan)), native_preview=False, observation=observation,
                  ad_preview_required=creative_fallback, human_review_verified=False,
                  spend_authorized=False, network_used=False)
    if not fetch:
        return result
    if provider is None:
        fail('FIRST_LAUNCH_PREVIEW_TRUSTED_HOST_NOT_CONFIGURED')

    def assert_current():
        current = progress(workspace, directory, ad_id=ad_id)
        keys = ('status', 'app_id', 'workspace_identity_sha256', 'object_chain_sha256')
        if any(current.get(key) != launch[key] for key in keys):
            fail('FIRST_LAUNCH_PREVIEW_SCOPE_CHANGED')

    try:
        reference = provider(plan, assert_current)
    except InputError as exc:
        code = str(exc)
        if code in CREATION_READBACK_REMOTE_FAILURES:
            fail('FIRST_LAUNCH_PREVIEW_' + code)
        if code in ('FIRST_LAUNCH_PREVIEW_SCOPE_CHANGED', 'FIRST_LAUNCH_PREVIEW_NOT_RETURNED',
                    'FIRST_LAUNCH_PREVIEW_GRANT_MISMATCH', 'FIRST_LAUNCH_PREVIEW_FRAME_UNSUPPORTED'):
            fail(code)
        fail('FIRST_LAUNCH_PREVIEW_UNAVAILABLE')
    except Exception:
        fail('FIRST_LAUNCH_PREVIEW_UNAVAILABLE')
    assert_current()
    if type(reference) is not str or not re.fullmatch(r'EV[-_][A-Za-z0-9_-]{1,48}', reference):
        fail('FIRST_LAUNCH_PREVIEW_REFERENCE_REQUIRED')
    observation.update(observed_at=clock().isoformat(), evidence_ref=reference)
    result.update(status='FIRST_LAUNCH_PREVIEW_OFFERED_FOR_REVIEW', native_preview=True, network_used=True)
    if output is not None:
        result['observation_file'] = _write_new(new_directory(output), 'preview-observation.json', observation)
    return result
