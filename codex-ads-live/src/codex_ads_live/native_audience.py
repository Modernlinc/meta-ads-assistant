"""Existing audience check and one approved lookalike from a named Meta seed."""
from pathlib import Path
import re
from urllib.parse import urlencode

from .first_launch_host import HEX
from .journal import canonical, digest
from .meta_write_mapping import SUPPORTED_CURRENCIES
from .native_rules import _dispatch, numeric, scoped_text
from .operator_review import load_input
from .validation import fail, object_only

FIELDS = 'id,account_id,subtype,operation_status,delivery_status,approximate_count_lower_bound,permission_for_actions'


def create_request(scope):
    from .first_launch_authority import IDENTITY

    keys = (*IDENTITY, 'schema', 'currency', 'seed_id', 'name', 'country', 'percent', 'minimum_observed_seed')
    object_only(scope, keys, keys)
    if scope['schema'] != 'native-audience-scope-v1' or scope['currency'] not in SUPPORTED_CURRENCIES:
        fail('NATIVE_AUDIENCE_SCOPE')
    for key in IDENTITY:
        if key.endswith('_sha256'):
            if type(scope[key]) is not str or not HEX.fullmatch(scope[key]):
                fail('NATIVE_AUDIENCE_SCOPE')
        else:
            numeric(scope[key])
    country, percent, minimum = (scope[key] for key in ('country', 'percent', 'minimum_observed_seed'))
    if (type(country) is not str or not re.fullmatch(r'[A-Z]{2}', country)
            or type(percent) is not int or not 1 <= percent <= 10
            or type(minimum) is not int or not 100 <= minimum <= 10**7):
        fail('NATIVE_AUDIENCE_CREATION_PROFILE')
    seed = numeric(scope['seed_id'])
    data = dict(name=scoped_text(scope['name']), subtype='LOOKALIKE', origin_audience_id=seed,
                lookalike_spec=canonical(dict(type='custom_ratio', country=country, ratio=percent / 100)))
    return dict(method='POST', path=f"/v27.0/act_{scope['account_id']}/customaudiences",
                body=urlencode(data))


def observe(fetch, account, identifier):
    response = fetch('/v27.0/' + numeric(identifier) + '?' + urlencode(dict(fields=FIELDS)))
    if (response.get('id'), response.get('account_id')) != (identifier, account):
        fail('NATIVE_AUDIENCE_SCOPE')
    subtype = response.get('subtype')
    if type(subtype) is not str or not re.fullmatch(r'[A-Z_]{3,45}', subtype):
        fail('NATIVE_AUDIENCE_UNRESOLVED_TYPE')
    def code(key):
        cell = response.get(key)
        return cell.get('code') if type(cell) is dict and type(cell.get('code')) is int else None
    operation, delivery = code('operation_status'), code('delivery_status')
    count = response.get('approximate_count_lower_bound')
    count = count if type(count) is int and 0 <= count <= 10**10 else None
    permissions = response.get('permission_for_actions')
    supports = type(permissions) is dict and permissions.get('subtype_supports_lookalike') is True
    available = operation == 200 and delivery == 200
    return dict(account_id=account, audience_id=identifier, type=subtype,
                operation_status=operation, delivery_status=delivery,
                approximate_count_lower_bound=count, usable=available,
                observed_lookalike_seed_support=supports,
                recovery=('USE_EXISTING_POOL' if available else 'CHECK_ELIGIBILITY_OR_CHOOSE_ANOTHER_POOL'))


def seed_review(scope, fetch):
    row = observe(fetch, scope['account_id'], scope['seed_id'])
    size = row['approximate_count_lower_bound']
    usable = (row['usable'] and row['observed_lookalike_seed_support'] and size is not None
              and size >= scope['minimum_observed_seed'])
    return dict(row, creation_preview='READY' if usable else 'SEED_OR_ELIGIBILITY_INSUFFICIENT',
                required_observed_floor=scope['minimum_observed_seed'],
                recovery='RECHECK_COUNTRY_MATCH_ELIGIBILITY_AND_SEED_SIZE' if not usable else 'SEPARATE_APPROVAL_REQUIRED')


def run(args):
    from .first_launch_authority import read_key
    from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
    from .first_launch_progress import progress

    launch = progress(args.workspace, args.plan_dir)
    identity = scope_from_launch(launch)
    action = args.action
    mode = args.confirmed_mode
    if action != 'lookup':
        if args.input is None or args.id is not None:
            fail('NATIVE_AUDIENCE_INPUT_REQUIRED')
        doc = load_input(args.input)
        keys = ('currency', 'seed_id', 'name', 'country', 'percent', 'minimum_observed_seed')
        object_only(doc, keys, keys)
        scope = dict(identity, **doc, schema='native-audience-scope-v1')
        wire = create_request(scope)
        key = digest(scope)
        if mode == 'preview':
            return dict(status='NATIVE_AUDIENCE_APPROVAL_PREVIEW', native_audience_scope=scope,
                        scope_sha256=key, request=wire, network_used=False)
        if mode in ('write','recover'):
            if action != 'create-lookalike' or args.state_dir is None:
                fail('NATIVE_AUDIENCE_OPERATION_SCOPE')
            host = LocalFirstLaunchHost(identity)
            return _dispatch(host, key, args.state_dir, mode == 'recover', kind='audience')
    else:
        if args.input is not None or args.state_dir is not None:
            fail('NATIVE_AUDIENCE_OPERATION_SCOPE')
        numeric(args.id)
        if mode == 'preview':
            return dict(status='NATIVE_AUDIENCE_LOOKUP_PREVIEW', identity=identity, audience_id=args.id, network_used=False)
    if mode != 'read':
        fail('NATIVE_AUDIENCE_OPERATION_SCOPE')
    host = LocalFirstLaunchHost(identity)
    key = read_key(identity, 'audiences')
    lease = host._validated_lease('read', key)
    def fetch(path):
        return host._get(path, lease, assert_current=lambda: host.assert_lease(lease, 'read', key))
    result = observe(fetch, identity['account_id'], args.id) if action == 'lookup' else seed_review(scope, fetch)
    if action == 'lookup' and result['usable']:
        result['suggested_selection'] = dict(custom_audiences=[result['audience_id']])
    return dict(result, status='NATIVE_AUDIENCE_READ', network_used=True)


def add_command(sub):
    command = sub.add_parser('native-audience', help='核对具名受众，准备基于现有种子的受管 Lookalike')
    command.add_argument('action', choices=('lookup', 'seed-review','create-lookalike'))
    command.add_argument('--workspace', type=Path, required=True)
    command.add_argument('--plan-dir', type=Path)
    command.add_argument('--input', type=Path)
    command.add_argument('--id')
    command.add_argument('--state-dir', type=Path)
    command.add_argument('--confirmed-mode', choices=('preview','read','write','recover'), default='preview')
