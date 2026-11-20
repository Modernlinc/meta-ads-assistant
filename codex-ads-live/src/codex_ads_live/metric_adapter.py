"""One metric per bounded request; no source refresh time inferred from polling.

The HTTPS interface remains blocked by REAL_LIVE_CONTRACT_PENDING. Synthetic
collection exercises the same plans and monitor lifecycle without credentials.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

from .analysis import METRICS, live_metric
from .live_contract import metric_observations
from .read_adapter import MockTransport, identifier, positive_integer, post_identifier
from .validation import InputError, fail

METRIC_CHANNELS = dict(zip(('live_current_viewers', 'live_views', 'live_social_actions', 'live_views_by_status'), METRICS))
RECORD_KEYS = ('metric', 'object_id', 'state', 'value', 'period', 'observed_at', 'data_as_of', 'bucket_end_time')


@dataclass(frozen=True)
class MetricReadPlan:
    object_id: str
    page_id: str
    live_id: str
    metric: str
    max_age_seconds: int = 120
    version: str = 'v27.0'
    operation: str = 'video_insights'

    @property
    def account_id(self): return None

    def __post_init__(self):
        for value in (self.object_id, self.page_id, self.live_id): identifier(value)
        if self.metric not in METRICS or self.version != 'v27.0' or self.operation != 'video_insights': fail('METRIC_PLAN_UNSUPPORTED')
        positive_integer(self.max_age_seconds, 86400)

    def request(self, after=None):
        if after is not None: fail('METRIC_PAGINATION_UNSUPPORTED')
        return dict(method='GET', path=f'/{self.version}/{self.object_id}/video_insights', params={'metric': self.metric})


@dataclass(frozen=True, kw_only=True)
class PostMetricReadPlan(MetricReadPlan):
    """Four post-video metrics bound to one Page Post and its Live Video."""
    video_id: str
    operation: str = 'post_insights'

    def __post_init__(self):
        for value in (self.page_id,self.live_id,self.video_id):
            identifier(value)
        post_identifier(self.object_id,self.page_id)
        if self.metric not in METRICS or self.version!='v27.0' or self.operation!='post_insights':
            fail('METRIC_PLAN_UNSUPPORTED')
        positive_integer(self.max_age_seconds,86400)

    def request(self, after=None):
        if after is not None:
            fail('METRIC_PAGINATION_UNSUPPORTED')
        return dict(method='GET',path=f'/{self.version}/{self.object_id}/insights',params={'metric':self.metric})


METRIC_PLAN_TYPES = (MetricReadPlan,PostMetricReadPlan)


def validate_observation(plan, row):
    if type(row) is not dict: fail('METRIC_OBSERVATION_TYPE')
    record = {key: row[key] for key in RECORD_KEYS if key in row}
    if record.get('state') != 'VALUE': record.pop('value', None)
    normalized = live_metric(record, row.get('as_of'), plan.max_age_seconds)
    if (normalized != row or row['metric'] != plan.metric or row['object_id'] != plan.object_id
            or row['data_as_of'] is not None): fail('METRIC_OBSERVATION_CONTRACT')
    return normalized


class MetricReadSession:
    """Complete response or error, never silently discard additional pages."""
    def __init__(self, plan, transport, clock=None):
        from .http_read import GraphReadTransport
        if type(plan) not in METRIC_PLAN_TYPES: fail('METRIC_PLAN_UNSUPPORTED')
        if type(transport) not in (MockTransport, GraphReadTransport): fail('UNREVIEWED_TRANSPORT')
        if type(transport) is GraphReadTransport and plan != transport.plan: fail('READ_PLAN_BINDING_CHANGED')
        self.mode = 'MOCK' if type(transport) is MockTransport else 'READ_ONLY'
        self.plan = plan; self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.rows = []; self.pages = 0; self.state = 'NOT_RUN'; self.last_error = None

    def collect(self, page_budget=1):
        positive_integer(page_budget, 20)
        if self.state == 'COMPLETE': return self.report()
        if self.state == 'ERROR': fail('EXPLICIT_REVIEW_REQUIRED')
        try:
            status, body = self.transport.get(self.plan.request())
            stamp = self.clock()
            result = metric_observations(self.plan.metric, self.plan.object_id, status, body,
                                         stamp, stamp, self.plan.max_age_seconds, post=type(self.plan) is PostMetricReadPlan)
            rows = result['observations']
            for row in rows: validate_observation(self.plan, row)
            self.rows = rows
            if result['state'] == 'ERROR':
                self.state = 'ERROR'; self.last_error = result['error_code']
            else: self.state = 'COMPLETE'; self.pages = 1
        except InputError as exc:
            self.state = 'ERROR'; self.last_error = str(exc)
        return self.report()

    def report(self):
        return dict(mode=self.mode, operation=self.plan.operation, state=self.state, pages=self.pages,
                    rows=deepcopy(self.rows), last_error=self.last_error,
                    coverage='ONE_BOUNDED_METRIC_RESPONSE_NOT_SUMMABLE_BUCKETS', real_connection_verified=False)
