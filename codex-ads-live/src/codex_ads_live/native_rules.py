"""Account-scoped disabled rules and explicit rule updates through the local authority."""
from pathlib import Path
from urllib.parse import urlencode

from .first_launch_host import HEX, NATIVE_ID
from .journal import Journal, canonical, digest
from .meta_write_mapping import SUPPORTED_CURRENCIES
from .operator_review import evidence_text, load_input
from .validation import fail, object_only

MUTATIONS = ('create', 'rename', 'reconfigure', 'disable', 'delete')
RULE_FIELDS = 'id,account_id,name,status,evaluation_spec,execution_spec,schedule_spec'
STATUSES = ('DISABLED', 'ENABLED', 'HAS_ISSUES', 'DELETED')
METRICS = ('spent', 'cost_per')
WINDOWS = ('LAST_3_DAYS', 'LAST_7_DAYS')
ACTIONS = ('PAUSE',)


def numeric(value):
    if type(value) is not str or not NATIVE_ID.fullmatch(value):
        fail('NATIVE_RULE_ID_REQUIRED')
    return value


def scoped_text(value):
    from .business_profile import SECRET_PATTERN

    checked = evidence_text(value)
    if len(checked) > 100 or SECRET_PATTERN.search(checked):
        fail('NATIVE_RULE_NAME')
    return checked


def configure(doc):
    keys = ('name', 'entity_type', 'metric', 'value_minor', 'window', 'execution')
    object_only(doc, keys, keys)
    if (doc['entity_type'] not in ('CAMPAIGN', 'ADSET', 'AD') or doc['metric'] not in METRICS
            or doc['window'] not in WINDOWS or doc['execution'] not in ACTIONS):
        fail('NATIVE_RULE_CONFIGURATION_SCOPE')
    if type(doc['value_minor']) is not int or not 1 <= doc['value_minor'] <= 100_000_000:
        fail('NATIVE_RULE_MINOR_UNITS')
    return dict(name=scoped_text(doc['name']), status='DISABLED',
        evaluation_spec=dict(evaluation_type='SCHEDULE', filters=[
            dict(field='entity_type', value=doc['entity_type'], operator='EQUAL'),
            dict(field='time_preset', value=doc['window'], operator='EQUAL'),
            dict(field=doc['metric'], value=doc['value_minor'], operator='GREATER_THAN'),
        ]),
        execution_spec=dict(execution_type=doc['execution'], execution_options=[]),
        schedule_spec=dict(schedule_type='SEMI_HOURLY'))


def mutation_request(scope):
    from .first_launch_authority import IDENTITY

    fields = (*IDENTITY, 'schema', 'action', 'currency', 'rule_id', 'before_sha256', 'config')
    object_only(scope, fields, fields)
    if (scope['schema'] != 'native-rule-scope-v1' or scope['action'] not in MUTATIONS
            or scope['currency'] not in SUPPORTED_CURRENCIES):
        fail('NATIVE_RULE_SCOPE')
    for key in IDENTITY:
        if key.endswith('_sha256'):
            if type(scope[key]) is not str or not HEX.fullmatch(scope[key]):
                fail('NATIVE_RULE_SCOPE')
        else:
            numeric(scope[key])
    verb = scope['action']
    if verb == 'create':
        if scope['rule_id'] is not None or scope['before_sha256'] is not None:
            fail('NATIVE_RULE_CREATE_SCOPE')
        fields = configure(scope['config'])
        path = f"/v27.0/act_{scope['account_id']}/adrules_library"
    else:
        path = '/v27.0/' + numeric(scope['rule_id'])
        if type(scope['before_sha256']) is not str or not HEX.fullmatch(scope['before_sha256']):
            fail('NATIVE_RULE_BEFORE_REQUIRED')
        if verb == 'rename':
            object_only(scope['config'], ('name',), ('name',))
            fields = dict(name=scoped_text(scope['config']['name']))
        elif verb == 'reconfigure':
            fields = configure(scope['config'])
        else:
            if scope['config'] is not None:
                fail('NATIVE_RULE_CONFIGURATION_SCOPE')
            fields = dict(status='DISABLED') if verb == 'disable' else {}
    method = 'DELETE' if verb == 'delete' else 'POST'
    return dict(method=method, path=path, body=urlencode({
        key: value if type(value) is str else canonical(value) for key, value in sorted(fields.items())}))


def rule_observation(body, account_id, rule_id=None):
    found = numeric(body.get('id'))
    if ((rule_id is not None and found != rule_id)
            or str(body.get('account_id')) != account_id or body.get('status') not in STATUSES):
        fail('NATIVE_RULE_READ_WRONG_SCOPE')
    specs = {}
    for key in ('evaluation_spec', 'execution_spec', 'schedule_spec'):
        value = body.get(key)
        if value is not None and (type(value) is not dict or len(canonical(value)) > 12_000):
            fail('NATIVE_RULE_OBSERVATION_FORMAT')
        specs[key] = digest(value) if value is not None else None
    return dict(id=found, account_id=account_id, name=scoped_text(body.get('name')), status=body['status'], **specs)


def get_rule(account, rule, fetch):
    endpoint = '/v27.0/' + numeric(rule) + '?' + urlencode(dict(fields=RULE_FIELDS))
    return rule_observation(fetch(endpoint), account, rule)


def _read(account, rule, action, fetch):
    if action == 'get':
        row = get_rule(account, rule, fetch)
        return dict(rule=row, observation_sha256=digest(row), coverage='COMPLETE')
    if action == 'list':
        if rule is not None:
            fail('NATIVE_RULE_SCOPE')
        path = f'/v27.0/act_{account}/adrules_library'
        fields = RULE_FIELDS
    else:
        get_rule(account, rule, fetch)
        path = f'/v27.0/{rule}/history'
        fields = 'timestamp,results'
    body = fetch(path + '?' + urlencode(dict(fields=fields, limit=30)))
    rows = body.get('data')
    if type(rows) is not list or len(rows) > 30 or any(type(row) is not dict for row in rows):
        fail('NATIVE_RULE_COLLECTION_FORMAT')
    if action == 'list':
        selected = [rule_observation(row, account) for row in rows]
    else:
        from .validation import aware_time

        selected = []
        for row in rows:
            stamp = row.get('timestamp')
            if type(stamp) is int and stamp >= 0:
                when = stamp
            else:
                when = aware_time(stamp).isoformat()
            selected.append(dict(timestamp=when, result_sha256=digest(row.get('results'))))
    paging = body.get('paging') or {}
    if type(paging) is not dict:
        fail('NATIVE_RULE_COLLECTION_FORMAT')
    return dict(rows=selected, coverage='PARTIAL' if paging.get('next') else 'COMPLETE')


def _dispatch(host, key, state_directory, resume, *, kind="rule"):
    journal = Journal(state_directory, stage_snapshots=True, existing_only=resume)

    def validate(row):
        fields = ('key', 'attempt', 'receipt')
        object_only(row, fields, fields)
        if row['key'] != key or type(row['attempt']) is not str or not HEX.fullmatch(row['attempt']):
            fail('NATIVE_RULE_STATE_SCOPE')
        if row['receipt'] is not None and type(row['receipt']) is not dict:
            fail('NATIVE_RULE_STATE_SCOPE')

    with journal.locked(existing_only=resume):
        saved, damaged = journal.load(validate)
        if damaged or journal.unpublished_snapshot_count():
            fail('NATIVE_RULE_STATE_REVIEW_REQUIRED')
        if saved and saved['receipt'] is not None:
            return dict(saved['receipt'], local_receipt_reused=True)
        if saved:
            result = host.rpc('receipt', kind, key, saved['attempt'])
        else:
            if resume:
                fail('NATIVE_RULE_STATE_REVIEW_REQUIRED')
            grant = host.rpc('get', kind, key)
            saved = dict(key=key, attempt=grant['attempt_id'], receipt=None)
            journal.save(saved, validate)
            result = host.rpc('execute', kind, key, saved['attempt'])
        if result.get('status') != 'OUTCOME_UNKNOWN':
            journal.save(dict(saved, receipt=result), validate)
        return result


def run(args):
    from .first_launch_authority import read_key
    from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
    from .first_launch_progress import progress

    launch = progress(args.workspace, args.plan_dir)
    identity = scope_from_launch(launch)
    action = args.action
    key = None
    scope = None
    if action in MUTATIONS:
        if args.input is None or args.rule_id is not None:
            fail('NATIVE_RULE_INPUT_REQUIRED')
        doc = load_input(args.input)
        fields = ('currency', 'rule_id', 'before_sha256', 'config')
        object_only(doc, fields, fields)
        scope = dict(identity, **doc, schema='native-rule-scope-v1', action=action)
        reviewed = mutation_request(scope)
        key = digest(scope)
        if args.confirmed_mode == 'preview':
            return dict(status='NATIVE_RULE_APPROVAL_PREVIEW', native_rule_scope=scope, request=reviewed,
                        scope_sha256=key, platform='META_NATIVE', dispatchable=False, network_used=False)
        currency = launch.get('activation_context', {}).get('currency')
        if currency is not None and currency != scope['currency']:
            fail('NATIVE_RULE_CURRENCY_MISMATCH')
    elif args.input is not None or args.state_dir is not None or (action == 'list' and args.rule_id is not None):
        fail('NATIVE_RULE_SCOPE')
    elif action != 'list':
        numeric(args.rule_id)
    if action not in MUTATIONS and args.confirmed_mode != 'read':
        if args.confirmed_mode == 'preview':
            return dict(status='NATIVE_RULE_READ_PREVIEW', action=action, rule_id=args.rule_id,
                        identity=identity, required_approval='INDEPENDENT_RULE_READ', network_used=False)
        fail('NATIVE_RULE_SCOPE')
    host = LocalFirstLaunchHost(identity)
    if key is not None:
        if args.state_dir is None or args.confirmed_mode not in ('write', 'recover'):
            fail('NATIVE_RULE_STATE_DIRECTORY_REQUIRED')
        return _dispatch(host, key, args.state_dir, args.confirmed_mode == 'recover')
    scope = read_key(identity, 'rules')
    lease = host._validated_lease('read', scope)
    result = _read(identity['account_id'], args.rule_id, action, lambda path:
                   host._get(path, lease, assert_current=lambda: host.assert_lease(lease, 'read', scope)))
    return dict(result, status='NATIVE_RULES_OBSERVED', platform='META_NATIVE', network_used=True)


def add_command(sub):
    command = sub.add_parser('native-rules', help='在具名 Meta 账户查看规则，准备独立批准的规则变更')
    command.add_argument('action', choices=('list', 'get', 'history', *MUTATIONS))
    command.add_argument('--workspace', type=Path, required=True)
    command.add_argument('--plan-dir', type=Path)
    command.add_argument('--input', type=Path)
    command.add_argument('--rule-id')
    command.add_argument('--state-dir', type=Path)
    command.add_argument('--confirmed-mode', choices=('preview', 'read', 'write', 'recover'), default='preview')
