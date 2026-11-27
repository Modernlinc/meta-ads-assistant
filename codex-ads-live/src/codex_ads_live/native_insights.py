"""Quote native Meta Insights at the requested object and reporting granularity."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from decimal import Decimal
import re
import sys

from . import client_workspace as workspace
from .connection import _debug, _get_connection_json, _MemoryBroker, utcnow, verified_metadata
from .credential_record import RecordGuard
from .http_read import GraphReadTransport, ReadBinding
from .journal import Journal, digest
from .local_paths import checked_path
from .meta_write_mapping import _write_new
from .monitor_tools import new_directory
from .native_credentials import NativeCredentialBroker
from .read_adapter import INSIGHTS_MAX_DAYS, error_category, normalize_action_stats, numeric_string
from .validation import InputError, aware_time, fail, object_only

LEVELS = ("account", "campaign", "adset", "ad")
DEFAULT_FIELDS = ("spend", "impressions", "clicks", "reach", "frequency")
ACTION_FIELDS = ("actions", "action_values")
INSIGHTS_FIELDS = (*DEFAULT_FIELDS, "inline_link_clicks", "unique_clicks", "cpm", "ctr", *ACTION_FIELDS)
DECIMAL_FIELDS = frozenset(("spend", "frequency", "cpm", "ctr"))
BREAKDOWNS = ("age", "gender", "publisher_platform", "platform_position", "impression_device")
CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,2048}")
MAX_JOB_ROWS = 2000
MAX_JOB_PAGES = 60


@dataclass(frozen=True)
class NativeInsightsPlan:
    account_id: str
    level: str
    object_id: str
    since: str
    until: str
    breakdowns: tuple = ()
    limit: int = 40
    fields: tuple = DEFAULT_FIELDS
    action_report_time: str = "impression"
    daily: bool = False
    operation: str = "native-insights"
    page_id: str | None = None

    def __post_init__(self):
        workspace.numeric(self.account_id)
        workspace.numeric(self.object_id)
        if self.level not in LEVELS or ((self.level == "account") != (self.object_id == self.account_id)):
            fail("NATIVE_INSIGHTS_OBJECT_SCOPE")
        if self.operation != "native-insights" or self.page_id is not None:
            fail("NATIVE_INSIGHTS_PLAN_SCHEMA")
        for day in (self.since, self.until):
            if type(day) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                fail("INVALID_DATE_RANGE")
        try:
            difference = (date.fromisoformat(self.until) - date.fromisoformat(self.since)).days
        except ValueError:
            fail("INVALID_DATE_RANGE")
        if not 0 <= difference < INSIGHTS_MAX_DAYS:
            fail("DATE_RANGE_OUTSIDE_LOCAL_BUDGET")
        if (type(self.breakdowns) is not tuple or len(self.breakdowns)!=len(set(self.breakdowns))
                or any(v not in BREAKDOWNS for v in self.breakdowns) or len(self.breakdowns)>2
                or (len(self.breakdowns)==2 and set(self.breakdowns)!={"publisher_platform","platform_position"})):
            fail("NATIVE_INSIGHTS_BREAKDOWN_SCOPE")
        if (type(self.fields) is not tuple or not self.fields or any(v not in INSIGHTS_FIELDS for v in self.fields)
                or len(self.fields)!=len(set(self.fields)) or type(self.limit) is not int or not 1 <= self.limit <= 100
                or self.action_report_time not in ("impression", "conversion") or type(self.daily) is not bool):
            fail("NATIVE_INSIGHTS_PLAN_SCHEMA")

    def request(self, after=None):
        node = "act_" + self.account_id if self.level == "account" else self.object_id
        ids = ("account_id",) if self.level == "account" else ("account_id", self.level + "_id")
        params = dict(fields=",".join((*ids, "date_start", "date_stop", "account_currency", *self.fields)),
                      level=self.level, limit=self.limit, time_range={"since": self.since, "until": self.until},
                      use_unified_attribution_setting=True, action_report_time=self.action_report_time)
        if set(ACTION_FIELDS) & set(self.fields):
            if self.breakdowns:
                fail('NATIVE_INSIGHTS_ACTION_BREAKDOWN_COMBINATION')
            params.pop('use_unified_attribution_setting')
            params.update(action_attribution_windows=['7d_click'], use_account_attribution_setting=False,
                          use_unified_attribution_setting=False)
        if self.daily:
            params["time_increment"] = 1
        if self.breakdowns:
            params["breakdowns"] = ",".join(self.breakdowns)
        if after is not None:
            if type(after) is not str or not CURSOR.fullmatch(after):
                fail("INVALID_CURSOR")
            params["after"] = after
        return dict(method="GET", path=f"/v27.0/{node}/insights", params=params)


@dataclass(frozen=True)
class ReportRunPlan:
    query: NativeInsightsPlan
    action: str
    report_id: str | None = None

    def __post_init__(self):
        if type(self.query) is not NativeInsightsPlan or self.action not in ('SUBMIT', 'STATUS', 'RESULT'):
            fail('NATIVE_INSIGHTS_JOB_CONTRACT')
        if self.action == 'SUBMIT':
            if self.report_id is not None:
                fail('NATIVE_INSIGHTS_JOB_CONTRACT')
        else:
            workspace.numeric(self.report_id)

    @property
    def operation(self):
        return 'native-insights-async'

    @property
    def account_id(self):
        return self.query.account_id

    @property
    def object_id(self):
        return self.query.object_id

    @property
    def page_id(self):
        return None

    def request(self, after=None):
        if self.action == 'RESULT':
            params = {'limit': self.query.limit}
            if after is not None:
                self.query.request(after)
                params['after'] = after
            return dict(method='GET', path=f'/v27.0/{self.report_id}/insights', params=params)
        if after is not None:
            fail('INVALID_CURSOR')
        if self.action == 'STATUS':
            return dict(method='GET', path=f'/v27.0/{self.report_id}',
                        params=dict(fields='id,account_id,async_status,async_percent_completion'))
        return dict(self.query.request(), method='POST')


def _row(plan, raw, currency):
    if type(raw) is not dict or raw.get("account_id") != plan.account_id:
        fail("NATIVE_INSIGHTS_ACCOUNT_MISMATCH")
    if plan.level != "account" and raw.get(plan.level + "_id") != plan.object_id:
        fail("NATIVE_INSIGHTS_OBJECT_MISMATCH")
    first, last = raw.get("date_start"), raw.get("date_stop")
    valid_window = ((first, last) == (plan.since, plan.until) if not plan.daily else
                    type(first) is str and first == last and plan.since <= first <= plan.until
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", first))
    if valid_window:
        try:
            date.fromisoformat(first)
        except ValueError:
            valid_window = False
    if not valid_window or raw.get("account_currency") not in (None, currency):
        fail("NATIVE_INSIGHTS_CONTEXT_MISMATCH")
    dimensions = {}
    for label in plan.breakdowns:
        value = raw.get(label)
        if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_+ .-]{1,80}", value):
            fail("NATIVE_INSIGHTS_BREAKDOWN_VALUE")
        dimensions[label] = value
    metrics = {}
    for field in plan.fields:
        value = raw.get(field)
        if value is None:
            metrics[field] = dict(state='NULL' if field in raw else 'FIELD_ABSENT', value=None)
        elif field in ACTION_FIELDS:
            data = normalize_action_stats(value, field)
            metrics[field] = dict(state='PRESENT' if data else 'NO_MATCHING_WEBSITE_ACTION_IN_WINDOW', value=data)
        else:
            metrics[field] = dict(state='PRESENT', value=numeric_string(value, integer=field not in DECIMAL_FIELDS))
    return dict(dimensions=dimensions, metrics=metrics, since=first, until=last)


def _identity(row):
    return (row['since'], *sorted(row['dimensions'].items()))


def collect_rows(plan, transport, currency, page_budget, *, request=None, continuation=None, save_page=None):
    if type(page_budget) is not int or not 1 <= page_budget <= 10:
        fail("NATIVE_INSIGHTS_PAGE_BUDGET")
    request = request or plan.request
    prior = continuation or dict(rows=[], cursors=[], after=None, pages=0, complete=False)
    rows = {_identity(row): row for row in prior['rows']}
    cursors, cursor, pages = set(prior['cursors']), prior['after'], prior['pages']
    error = None
    state = 'COMPLETE' if prior['complete'] else 'PARTIAL'
    available = min(page_budget, MAX_JOB_PAGES-pages) if not prior['complete'] else 0
    for _ in range(available):
        try:
            status, body = transport.get(request(cursor))
            if not 200 <= status < 300 or "error" in body:
                fail(error_category(status, body))
            raw = body.get("data")
            if type(raw) is not list or len(raw) > plan.limit:
                fail("NATIVE_INSIGHTS_RESPONSE_FORMAT")
            paging = body.get("paging", {})
            if type(paging) is not dict:
                fail("NATIVE_INSIGHTS_RESPONSE_FORMAT")
            next_cursor = None
            if paging.get("next"):
                hint = paging.get("cursors")
                next_cursor = hint.get("after") if type(hint) is dict else None
                if not next_cursor or next_cursor in cursors:
                    fail("INVALID_CURSOR")
                request(next_cursor)
            candidate = rows.copy()
            for raw_row in raw:
                row = _row(plan, raw_row, currency)
                if candidate.setdefault(_identity(row), row) != row:
                    fail("NATIVE_INSIGHTS_DUPLICATE_ROW")
            if len(candidate) > MAX_JOB_ROWS:
                fail('NATIVE_INSIGHTS_JOB_ROW_BUDGET')
            if next_cursor is not None:
                cursors.add(next_cursor)
            rows = candidate
            cursor = next_cursor
            pages += 1
            if save_page is not None:
                save_page(dict(rows=list(rows.values()), cursors=sorted(cursors), after=cursor,
                               pages=pages, complete=cursor is None))
            if cursor is None:
                state = "COMPLETE"
                break
        except InputError as failure:
            error = str(failure)
            state = "PARTIAL" if pages else "UNAVAILABLE"
            break
    if pages >= MAX_JOB_PAGES and state != 'COMPLETE':
        error = 'NATIVE_INSIGHTS_JOB_PAGE_BUDGET'
    return dict(basis="NATIVE_INSIGHTS", coverage=state, rows=list(rows.values()), pages_read=pages,
                error_code=error, metric_scope=("EXACT_NODE_BY_DAY_NONADDITIVE_REACH_FREQUENCY" if plan.daily
                              else "EXACT_NODE_AND_WINDOW_NONADDITIVE_REACH_FREQUENCY"))


def _job_report(plan, build_transport, state, clock, query_scope, currency, page_budget, resume):
    journal = Journal(state, mode='READ_ONLY', existing_only=resume, stage_snapshots=True)
    fields = ('query_scope', 'state', 'report_id', 'submitted_at', 'progress')

    def validate(record):
        object_only(record, fields, fields)
        if record['query_scope'] != query_scope:
            fail('NATIVE_INSIGHTS_JOB_SCOPE_CHANGED')
        aware_time(record['submitted_at'])
        progress = record['progress']
        if record['state'] == 'SUBMISSION_UNCONFIRMED' and record['report_id'] is None and progress is None:
            return
        if record['state'] != 'SUBMITTED':
            fail('NATIVE_INSIGHTS_JOB_CONTRACT')
        workspace.numeric(record['report_id'])
        if progress is None:
            return
        keys = ('rows', 'cursors', 'after', 'pages', 'complete')
        object_only(progress, keys, keys)
        rows, cursors = progress['rows'], progress['cursors']
        if (type(progress['pages']) is not int or not 1 <= progress['pages'] <= MAX_JOB_PAGES
                or type(progress['complete']) is not bool
                or type(rows) is not list or len(rows) > MAX_JOB_ROWS
                or type(cursors) is not list or len(cursors) > progress['pages']
                or any(type(cursor) is not str or not CURSOR.fullmatch(cursor) for cursor in cursors)
                or (progress['complete'] and progress['after'] is not None)
                or (not progress['complete'] and progress['after'] not in cursors)):
            fail('NATIVE_INSIGHTS_JOB_CHECKPOINT_FORMAT')
        for row in rows:
            object_only(row, ('dimensions', 'metrics', 'since', 'until'),
                        ('dimensions', 'metrics', 'since', 'until'))
            if type(row['dimensions']) is not dict or type(row['metrics']) is not dict:
                fail('NATIVE_INSIGHTS_JOB_CHECKPOINT_FORMAT')

    def exchange(named):
        adapter = build_transport(named)
        request = named.request()
        status, body = (adapter.submit_report(request) if named.action == 'SUBMIT' else adapter.get(request))
        if not 200 <= status < 300 or 'error' in body:
            fail(error_category(status, body))
        return body

    with journal.locked(existing_only=resume):
        saved, damaged = journal.load(validate)
        if damaged or journal.unpublished_snapshot_count():
            fail('NATIVE_INSIGHTS_JOB_CHECKPOINT_INVALID')
        if saved is None:
            if resume:
                fail('NATIVE_INSIGHTS_JOB_CHECKPOINT_REQUIRED')
            saved = dict(query_scope=query_scope, state='SUBMISSION_UNCONFIRMED',
                         report_id=None, submitted_at=clock().isoformat(), progress=None)
            journal.save(saved, validate)
            response = exchange(ReportRunPlan(plan, 'SUBMIT'))
            raw_id = response.get('report_run_id')
            ident = str(raw_id) if type(raw_id) is int else raw_id
            workspace.numeric(ident)
            saved = dict(saved, state='SUBMITTED', report_id=ident)
            journal.save(saved, validate)
        elif saved['state'] != 'SUBMITTED':
            fail('NATIVE_INSIGHTS_SUBMISSION_UNCONFIRMED_NO_AUTOMATIC_RETRY')
        if not aware_time(saved['submitted_at']) <= clock() < aware_time(saved['submitted_at']) + timedelta(days=29):
            fail('NATIVE_INSIGHTS_JOB_EXPIRED')
        ident = saved['report_id']
        status = exchange(ReportRunPlan(plan, 'STATUS', ident))
        if str(status.get('id')) != ident or str(status.get('account_id')) != plan.account_id:
            fail('NATIVE_INSIGHTS_JOB_SCOPE_CHANGED')
        job, percentage = status.get('async_status'), status.get('async_percent_completion')
        if job in ('Job Failed', 'Job Skipped'):
            return dict(status='NATIVE_INSIGHTS_JOB_FAILED', job=job, network_used=True)
        if job not in ('Job Not Started', 'Job Started', 'Job Running', 'Job Completed'):
            fail('NATIVE_INSIGHTS_JOB_STATUS_INVALID')
        if type(percentage) is not int or not 0 <= percentage <= 100:
            fail('NATIVE_INSIGHTS_JOB_STATUS_INVALID')
        if job != 'Job Completed' or percentage != 100:
            return dict(status='NATIVE_INSIGHTS_JOB_PENDING', job=job, percent=percentage, network_used=True)
        named = ReportRunPlan(plan, 'RESULT', ident)
        def save_page(point):
            journal.save(dict(saved, progress=point), validate)

        report = collect_rows(plan, build_transport(named), currency, page_budget, request=named.request,
                              continuation=saved['progress'], save_page=save_page)
        if report['coverage'] != 'COMPLETE':
            return dict(status='NATIVE_INSIGHTS_JOB_RESULTS_INCOMPLETE', coverage=report['coverage'],
                        pages_read=report['pages_read'], error_code=report['error_code'],
                        rows_preserved=len(report['rows']), network_used=True)
        return dict(status='NATIVE_INSIGHTS_JOB_RESULT', report=report, network_used=True)


OBJECT_FIELDS = {
    'campaign': 'id,account_id,status,effective_status,objective,spend_cap',
    'adset': 'id,account_id,status,effective_status,campaign_id,optimization_goal,promoted_object',
    'ad': 'id,account_id,status,effective_status,campaign_id,adset_id,creative{id}',
}


def _object_context(level, node, account, lookup):
    raw = lookup(node, OBJECT_FIELDS[level])
    if (raw.get('id'), raw.get('account_id')) != (node, account):
        fail('NATIVE_INSIGHTS_PARENT_RELATION_REQUIRED')
    context = dict(level=level, object_id=node, account_id=account)
    enumerations = ('status', 'effective_status', 'objective', 'optimization_goal')
    for item in enumerations:
        text = raw.get(item)
        if type(text) is str and re.fullmatch(r'[A-Z_]{2,80}', text):
            context[item] = text
    for name in ('campaign_id', 'adset_id'):
        if raw.get(name) is not None:
            context[name] = workspace.numeric(raw[name])
    if level == 'ad':
        creative = raw.get('creative')
        if type(creative) is dict and creative.get('id') is not None:
            context['creative_id'] = workspace.numeric(creative['id'])
        if context.get('adset_id') is not None:
            adset = _object_context('adset', context['adset_id'], account, lookup)
            if adset.get('campaign_id') != context.get('campaign_id'):
                fail('NATIVE_INSIGHTS_PARENT_RELATION_REQUIRED')
            context['adset'] = adset
    if level == 'adset':
        promoted = raw.get('promoted_object')
        if type(promoted) is dict:
            event = promoted.get('custom_event_type')
            field = 'CUSTOM_CONVERSION' if promoted.get('custom_conversion_id') else 'PLATFORM_EVENT'
            event_value = event if type(event) is str and re.fullmatch(r'[A-Z_]{2,80}', event) else None
            context['event'] = {
                'signal_type': field,
                'event': event_value,
                'pixel_id': workspace.numeric(promoted['pixel_id']) if promoted.get('pixel_id') else None,
                'custom_conversion_id': workspace.numeric(promoted['custom_conversion_id'])
                if promoted.get('custom_conversion_id') else None,
                'comparison_requires_event_semantics': field == 'CUSTOM_CONVERSION' or event_value is None,
            }
    return context


@contextmanager
def authorized_account_read(directory, profile, account, plan_hash, phrase, record_directory, clock):
    route = (profile.get('connection_onboarding') or {}).get('route')
    if route == 'host-connector':
        fail('META_HOST_REPORTING_REQUIRES_ORIGINAL_CONNECTOR')
    if route == 'system-user':
        from .system_user_read import scoped_session

        with scoped_session(directory,profile,account,plan_hash,phrase,record_directory,clock) as session:
            yield session
        return
    ref = profile["binding"] or {}
    intent = dict(account_id=account, app_id=profile["app_id"], credential_source="OS_REFERENCE",
                  principal_id=ref.get("principal_id"), credential_reference=ref.get("secret_ref"))
    scope = workspace.monitor_scope(profile, intent, clock)
    context = profile["checks"][workspace.asset_key("ACCOUNT", account)]
    currency, zone = context["currency"], context["timezone"]
    record_directory = record_directory or Path(directory) / "credential-record"
    guard = RecordGuard(record_directory, intent, ref["record_hash"])
    guard.check(existing_only=True)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        fail("NATIVE_INSIGHTS_INTERACTIVE_REQUIRED")
    if input(f"确认请键入 {phrase}：") != phrase:
        fail("NATIVE_INSIGHTS_NOT_CONFIRMED")
    now = clock()
    deadline = now + timedelta(minutes=5)
    provisional = ReadBinding(plan_hash, intent["app_id"], intent["principal_id"], now.isoformat(),
                              deadline.isoformat(), deadline.isoformat(), (), False, False, False)

    def checkpoint():
        guard.check(existing_only=True)
        if workspace.monitor_scope(workspace.read(directory, existing_only=True), intent, clock) != scope:
            fail("WORKSPACE_CHANGED_DURING_CHECK")
        if not now <= clock() < deadline:
            fail("CONNECTION_REVIEW_EXPIRED")

    def time_remaining():
        return (deadline - clock()).total_seconds()

    with NativeCredentialBroker(ref["secret_ref"], provisional, enabled=True).lease(provisional) as credential:
        metadata = verified_metadata(_debug(credential, checkpoint, clock=clock, time_remaining=time_remaining),
                                     intent, clock(), deadline)
        deadline = min(deadline, aware_time(metadata["effective_read_expires_at"]))
        def get(path):
            return _get_connection_json(path, credential, checkpoint, clock=clock, time_remaining=time_remaining)

        account_row = get(f"/v27.0/act_{account}?fields=id,account_id,currency,timezone_name")
        if (account_row.get("id"), account_row.get("account_id"), account_row.get("currency"),
                account_row.get("timezone_name")) != ("act_" + account, account, currency, zone):
            fail("NATIVE_INSIGHTS_ACCOUNT_CONTEXT_CHANGED")
        class ScopeGuard:
            def check(self):
                checkpoint()

        brokers = []
        def transport(for_plan):
            binding = ReadBinding(digest(asdict(for_plan)), intent["app_id"], intent["principal_id"],
                                  metadata["observed_at"], metadata["effective_read_expires_at"],
                                  deadline.isoformat(), tuple(metadata["scopes"]), True, True, True)
            broker = _MemoryBroker(credential, binding, ScopeGuard())
            brokers.append(broker)
            return GraphReadTransport(for_plan, binding, broker, clock=clock, time_remaining=time_remaining)

        try:
            yield get, transport, scope, currency, zone
            checkpoint()
        finally:
            for broker in brokers:
                broker._credential = None
            credential = None


def run(directory, output, level, identifier, since, until, *, account_id=None, breakdowns=(), page_budget=3,
        record_directory=None, execute=False, daily=False, report_mode="sync", job_state=None, resume=False,
        include_object_context=False, metrics=(), report_time="impression", clock=utcnow):
    from urllib.parse import urlencode

    profile = workspace.read(directory, existing_only=True)
    account = workspace.business_account(profile, account_id)
    target = account if level == "account" and identifier is None else identifier
    plan = NativeInsightsPlan(account, level, target, since, until, tuple(breakdowns), daily=daily,
                              fields=tuple(metrics) if metrics else DEFAULT_FIELDS, action_report_time=report_time)
    plan.request()
    if include_object_context and level not in OBJECT_FIELDS:
        fail('NATIVE_INSIGHTS_OBJECT_CONTEXT_REQUIRES_NAMED_OBJECT')
    if (report_mode not in ('sync', 'async') or (report_mode == 'async') != (job_state is not None)
            or resume and report_mode != 'async'):
        fail('NATIVE_INSIGHTS_JOB_OPTIONS')
    if job_state is not None:
        path_options = dict(link_error='STATE_PATH_SYMLINK', volume_error='STATE_VOLUME_NOT_MOUNTED')
        job_state = checked_path(job_state, **path_options)
        result_directory = checked_path(output, **path_options)
        if job_state.is_relative_to(result_directory) or result_directory.is_relative_to(job_state):
            fail('NATIVE_INSIGHTS_OUTPUT_JOB_STATE_CONFLICT')
    if not 1 <= page_budget <= 10:
        fail("NATIVE_INSIGHTS_PAGE_BUDGET")
    identity = workspace.workspace_identity(profile)
    if not execute:
        return dict(status="NATIVE_INSIGHTS_PREVIEW", request=plan.request(), account_id=account,
                    workspace_sha256=identity, max_report_pages=page_budget, network_used=False,
                    report_mode=report_mode, resume=resume,
                    object_context_fields=OBJECT_FIELDS.get(level) if include_object_context else None,
                    async_first_request=(ReportRunPlan(plan, 'SUBMIT').request()
                                         if report_mode == 'async' and not resume else None))
    phrase = f"READ {level.upper()} {target} ACCOUNT {account} {since} {until}"
    if report_mode == 'async':
        phrase += ' REPORT JOB RESUME' if resume else ' REPORT JOB CREATE'
        print('异步查询会创建或接续 Meta 报表任务。每次最多检查一次状态，不修改广告对象。')
    print(f"范围为一次身份核验、相应对象归属核验和最多 {page_budget} 页原生报表。")
    if include_object_context:
        print("本次另读取同一对象的归属、投递状态、广告组目标与事件配置。")
    plan_hash = digest(asdict(plan))
    with authorized_account_read(directory, profile, account, plan_hash, phrase, record_directory, clock) as session:
        get, transport, scope, currency, zone = session
        details = None
        if level != 'account':
            def lookup(obj, fields):
                return get(f'/v27.0/{workspace.numeric(obj)}?' + urlencode(dict(fields=fields)))

            if include_object_context:
                details = _object_context(level, target, account, lookup)
            else:
                relation = lookup(target, 'id,account_id')
                if (relation.get('id'), relation.get('account_id')) != (target, account):
                    fail('NATIVE_INSIGHTS_PARENT_RELATION_REQUIRED')
        if report_mode == 'async':
            job_result = _job_report(plan, transport, job_state, clock,
                                     digest(dict(plan_sha256=plan_hash, workspace_sha256=identity, scope=scope)),
                                     currency, page_budget, resume)
            if 'report' not in job_result:
                return job_result
            report = job_result['report']
        else:
            report = collect_rows(plan, transport(plan), currency, page_budget)
    result = dict(schema="native-insights-snapshot-v1", workspace_sha256=identity,
                  account_id=account, source_context={"query": asdict(plan), "currency": currency, "timezone": zone,
                                                      "scope_sha256": scope, "observed_at": clock().isoformat(),
                                                      "report_mode": report_mode},
                  report=report)
    if details is not None:
        result['object_context'] = details
    root = new_directory(output)
    file = _write_new(root, "NATIVE-INSIGHTS.json", dict(payload=result, sha256=digest(result)))
    value = dict(status="NATIVE_INSIGHTS_OBSERVED", report=report, snapshot=file, network_used=True)
    if details is not None:
        value['object_context'] = details
    return value


def commerce_spend(directory, filename, account, currency, zone, since, until, digits):
    profile = workspace.read(directory, existing_only=True)
    identity = workspace.workspace_identity(profile)
    path = checked_path(filename, link_error='STATE_PATH_SYMLINK', volume_error='STATE_VOLUME_NOT_MOUNTED')
    if type(digits) is not int or not 0 <= digits <= 4:
        fail('SHOPIFY_SPEND_MINOR_DIGITS_INVALID')

    def validate(snapshot):
        keys = ('schema', 'workspace_sha256', 'account_id', 'source_context', 'report')
        object_only(snapshot, keys, keys)
        if (snapshot['schema'], snapshot['workspace_sha256'], snapshot['account_id']) != (
                'native-insights-snapshot-v1', identity, account):
            fail('COMMERCE_NATIVE_SNAPSHOT_SCOPE')
        source = snapshot['source_context']
        object_only(source, ('query', 'currency', 'timezone', 'scope_sha256', 'observed_at', 'report_mode'),
                    ('query', 'currency', 'timezone', 'scope_sha256', 'observed_at', 'report_mode'))
        if (source['currency'], source['timezone']) != (currency, zone):
            fail('COMMERCE_NATIVE_SNAPSHOT_SCOPE')
        plan_fields = tuple(NativeInsightsPlan.__dataclass_fields__)
        query = source['query']
        object_only(query, plan_fields, plan_fields)
        plan = NativeInsightsPlan(**dict(query, breakdowns=tuple(query['breakdowns']), fields=tuple(query['fields'])))
        if (plan.account_id, plan.level, plan.object_id, plan.since, plan.until, plan.daily, plan.breakdowns) != (
                account, 'account', account, since, until, True, ()):
            fail('COMMERCE_NATIVE_SNAPSHOT_SCOPE')

    snapshot, snapshot_hash = Journal.read_snapshot(path, validate)
    report = snapshot['report']
    if report['coverage'] != 'COMPLETE':
        fail('COMMERCE_NATIVE_SNAPSHOT_INCOMPLETE')
    rows = {}
    for row in report['rows']:
        day = row['since']
        if (day != row['until'] or not since <= day <= until or row['dimensions'] or (day,) in rows):
            fail('COMMERCE_NATIVE_SNAPSHOT_SCOPE')
        spend = row['metrics'].get('spend', {})
        if spend.get('state') == 'PRESENT':
            amount = Decimal(numeric_string(spend['value'])).scaleb(digits)
            if amount != amount.to_integral_value() or not 0 <= amount <= 10**12:
                fail('SHOPIFY_SPEND_MINOR_CONVERSION')
            rows[(day,)] = dict(date=day, spend_minor=str(int(amount)))
    return rows, snapshot_hash


def add_command(sub):
    command = sub.add_parser("native-insights", help="读取指定账户、Campaign、Ad Set 或 Ad 的平台原生汇总")
    command.add_argument("--workspace", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--level", choices=LEVELS, required=True)
    command.add_argument("--id")
    command.add_argument("--account-id")
    command.add_argument("--since", required=True)
    command.add_argument("--until", required=True)
    command.add_argument("--breakdown", choices=BREAKDOWNS, action="append", default=[])
    command.add_argument("--page-budget", type=int, default=3)
    command.add_argument("--credential-record-dir", type=Path)
    command.add_argument("--daily", action="store_true")
    command.add_argument("--include-object-context", action="store_true")
    command.add_argument("--metric", action="append", choices=INSIGHTS_FIELDS, default=[])
    command.add_argument("--report-time", choices=("impression","conversion"), default="impression")
    command.add_argument("--report-mode", choices=("sync", "async"), default="sync")
    command.add_argument("--job-state", type=Path)
    command.add_argument("--resume", action="store_true")
    command.add_argument("--execute-read", action="store_true")
