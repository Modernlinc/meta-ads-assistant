"""Bounded inventory and activities for one previously connected Meta account."""
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import urlencode

from . import client_workspace as workspace
from .business_profile import SECRET_PATTERN
from .graph_network import NETWORK_FAILURE
from .journal import digest
from .local_paths import checked_path
from .meta_write_mapping import _write_new, utcnow
from .monitor_tools import new_directory
from .native_insights import CURSOR, authorized_account_read
from .read_adapter import GRAPH_AUTH_ERRORS, error_category
from .validation import InputError, aware_time, fail, strict_json

NODE_FIELDS = {
    'account': 'id,account_id,name,account_status,currency,timezone_name',
    'campaign': 'id,account_id,name,status,effective_status,objective,daily_budget,lifetime_budget,spend_cap',
    'adset': 'id,account_id,name,status,effective_status,campaign_id,optimization_goal,promoted_object,daily_budget,lifetime_budget,start_time,end_time',
    'ad': 'id,account_id,name,status,effective_status,campaign_id,adset_id,creative{id}',
    'creative': 'id,account_id,name,title,body,object_type,object_story_id,effective_object_story_id,video_id',
}
COLLECTIONS = {
    'campaign': ('campaigns', ('account',)),
    'adset': ('adsets', ('account', 'campaign')),
    'ad': ('ads', ('account', 'campaign', 'adset')),
    'creative': ('adcreatives', ('ad',)),
    'activity': ('activities', ('account', 'adset')),
}
RUNTIME_ERRORS = {'RATE_LIMIT', 'PERMISSION', 'OBJECT_OR_PARAMETER', 'SERVICE_UNAVAILABLE', 'UNCLASSIFIED_ERROR',
                  NETWORK_FAILURE, *GRAPH_AUTH_ERRORS, 'CREDENTIAL_OR_TRANSPORT_FAILURE'}


def _metadata(value, allowed):
    if type(value) is str and re.fullmatch(allowed, value):
        return value


def _object(source, account, kind, requested=None):
    if type(source) is not dict or source.get('account_id') != account:
        fail('NATIVE_INVENTORY_OBJECT_SCOPE')
    ident = source.get('id')
    if kind == 'account':
        if ident != 'act_' + account:
            fail('NATIVE_INVENTORY_OBJECT_SCOPE')
    else:
        workspace.numeric(ident)
    if requested is not None and ident != requested:
        fail('NATIVE_INVENTORY_OBJECT_SCOPE')
    result = dict(kind=kind, id=ident, account_id=account)
    for key in ('status', 'effective_status', 'objective', 'optimization_goal', 'object_type', 'currency'):
        value = _metadata(source.get(key), r'[A-Z_]{2,80}')
        if value is not None:
            result[key] = value
    for key in ('campaign_id','adset_id','video_id','object_story_id','effective_object_story_id'):
        value = _metadata(source.get(key), r'[1-9][0-9]{0,39}(?:_[1-9][0-9]{0,39})?')
        if value is not None:
            result[key] = value
    for key in ('daily_budget','lifetime_budget','spend_cap'):
        value = source.get(key)
        if type(value) is int and 0 <= value <= 10**12:
            result[key + '_minor'] = value
        elif _metadata(value, r'[0-9]{1,13}') and int(value) <= 10**12:
            result[key + '_minor'] = int(value)
    for key, maximum in (('name', 250), ('title', 500), ('body', 2000)):
        value = source.get(key)
        if type(value) is str and len(value) <= maximum and not SECRET_PATTERN.search(value):
            result[key] = value
    for key in ('start_time','end_time'):
        if source.get(key):
            result[key] = aware_time(source[key]).isoformat()
    if type(source.get('account_status')) is int:
        result['account_status'] = source['account_status']
    zone = _metadata(source.get('timezone_name'), r'[A-Za-z0-9_+./-]{1,100}')
    if zone:
        result['timezone_name'] = zone
    creative = source.get('creative')
    if type(creative) is dict and creative.get('id') is not None:
        result['creative_id'] = workspace.numeric(creative['id'])
    promoted = source.get('promoted_object')
    if type(promoted) is dict:
        result['promoted_object'] = {key: value for key in ('pixel_id','custom_conversion_id','custom_event_type')
            if (value := _metadata(promoted.get(key), r'[A-Z_]{2,80}' if key == 'custom_event_type' else r'[1-9][0-9]{0,39}'))}
    return result


def _activity(source, earliest, latest):
    if type(source) is not dict:
        fail('NATIVE_INVENTORY_ACTIVITY_SHAPE')
    timestamp = aware_time(source.get('event_time'))
    if not earliest <= timestamp < latest:
        return None
    value = _metadata(source.get('event_type'), r'[a-z_]{2,100}')
    identifier = _metadata(source.get('object_id'), r'(?:act_)?[1-9][0-9]{0,39}')
    if value is None or identifier is None:
        fail('NATIVE_INVENTORY_ACTIVITY_SHAPE')
    return dict(event_time=timestamp.astimezone(timezone.utc).isoformat(), event_type=value, object_id=identifier)


def _read_object(get, kind, node, account):
    return _object(get(f'/v27.0/{node}?' + urlencode(dict(fields=NODE_FIELDS[kind]))), account, kind, node)


def _collect(get, plan, previous):
    scope, params, route = plan['scope'], plan['params'], plan['path']
    kind, account, parent_kind, parent = (scope[key] for key in ('kind','account_id','parent_kind','parent_id'))
    if parent_kind != 'account':
        _read_object(get, parent_kind, parent, account)
    if scope['mode'] == 'get':
        if kind == 'adset' and len(scope['ids']) > 1:
            body = get(route + '?' + urlencode(params))
            if body.keys() - set(scope['ids']):
                fail('NATIVE_INVENTORY_OBJECT_SCOPE')
            items, unavailable = [], []
            for ident in scope['ids']:
                cell = body.get(ident)
                if cell is None:
                    unavailable.append(dict(id=ident,cause='OBJECT_NOT_RETURNED'))
                elif type(cell) is not dict:
                    fail('NATIVE_INVENTORY_RESPONSE_SHAPE')
                elif 'error' in cell:
                    unavailable.append(dict(id=ident,cause=error_category(200,cell)))
                else:
                    items.append(_object(cell,account,kind,ident))
            return dict(coverage='PARTIAL' if unavailable else 'COMPLETE',rows=items,unavailable=unavailable,
                        next_cursor=None,pages_total=0,error=None)
        items, unavailable = [], []
        for node in scope['ids']:
            try:
                items.append(_read_object(get, kind, node, account))
            except InputError as exc:
                if str(exc) not in RUNTIME_ERRORS:
                    raise
                unavailable.append(dict(id=node, cause=str(exc)))
                if str(exc) != 'OBJECT_OR_PARAMETER':
                    unavailable.extend(dict(id=rest, cause='NOT_RUN_AFTER_ERROR')
                                       for rest in scope['ids'][len(items)+len(unavailable):])
                    break
        return dict(coverage='PARTIAL' if unavailable else 'COMPLETE', rows=items, unavailable=unavailable,
                    next_cursor=None, pages_total=0, error=None)
    prior_rows = previous['rows'] if previous else []
    index = {digest(row) if kind == 'activity' else row['id']: row for row in prior_rows}
    pages = previous['pages_total'] if previous else 0
    cursor = previous['next_cursor'] if previous else None
    seen = {cursor} if cursor else set()
    result = dict(coverage='PARTIAL', error=None, unavailable=[])
    for _ in range(scope['page_budget']):
        if pages >= 24 or len(index) >= 1200:
            result['error'] = 'LOCAL_INVENTORY_BUDGET_EXHAUSTED'
            break
        try:
            body = get(route + '?' + urlencode(dict(params, after=cursor) if cursor else params))
        except InputError as exc:
            if str(exc) not in RUNTIME_ERRORS:
                raise
            result['error'] = str(exc)
            break
        data = body.get('data')
        if type(data) is not list or len(data) > params['limit']:
            fail('NATIVE_INVENTORY_RESPONSE_SHAPE')
        for raw in data:
            if kind == 'activity':
                row = _activity(raw, *map(aware_time, (scope['since'], scope['until'])))
                if row is None:
                    continue
                key = digest(row)
            else:
                row = _object(raw, account, kind)
                relation = {'campaign': 'campaign_id', 'adset': 'adset_id'}.get(parent_kind)
                if relation is not None and row.get(relation) != parent:
                    fail('NATIVE_INVENTORY_PARENT_SCOPE')
                key = row['id']
            if key in index and row != index[key]:
                fail('NATIVE_INVENTORY_OBJECT_CHANGED_DURING_PAGING')
            index[key] = row
        pages += 1
        paging = body.get('paging') or {}
        if type(paging) is not dict:
            fail('NATIVE_INVENTORY_RESPONSE_SHAPE')
        if not paging.get('next'):
            cursor = None
            result['coverage'] = 'COMPLETE'
            break
        cursors = paging.get('cursors')
        next_page = cursors.get('after') if type(cursors) is dict else None
        if not isinstance(next_page, str) or not CURSOR.fullmatch(next_page) or next_page in seen:
            cursor = None
            result['error'] = 'MISSING_OR_REPEATED_PAGINATION_CURSOR'
            break
        cursor = next_page
        seen.add(cursor)
    return dict(result, rows=list(index.values()), next_cursor=cursor, pages_total=pages)


def run(args, *, clock=utcnow):
    profile = workspace.read(args.workspace, existing_only=True)
    account = workspace.business_account(profile, args.account_id)
    mode, kind, parent_kind, parent = args.action, args.kind, args.parent_kind, args.parent_id
    ids = args.id or []
    if not 1 <= args.page_budget <= 6:
        fail('NATIVE_INVENTORY_PAGE_BUDGET')
    if mode == 'get':
        if kind == 'account' and not ids:
            ids = ['act_' + account]
        elif kind == 'account' or not ids or len(ids) > 12 or len(ids) != len(set(ids)):
            fail('NATIVE_INVENTORY_OBJECT_SELECTION')
        else:
            ids = [workspace.numeric(node) for node in ids]
        if kind not in NODE_FIELDS or parent is not None or parent_kind != 'account' or args.resume_from:
            fail('NATIVE_INVENTORY_OBJECT_SELECTION')
        edge = None
    else:
        if (kind not in COLLECTIONS or parent_kind not in COLLECTIONS[kind][1] or ids
                or (parent_kind == 'account') != (parent is None)):
            fail('NATIVE_INVENTORY_PARENT_SCOPE')
        edge = COLLECTIONS[kind][0]
        if parent is not None:
            parent = workspace.numeric(parent)
    since, until = args.since, args.until
    if kind == 'activity':
        if not profile['business_id'] or since is None or until is None or not 0 <= (until-since).days < 31:
            fail('NATIVE_INVENTORY_ACTIVITY_DATES')
        try:
            since = datetime.combine(since, time.min, timezone.utc).isoformat()
            until = datetime.combine(until+timedelta(days=1), time.min, timezone.utc).isoformat()
        except OverflowError:
            fail('NATIVE_INVENTORY_ACTIVITY_DATES')
    elif since is not None or until is not None:
        fail('NATIVE_INVENTORY_ACTIVITY_DATES')
    node = parent if parent is not None else 'act_' + account
    scope = dict(account_id=account, mode=mode, kind=kind, ids=ids, parent_kind=parent_kind,
                 parent_id=node, since=since, until=until, page_budget=args.page_budget,
                 workspace_sha256=workspace.workspace_identity(profile))
    params = dict(fields=NODE_FIELDS.get(kind,'object_id,event_type,event_time'), limit=50)
    if kind == 'activity':
        params.update(since=since, until=until, business_id=profile['business_id'])
    plan = dict(scope=scope, path=f'/v27.0/{node}/{edge}' if edge else None, params=params if edge else None)
    if mode == 'get':
        if kind == 'adset' and len(ids) > 1:
            plan.update(path='/v27.0/', params=dict(ids=','.join(ids),fields=NODE_FIELDS[kind]))
        else:
            plan['paths'] = [f'/v27.0/{item}' for item in ids]
            plan['params'] = dict(fields=NODE_FIELDS[kind])
    current = digest(plan)
    previous = None
    if args.resume_from:
        path = checked_path(args.resume_from, link_error='NATIVE_INVENTORY_RESUME_SCOPE',
                            volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
        if not path.is_file():
            fail('NATIVE_INVENTORY_RESUME_SCOPE')
        with path.open('rb') as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            fail('NATIVE_INVENTORY_RESUME_SCOPE')
        packet = strict_json(raw.decode('utf-8'), max_chars=4_000_000)
        if type(packet) is not dict:
            fail('NATIVE_INVENTORY_RESUME_SCOPE')
        previous = packet.get('payload')
        if (type(previous) is not dict or digest(previous) != packet.get('sha256') or previous.get('plan_sha256') != current
                or previous.get('plan') != plan or previous.get('schema') != 'native-inventory-v1'
                or type(previous.get('rows')) is not list or len(previous['rows']) > 1200
                or type(previous.get('pages_total')) is not int or not 0 <= previous['pages_total'] < 24
                or not isinstance(previous.get('next_cursor'), str) or not CURSOR.fullmatch(previous['next_cursor'])
                or previous.get('coverage') != 'PARTIAL'):
            fail('NATIVE_INVENTORY_RESUME_SCOPE')
    if not args.execute_read:
        return dict(status='NATIVE_INVENTORY_PREVIEW', request=plan, plan_sha256=current, network_used=False)
    if args.output is None:
        fail('NATIVE_INVENTORY_OUTPUT_REQUIRED')
    root = checked_path(args.output, link_error='NATIVE_INVENTORY_OUTPUT_REQUIRED',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if root.exists():
        fail('NATIVE_INVENTORY_OUTPUT_REQUIRED')
    target = ','.join(ids) if ids else node
    phrase = f'READ INVENTORY {kind.upper()} {target} ACCOUNT {account}'
    print(f'核对范围 {kind} · {target} · 最多 {args.page_budget} 页。')
    if since:
        phrase += ' ' + since[:10] + ' TO ' + args.until.isoformat()
        print(f'活动记录 UTC 日期 {since[:10]} 至 {args.until.isoformat()}。')
    with authorized_account_read(args.workspace, profile, account, current, phrase, args.record_dir, clock) as session:
        get, _, _, currency, zone = session
        report = _collect(get, plan, previous)
    payload = dict(report, schema='native-inventory-v1', plan=plan, plan_sha256=current, observed_at=clock().isoformat(),
                   currency=currency, timezone=zone)
    artifact = _write_new(new_directory(root),'NATIVE-INVENTORY.json',dict(payload=payload,sha256=digest(payload)))
    return dict(status='NATIVE_INVENTORY_OBSERVED', snapshot=artifact, coverage=report['coverage'],
                rows=report['rows'], unavailable=report['unavailable'], pages_total=report['pages_total'],
                next_page_available=report['next_cursor'] is not None, error=report['error'], network_used=True)


def add_command(sub):
    from datetime import date

    command = sub.add_parser('native-inventory', help='读取同一账户的具名资产与活动')
    command.add_argument('action', choices=('get','list'))
    command.add_argument('kind', choices=(*NODE_FIELDS,'activity'))
    command.add_argument('--workspace', required=True, type=Path)
    command.add_argument('--account-id')
    command.add_argument('--id', action='append')
    command.add_argument('--parent-kind', default='account', choices=('account','campaign','adset','ad'))
    command.add_argument('--parent-id')
    command.add_argument('--since', type=date.fromisoformat)
    command.add_argument('--until', type=date.fromisoformat)
    command.add_argument('--page-budget', type=int, default=3)
    command.add_argument('--output', type=Path)
    command.add_argument('--resume-from', type=Path)
    command.add_argument('--record-dir', type=Path)
    command.add_argument('--execute-read', action='store_true')
