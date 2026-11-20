"""Bounded read-before-write snapshots; no writes or credential enrollment.

The live seam uses the existing authorized GET transport. File imports prove
local consistency only; neither hashes nor this module grant write permission.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import os
import re
import time

from .journal import canonical, digest
from .local_paths import checked_path
from .meta_write_mapping import SNAPSHOT_OPERATIONS, adset_parent_objective, validate_draft, review_draft, utcnow, _write_new
from .read_adapter import STATES, error_category
from .validation import aware_time, fail, object_only, strict_json

FIELDS = {
    'ACCOUNT': ('id', 'account_id', 'currency', 'timezone_name', 'account_status'),
    'CAMPAIGN': ('id', 'account_id', 'status', 'effective_status', 'objective', 'buying_type',
                 'special_ad_categories', 'daily_budget', 'lifetime_budget'),
    'ADSET': ('id', 'account_id', 'campaign_id', 'status', 'effective_status', 'daily_budget',
              'lifetime_budget', 'billing_event', 'optimization_goal', 'bid_strategy', 'destination_type'),
    'AD': ('id', 'account_id', 'campaign_id', 'adset_id', 'status', 'effective_status', 'creative'),
    'CREATIVE': ('id', 'account_id', 'actor_id'),
    'PAGE': ('id',),
}
_CAPTURE_KEY = object()


def _id(value):
    if type(value) is not str or not re.fullmatch('[1-9][0-9]{0,39}', value): fail('SNAPSHOT_NUMERIC_ID_REQUIRED')
    return value


@dataclass(frozen=True)
class SnapshotPlan:
    level: str
    object_id: str
    account_id: str | None = None
    page_id: str | None = None
    version: str = 'v27.0'

    @property
    def operation(self): return 'page' if self.level == 'PAGE' else 'write_snapshot'

    def __post_init__(self):
        if type(self.level) is not str or self.level not in FIELDS or self.version != 'v27.0': fail('SNAPSHOT_PLAN_PROFILE')
        _id(self.object_id)
        if self.level == 'PAGE':
            if self.account_id is not None or self.page_id != self.object_id: fail('SNAPSHOT_PAGE_SCOPE')
        else:
            _id(self.account_id)
            if self.page_id is not None: fail('SNAPSHOT_UNUSED_SCOPE')
            if self.level == 'ACCOUNT' and self.object_id != self.account_id: fail('SNAPSHOT_ACCOUNT_SCOPE')

    def request(self, after=None):
        if after is not None: fail('SNAPSHOT_PAGING_REFUSED')
        fields = ','.join(FIELDS[self.level])
        if self.level == 'AD': fields = fields.replace('creative', 'creative{id}')
        return dict(method='GET', path='/v27.0/' + ('act_' if self.level == 'ACCOUNT' else '') + self.object_id,
                    params=dict(fields=fields))


def normalize_snapshot(plan, status, body):
    if type(status) is not int or not 100 <= status < 600 or type(body) is not dict: fail('SNAPSHOT_RESPONSE_ENVELOPE')
    if 'error' in body or not 200 <= status < 300: fail('SNAPSHOT_' + error_category(status, body))
    if 'paging' in body or 'data' in body: fail('SNAPSHOT_SINGLE_OBJECT_REQUIRED')
    # Missing/null is not zero. Required core fields are checked below, and
    # operation-specific budget fields are checked when binding the review.
    row = {key: deepcopy(body[key]) for key in FIELDS[plan.level] if key in body and body[key] is not None}
    if row.get('id') != ('act_' if plan.level == 'ACCOUNT' else '') + plan.object_id: fail('SNAPSHOT_OBJECT_MISMATCH')
    if plan.level != 'PAGE' and row.get('account_id') != plan.account_id: fail('SNAPSHOT_ACCOUNT_MISMATCH')
    for key, value in row.items():
        if key in ('account_id', 'campaign_id', 'adset_id', 'actor_id'): _id(value)
        elif key == 'creative':
            if type(value) is not dict or 'id' not in value: fail('SNAPSHOT_CREATIVE_RELATION')
            row[key] = {'id': _id(value['id'])}
        elif key in ('daily_budget', 'lifetime_budget'):
            if type(value) is not str or not re.fullmatch('0|[1-9][0-9]{0,17}', value): fail('SNAPSHOT_BUDGET_REPRESENTATION')
        elif key in ('status', 'effective_status'):
            if type(value) is not str or value not in STATES: fail('SNAPSHOT_STATUS_UNKNOWN')
        elif key == 'account_status':
            if type(value) is not int or not 0 <= value < 100: fail('SNAPSHOT_ACCOUNT_STATUS')
        elif key == 'currency':
            if type(value) is not str or not re.fullmatch('[A-Z]{3}', value): fail('SNAPSHOT_CURRENCY')
        elif key == 'timezone_name':
            if type(value) is not str or not re.fullmatch('[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', value) or len(value) > 100:
                fail('SNAPSHOT_TIMEZONE')
        elif key == 'special_ad_categories':
            if type(value) is not list or len(value) > 10 or any(type(x) is not str or not re.fullmatch('[A-Z_]{1,60}', x) for x in value):
                fail('SNAPSHOT_SPECIAL_CATEGORIES')
            if len(set(value)) != len(value): fail('SNAPSHOT_SPECIAL_CATEGORIES')
            row[key] = sorted(value)
        elif key not in ('id',):
            if type(value) is not str or not re.fullmatch('[A-Z0-9_]{1,80}', value): fail('SNAPSHOT_ENUM_FORMAT')
    required = {'ACCOUNT': FIELDS['ACCOUNT'], 'CAMPAIGN': ('id', 'account_id', 'status', 'effective_status', 'objective', 'buying_type', 'special_ad_categories'),
                'ADSET': ('id', 'account_id', 'campaign_id', 'status', 'effective_status'),
                'AD': FIELDS['AD'], 'CREATIVE': FIELDS['CREATIVE'], 'PAGE': ('id',)}[plan.level]
    if any(key not in row for key in required): fail('SNAPSHOT_REQUIRED_FIELD_MISSING')
    if re.search(r'EAA[A-Za-z0-9]{40,}|sk-proj-|PRIVATE KEY|(?:access_token|app_secret|cookie)\s*[:=]', canonical(row), re.I):
        fail('SNAPSHOT_SECRET_PATTERN_REFUSED')
    return row


def validate_allowlist(value):
    keys = ('account_id', 'CAMPAIGN', 'ADSET', 'AD', 'CREATIVE', 'PAGE')
    object_only(value, keys, keys); _id(value['account_id'])
    for key in keys[1:]:
        ids = value[key]
        if type(ids) is not list or len(ids) > 25: fail('SNAPSHOT_ALLOWLIST_BUDGET')
        for item in ids: _id(item)
        if len(set(ids)) != len(ids): fail('SNAPSHOT_ALLOWLIST_DUPLICATE')
    return value


def _plan(level, obj, allowlist):
    _id(obj)
    if level == 'ACCOUNT':
        if obj != allowlist['account_id']: fail('SNAPSHOT_NOT_ALLOWLISTED')
    elif obj not in allowlist[level]: fail('SNAPSHOT_NOT_ALLOWLISTED')
    return SnapshotPlan(level, obj, page_id=obj) if level == 'PAGE' else SnapshotPlan(level, obj, account_id=allowlist['account_id'])


def _walk(draft, read):
    """Shared traversal for collection and verification. No arbitrary URL following."""
    rows = {'account': read('ACCOUNT', draft['account_id'])}
    op = draft['operation']; params = draft['parameters']
    if op == 'CREATE_ADSET': rows['parent'] = read('CAMPAIGN', params['campaign_id'])
    elif op == 'CREATE_CREATIVE':
        rows['page'] = read('PAGE', draft['context']['page_id'])
    elif op == 'CREATE_AD':
        rows['parent'] = read('ADSET', params['adset_id'])
        rows['campaign'] = read('CAMPAIGN', rows['parent']['campaign_id'])
        creative = params['creative']
        if type(creative) is not dict: fail('SNAPSHOT_CREATIVE_REQUIRED')
        rows['creative'] = read('CREATIVE', creative['creative_id'])
        rows['page'] = read('PAGE', draft['context']['page_id'])
        if rows['creative']['actor_id'] != rows['page']['id']: fail('SNAPSHOT_CREATIVE_PAGE_MISMATCH')
    elif not op.startswith('CREATE_'):
        level = draft['target_level']
        if level not in ('CAMPAIGN', 'ADSET', 'AD'): fail('SNAPSHOT_TARGET_LEVEL_REQUIRED')
        rows['target'] = read(level, draft['target_id'])
        if level == 'AD':
            rows['adset'] = read('ADSET', rows['target']['adset_id'])
            if rows['target']['campaign_id'] != rows['adset']['campaign_id']: fail('SNAPSHOT_PARENT_RELATION_MISMATCH')
        if level in ('ADSET', 'AD'): rows['campaign'] = read('CAMPAIGN', rows['target']['campaign_id'])
    return rows


class SyntheticSnapshotTransport:
    """Explicit fixture transport; never accesses HTTP or an OS credential store."""
    def __init__(self, plan, responses):
        if type(plan) is not SnapshotPlan: fail('SNAPSHOT_PLAN_PROFILE')
        self.plan = plan; self.responses = deepcopy(responses); self.calls = []

    def get(self, request):
        if request != self.plan.request(): fail('SNAPSHOT_REQUEST_CHANGED')
        self.calls.append(deepcopy(request))
        if not self.responses: fail('SNAPSHOT_FIXTURE_EXHAUSTED')
        return self.responses.pop(0)


@dataclass(frozen=True)
class CapturedSnapshots:
    payload: str
    _key: object


def validate_snapshot_draft(draft, *, synthetic_creative=False):
    value = validate_draft(draft)
    allowed = (*SNAPSHOT_OPERATIONS, 'CREATE_CREATIVE') if synthetic_creative else SNAPSHOT_OPERATIONS
    if value['operation'] not in allowed:
        fail('SNAPSHOT_OPERATION_UNSUPPORTED')
    return value


def collect_snapshots(draft, allowlist, transport_factory, *, clock=utcnow, monotonic=time.monotonic,
                      before_request=None, synthetic_creative=False):
    """At most ten GETs, two identical field sweeps, no retry/partial result.

    The caller supplies separately authorized per-plan GraphReadTransport objects.
    This function cannot derive a grant from the allowlist or the draft.
    """
    from .http_read import GraphReadTransport
    draft = validate_snapshot_draft(strict_json(canonical(draft)), synthetic_creative=synthetic_creative)
    supported_transports = ((SyntheticSnapshotTransport,) if draft['operation'] == 'CREATE_CREATIVE'
                            else (SyntheticSnapshotTransport, GraphReadTransport))
    allowed = strict_json(canonical(allowlist)); validate_allowlist(allowed)
    start = clock(); aware_time(start.isoformat()); tick = monotonic()
    previous_stamp = start; previous_tick = tick
    entries = {}; transports = {}; bindings = {}; modes = set(); identities = set(); calls = 0

    def guard():
        nonlocal previous_stamp, previous_tick
        now = clock(); aware_time(now.isoformat()); current_tick = monotonic()
        if (not previous_stamp <= now <= start + timedelta(seconds=60)
                or not previous_tick <= current_tick <= tick + 60):
            fail('SNAPSHOT_CAPTURE_DEADLINE')
        previous_stamp = now; previous_tick = current_tick
        return now

    def request(plan, transport):
        nonlocal calls
        guard()
        if type(transport) is GraphReadTransport and digest(asdict(transport.binding)) != digest(bindings[id(transport)]):
            fail('SNAPSHOT_READ_BINDING_CHANGED')
        if calls >= 10: fail('SNAPSHOT_REQUEST_BUDGET')
        calls += 1
        if before_request is not None:
            before_request(plan)
            # Caller audit/approval callbacks can block or update their transport.
            # Recheck frozen identity and the capture window at the GET edge.
            guard()
            if type(transport) is GraphReadTransport and digest(asdict(transport.binding)) != digest(bindings[id(transport)]):
                fail('SNAPSHOT_READ_BINDING_CHANGED')
        status, body = transport.get(plan.request())
        seen = guard()
        if type(transport) is GraphReadTransport:
            if digest(asdict(transport.binding)) != digest(bindings[id(transport)]): fail('SNAPSHOT_READ_BINDING_CHANGED')
            try:
                transport.broker.recheck(transport.binding)
                transport.binding.validate(plan, seen)
            except Exception:
                # A provider may include a credential in its exception text.
                fail('SNAPSHOT_POST_READ_AUTHORIZATION_FAILED')
            if digest(asdict(transport.binding)) != digest(bindings[id(transport)]): fail('SNAPSHOT_READ_BINDING_CHANGED')
        return normalize_snapshot(plan, status, body), seen.isoformat()

    def read(level, obj):
        plan = _plan(level, obj, allowed); key = level + ':' + obj
        if key in entries: return deepcopy(entries[key]['data'])
        if len(entries) >= 5: fail('SNAPSHOT_OBJECT_BUDGET')
        transport = transport_factory(plan)
        if type(transport) not in supported_transports or transport.plan != plan:
            fail('SNAPSHOT_TRANSPORT_BINDING')
        mode = 'SYNTHETIC' if type(transport) is SyntheticSnapshotTransport else 'GRAPH_READ'
        modes.add(mode)
        if len(modes) != 1: fail('SNAPSHOT_MIXED_PROVENANCE')
        if mode == 'GRAPH_READ':
            identities.add((transport.binding.app_id, transport.binding.principal_id))
            if len(identities) != 1: fail('SNAPSHOT_MIXED_PRINCIPAL')
            bindings[id(transport)] = strict_json(canonical(asdict(transport.binding)))
        row, seen = request(plan, transport)
        entries[key] = dict(plan=asdict(plan), request=plan.request(), data=row, data_sha256=digest(row),
                            first_observed_at=seen, last_observed_at=seen, read_binding=bindings.get(id(transport)))
        transports[key] = transport
        return deepcopy(row)

    _walk(draft, read)
    # Re-read actual fields, not updated_time. This detects changes between the
    # two sweeps; it cannot provide a transaction or guarantee no later mutation.
    for key, item in entries.items():
        row, seen = request(SnapshotPlan(**item['plan']), transports[key])
        if row != item['data']: fail('SNAPSHOT_CHANGED_DURING_CAPTURE')
        item['last_observed_at'] = seen
    end = guard()
    bundle = dict(schema='meta-write-snapshots-v1', draft_intent_sha256=_intent_hash(draft),
                  allowlist=allowed, source_mode=next(iter(modes)), started_at=start.isoformat(), ended_at=end.isoformat(),
                  request_count=calls, entries=list(entries.values()),
                  read_identity=dict(zip(('app_id','principal_id'), next(iter(identities)))) if identities else None,
                  coverage='FIXED_FIELDS_TWO_SWEEPS_NOT_ATOMIC_OR_COMPLETE_MATERIAL', write_enabled=False)
    bundle['bundle_sha256'] = digest(bundle)
    return CapturedSnapshots(canonical(bundle), _CAPTURE_KEY)


def _intent_hash(draft):
    # Fields to be read are derived, not caller-selected. Previously declared
    # before/hash metadata is checked at binding; it does not change GET scope.
    return digest({key: draft[key] for key in ('operation', 'api_version', 'account_id', 'target_id', 'target_level', 'parameters')} |
                  {'page_id': draft['context']['page_id'], 'material_sha256': draft['context']['material_sha256']})


def validate_bundle(draft, bundle, *, clock=utcnow):
    synthetic_creative = type(bundle) is dict and bundle.get('source_mode') == 'SYNTHETIC'
    validate_snapshot_draft(draft, synthetic_creative=synthetic_creative)
    keys = ('schema', 'draft_intent_sha256', 'allowlist', 'source_mode', 'read_identity', 'started_at', 'ended_at', 'request_count',
            'entries', 'coverage', 'write_enabled', 'bundle_sha256')
    object_only(bundle, keys, keys)
    if len(canonical(bundle).encode('utf-8')) > 64000: fail('SNAPSHOT_BUNDLE_TOO_LARGE')
    if bundle['schema'] != 'meta-write-snapshots-v1' or bundle['write_enabled'] is not False: fail('SNAPSHOT_BUNDLE_PROFILE')
    if bundle['source_mode'] not in ('SYNTHETIC', 'GRAPH_READ'): fail('SNAPSHOT_SOURCE_MODE')
    if bundle['source_mode'] == 'GRAPH_READ':
        object_only(bundle['read_identity'], ('app_id','principal_id'), ('app_id','principal_id'))
        for value in bundle['read_identity'].values(): _id(value)
    elif bundle['read_identity'] is not None: fail('SNAPSHOT_SYNTHETIC_IDENTITY_MUST_BE_NULL')
    if bundle['coverage'] != 'FIXED_FIELDS_TWO_SWEEPS_NOT_ATOMIC_OR_COMPLETE_MATERIAL': fail('SNAPSHOT_COVERAGE')
    if bundle['draft_intent_sha256'] != _intent_hash(draft): fail('SNAPSHOT_INTENT_CHANGED')
    if bundle['bundle_sha256'] != digest({k: v for k, v in bundle.items() if k != 'bundle_sha256'}): fail('SNAPSHOT_BUNDLE_HASH_MISMATCH')
    allowed = validate_allowlist(bundle['allowlist']); now = clock(); aware_time(now.isoformat())
    start = aware_time(bundle['started_at']); end = aware_time(bundle['ended_at'])
    if not timedelta(0) <= end - start <= timedelta(seconds=60): fail('SNAPSHOT_CAPTURE_WINDOW')
    if not timedelta(0) <= now - start <= timedelta(minutes=5) or end > now: fail('SNAPSHOT_STALE_OR_FUTURE')
    entries = bundle['entries']
    if type(entries) is not list or not 1 <= len(entries) <= 5: fail('SNAPSHOT_OBJECT_BUDGET')
    if type(bundle['request_count']) is not int or bundle['request_count'] != len(entries) * 2: fail('SNAPSHOT_REQUEST_COUNT')
    lookup = {}; used = set()
    for item in entries:
        fields = ('plan', 'request', 'data', 'data_sha256', 'first_observed_at', 'last_observed_at', 'read_binding')
        object_only(item, fields, fields)
        object_only(item['plan'], ('level', 'object_id', 'account_id', 'page_id', 'version'), ('level', 'object_id', 'account_id', 'page_id', 'version'))
        plan = SnapshotPlan(**item['plan']); expected = _plan(plan.level, plan.object_id, allowed)
        if plan != expected or item['request'] != expected.request(): fail('SNAPSHOT_REQUEST_CHANGED')
        if not start <= aware_time(item['first_observed_at']) <= aware_time(item['last_observed_at']) <= end:
            fail('SNAPSHOT_ENTRY_TIME')
        if bundle['source_mode'] == 'GRAPH_READ':
            from .http_read import ReadBinding
            meta = item['read_binding']; binding_keys = tuple(ReadBinding.__dataclass_fields__)
            object_only(meta, binding_keys, binding_keys)
            if {key:meta[key] for key in ('app_id','principal_id')} != bundle['read_identity']: fail('SNAPSHOT_MIXED_PRINCIPAL')
            if type(meta['granted_scopes']) is not list: fail('SNAPSHOT_SCOPE_METADATA')
            binding = ReadBinding(**(meta | {'granted_scopes': tuple(meta['granted_scopes'])}))
            binding.validate(plan, aware_time(item['first_observed_at']))
            binding.validate(plan, aware_time(item['last_observed_at']))
        elif item['read_binding'] is not None: fail('SNAPSHOT_SYNTHETIC_BINDING_MUST_BE_NULL')
        normalized = normalize_snapshot(plan, 200, item['data'])
        if normalized != item['data'] or digest(normalized) != item['data_sha256']: fail('SNAPSHOT_DATA_HASH_MISMATCH')
        key = (plan.level, plan.object_id)
        if key in lookup: fail('SNAPSHOT_DUPLICATE_OBJECT')
        lookup[key] = normalized

    def read(level, obj):
        _plan(level, obj, allowed); key = (level, obj)
        if key not in lookup: fail('SNAPSHOT_REQUIRED_OBJECT_MISSING')
        used.add(key); return deepcopy(lookup[key])
    rows = _walk(draft, read)
    if used != set(lookup): fail('SNAPSHOT_UNRELATED_OBJECT')
    return rows


def review_snapshots(draft, snapshots, *, clock=utcnow):
    captured = type(snapshots) is CapturedSnapshots and snapshots._key is _CAPTURE_KEY
    bundle = strict_json(snapshots.payload) if captured else strict_json(canonical(snapshots))
    value = strict_json(canonical(draft)); rows = validate_bundle(value, bundle, clock=clock)
    ctx = value['context']; account = rows['account']; op = value['operation']

    def bind(container, key, actual):
        if container[key] is not None and container[key] != actual: fail('SNAPSHOT_DECLARATION_CONFLICT')
        container[key] = actual

    bind(ctx, 'currency', account['currency']); bind(ctx, 'timezone', account['timezone_name'])
    bind(ctx, 'observed_at', bundle['started_at'])
    bind(ctx, 'account_snapshot_sha256', digest(account))
    if 'target' in rows:
        bind(ctx, 'target_snapshot_sha256', digest(rows['target']))
        bind(value['before'], 'configured_status', rows['target']['status'])
    if 'parent' in rows: bind(ctx, 'parent_snapshot_sha256', digest(rows['parent']))
    if 'creative' in rows: bind(ctx, 'creative_snapshot_sha256', digest(rows['creative']))
    if op == 'SET_ADSET_DAILY_BUDGET':
        target = rows['target']
        if 'daily_budget' not in target or 'lifetime_budget' not in target: fail('SNAPSHOT_BUDGET_MODE_UNKNOWN')
        if int(target['daily_budget']) <= 0 or int(target['lifetime_budget']) != 0: fail('SNAPSHOT_NOT_EXISTING_DAILY_BUDGET')
        parent = rows['campaign']
        if 'daily_budget' not in parent or 'lifetime_budget' not in parent: fail('SNAPSHOT_PARENT_BUDGET_MODE_UNKNOWN')
        if int(parent['daily_budget']) != 0 or int(parent['lifetime_budget']) != 0: fail('SNAPSHOT_CAMPAIGN_BUDGET_REQUIRES_OTHER_PROFILE')
        bind(value['before'], 'daily_budget_minor', int(target['daily_budget']))
    review = review_draft(value, clock=clock)
    review['blockers'].remove('DECLARED_SNAPSHOTS_NOT_VERIFIED_READS')
    provenance = 'IMPORTED_UNATTESTED' if not captured else 'IN_PROCESS_' + bundle['source_mode']
    review['blockers'].append('SNAPSHOT_' + provenance + '_NOT_WRITE_AUTHORITY')
    review['blockers'].append('SNAPSHOT_FIXED_FIELDS_NOT_ATOMIC_RECHECK_REQUIRED_BEFORE_WRITE')
    if account['account_status'] != 1: review['blockers'].append('READ_ACCOUNT_NOT_ACTIVE')
    if any(row.get('status') not in (None, 'ACTIVE', 'PAUSED') for row in rows.values()): review['blockers'].append('READ_OBJECT_STATUS_NOT_SUPPORTED_FOR_WRITE')
    if op == 'CREATE_ADSET':
        expected = adset_parent_objective(value['parameters'])
        if expected is not None and rows['parent']['objective'] != expected:
            code = ('READ_PARENT_OBJECTIVE_NOT_TRAFFIC_PROFILE' if expected == 'OUTCOME_TRAFFIC'
                    else 'READ_PARENT_OBJECTIVE_NOT_WEBSITE_EVENT_PROFILE')
            review['blockers'].append(code)
    if any(row.get('special_ad_categories') for row in rows.values()): review['blockers'].append('READ_SPECIAL_CATEGORY_REQUIRES_OTHER_PROFILE')
    if op == 'CREATE_AD': review['blockers'].append('CREATIVE_ACTOR_ID_NOT_FULL_COPY_MEDIA_OR_RIGHTS_VERIFICATION')
    review['snapshot_evidence'] = dict(provenance=provenance, source_mode_claim=bundle['source_mode'],
                                     read_identity=deepcopy(bundle['read_identity']),
                                     read_binding_hashes=[digest(item['read_binding']) for item in bundle['entries'] if item['read_binding'] is not None],
                                     bundle_sha256=bundle['bundle_sha256'], rows=rows, request_count=bundle['request_count'],
                                     state_sha256=digest(rows), server_signature_verified=False, original_draft_sha256=digest(draft))
    review['approval_binding']['snapshot_evidence'] = deepcopy(review['snapshot_evidence'])
    review['review_sha256'] = digest(review['approval_binding'])
    review['status'] = 'READ_SNAPSHOT_REVIEW_EXECUTION_BLOCKED' if not review['missing_fields'] else 'DRAFT_INCOMPLETE'
    review['network_used'] = captured and bundle['source_mode'] == 'GRAPH_READ'
    review['credential_accessed'] = review['network_used']
    return review


def load_snapshot_bundle(path):
    path = checked_path(path, link_error='SNAPSHOT_PATH_LINK', volume_error='SNAPSHOT_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 64000: fail('SNAPSHOT_BUNDLE_FILE')
    raw = path.read_bytes()
    if len(raw) > 64000: fail('SNAPSHOT_BUNDLE_TOO_LARGE')
    return strict_json(raw.decode('utf-8'))


def export_snapshot_review(draft, bundle, output=None, *, material_directory=None, clock=utcnow):
    if material_directory is None:
        report = review_snapshots(draft,bundle,clock=clock)
    else:
        from .material_bundle import attach_material_packet, load_material_packet
        packet = load_material_packet(material_directory,draft,clock=clock)
        report = attach_material_packet(review_snapshots(draft,bundle,clock=clock),packet)
    if output is not None:
        from .monitor_tools import new_directory
        root = new_directory(output)
        provenance = report['snapshot_evidence']['provenance']
        imported = provenance == 'IMPORTED_UNATTESTED'
        rows = [_write_new(root, 'REVIEW.json', report), _write_new(root, 'BOUND_DRAFT.json', report['draft'])]
        sections = [('候选请求（未发送）', report['approval_binding']['request']),
                    ('修改前后差异', report['approval_binding']['diff']),
                    ('投放影响', report['approval_binding']['impact']),
                    ('从固定字段快照核对的对象状态', report['snapshot_evidence']),
                    ('仍需解决的阻断', report['blockers'])]
        if 'material_evidence' in report:
            sections += [('本地材料审阅证据',report['material_evidence']),('当前材料待核项',report['current_material_findings'])]
        lines = ['# 读取快照与写入候选审阅', '', '状态：' + report['status'],
                 '来源：' + provenance,
                 ('导入文件只核对内部一致性，不能证明来自Meta。' if imported else
                  '使用本进程采集结果；来源标签描述所用传输，不是服务器签名或身份/业务验收。'),
                 '本次导出没有网络请求，没有执行POST，不授予写权限。',
                 '两次复读不是原子快照，不能保证审阅后状态不变；预算单位和最低预算仍待核验。',
                 '审阅hash：' + report['review_sha256'], '缺项：' + ', '.join(report['missing_fields'])]
        for title, value in sections:
            lines += ['', '## ' + title, '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```']
        raw = ('\n'.join(lines) + '\n').encode('utf-8')
        with (root / 'REVIEW.md').open('xb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        rows.append(dict(path='REVIEW.md', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        _write_new(root, 'RECEIPT.json', dict(status=report['status'], files=rows, dispatchable=False,
                    source_provenance=provenance, requests_in_export=0, credentials_accessed_in_export=False,
                    graph_gets_in_capture=report['snapshot_evidence']['request_count'] if provenance == 'IN_PROCESS_GRAPH_READ' else 0,
                    graph_capture_count_scope='TRANSPORT_OBSERVED_NOT_SERVER_ATTESTATION_IMPORTED_CLAIMS_NOT_COUNTED',
                    imported_evidence_unattested=imported))
    return report


def snapshot_schema():
    def obj(props, required=None):
        return dict(type='object', additionalProperties=False, properties=props, required=list(props) if required is None else required)
    identifier = dict(type='string', pattern='^[1-9][0-9]{0,39}$')
    hashed = dict(type='string', pattern='^[a-f0-9]{64}$')
    stamp = dict(type='string', format='date-time')
    identity = obj(dict(app_id=identifier, principal_id=identifier)); identity['type'] = ['object','null']
    read_binding = obj(dict(plan_hash=hashed, app_id=identifier, principal_id=identifier,
                           observed_at=stamp, expires_at=stamp, review_expires_at=stamp,
                           granted_scopes=dict(type='array', maxItems=64, items=dict(type='string',pattern='^[a-z][a-z0-9_]{0,63}$')),
                           app_binding_verified={'const':True}, asset_ownership_verified={'const':True}, read_authorized={'const':True}))
    read_binding['type'] = ['object','null']
    allowed = obj(dict(account_id=identifier, **{level: dict(type='array', maxItems=25, uniqueItems=True, items=identifier)
                                                 for level in FIELDS if level != 'ACCOUNT'}))
    variants = []
    for level, fields in FIELDS.items():
        data = {key: dict(type='string', pattern='^[A-Z0-9_]{1,80}$') for key in fields}
        for key in ('id', 'account_id', 'campaign_id', 'adset_id', 'actor_id'):
            if key in fields: data[key] = identifier
        if level == 'ACCOUNT':
            data.update(id=dict(type='string', pattern='^act_[1-9][0-9]{0,39}$'),
                        currency=dict(type='string', pattern='^[A-Z]{3}$'),
                        timezone_name=dict(type='string', maxLength=100, pattern='^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$'),
                        account_status=dict(type='integer', minimum=0, maximum=99))
        for key in ('status', 'effective_status'):
            if key in fields: data[key] = dict(enum=sorted(STATES))
        for key in ('daily_budget', 'lifetime_budget'):
            if key in fields: data[key] = dict(type='string', pattern='^(0|[1-9][0-9]{0,17})$')
        if level == 'CAMPAIGN': data['special_ad_categories'] = dict(type='array', maxItems=10, uniqueItems=True, items=dict(type='string', pattern='^[A-Z_]{1,60}$'))
        if level == 'AD': data['creative'] = obj(dict(id=identifier))
        required = [key for key in fields if key not in ('daily_budget','lifetime_budget','billing_event','optimization_goal','bid_strategy','destination_type')]
        plan = obj(dict(level={'const':level}, object_id=identifier, account_id={'type':'null'} if level=='PAGE' else identifier,
                        page_id=identifier if level=='PAGE' else {'type':'null'}, version={'const':'v27.0'}))
        expression = ','.join(fields).replace('creative', 'creative{id}') if level == 'AD' else ','.join(fields)
        request = obj(dict(method={'const':'GET'}, path=dict(type='string', pattern='^/v27[.]0/' + ('act_' if level=='ACCOUNT' else '') + '[1-9][0-9]{0,39}$'), params=obj(dict(fields={'const':expression}))))
        variants.append(obj(dict(plan=plan, request=request, data=obj(data,required), data_sha256=hashed,
                                 first_observed_at=stamp, last_observed_at=stamp, read_binding=read_binding)))
    schema = obj(dict(schema={'const':'meta-write-snapshots-v1'}, draft_intent_sha256=hashed, allowlist=allowed,
                      source_mode={'enum':['SYNTHETIC','GRAPH_READ']}, read_identity=identity, started_at=stamp, ended_at=stamp,
                      request_count=dict(type='integer', minimum=2, maximum=10),
                      entries=dict(type='array', minItems=1, maxItems=5, items={'oneOf':variants}),
                      coverage={'const':'FIXED_FIELDS_TWO_SWEEPS_NOT_ATOMIC_OR_COMPLETE_MATERIAL'},
                      write_enabled={'const':False}, bundle_sha256=hashed))
    schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
                   'title':'Bounded read-before-write snapshot evidence; file imports are unattested',
                   '$comment':'Runtime checks <=64000 UTF-8 bytes, duplicate keys, field hashes, exact plan/path/ID/account/allowlist binding, relationship closure, no extra or duplicate objects, request count twice object count, time ordering and freshness, draft intent binding. Hashes are integrity checks, never source authentication or write authority. No writes are dispatched.'})
    return schema
