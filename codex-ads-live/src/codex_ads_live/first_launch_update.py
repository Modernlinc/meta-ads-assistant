"""Exact, independently approved updates to the recorded launch objects."""

from pathlib import Path
import re
from urllib.parse import urlencode

from .first_launch_authority import read_key
from .first_launch_controls import _amount, read_account
from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
from .first_launch_plan import _read_plan_json
from .first_launch_progress import progress
from .journal import Journal, digest
from .meta_write_mapping import SUPPORTED_CURRENCIES, utcnow
from .validation import InputError, aware_time, fail, object_only

ID = re.compile(r'[1-9][0-9]{0,39}')
ACTIONS = ('PAUSE_AD', 'PAUSE_ADSET', 'PAUSE_CAMPAIGN', 'SET_ADSET_DAILY_BUDGET')
IDENTITY = ('workspace_sha256', 'plan_sha256', 'business_id', 'app_id', 'account_id', 'page_id')


def _expected_objects(launch, action):
    rows = [stage['readback']['object'] for stage in launch['stages']]
    ad = rows[-1]
    affected = [ad['id']]
    if action != 'PAUSE_AD':
        affected = sorted(obj['id'] for obj in rows if obj.get('creative_id') is not None)
    return dict(campaign_id=ad['campaign_id'], adset_id=ad['adset_id'], ad_id=ad['id'], affected_ads=affected)


def _target(scope):
    level = {'PAUSE_AD': 'ad', 'PAUSE_CAMPAIGN': 'campaign', 'PAUSE_ADSET': 'adset',
             'SET_ADSET_DAILY_BUDGET': 'adset'}[scope['action']]
    return level, scope['objects'][level + '_id']


def validate_scope(scope):
    fields = (*IDENTITY, 'schema', 'action', 'objects', 'currency', 'timezone', 'before', 'after', 'observed_at')
    object_only(scope, fields, fields)
    if (scope['schema'] != 'first-launch-update-v1' or scope['action'] not in ACTIONS
            or scope['currency'] not in SUPPORTED_CURRENCIES
            or type(scope['timezone']) is not str or not 1 <= len(scope['timezone']) <= 120):
        fail('FIRST_LAUNCH_UPDATE_SCOPE_INVALID')
    aware_time(scope['observed_at'])
    objects = scope['objects']
    fields = ('campaign_id', 'adset_id', 'ad_id', 'affected_ads')
    object_only(objects, fields, fields)
    ads = objects['affected_ads']
    if (type(ads) is not list or not 1 <= len(ads) <= 20 or
            any(type(value) is not str or not ID.fullmatch(value)
                for value in (*ads, *(objects[key] for key in fields[:-1])))
            or ads != sorted(set(ads)) or objects['ad_id'] not in ads):
        fail('FIRST_LAUNCH_UPDATE_OBJECT_SCOPE_INVALID')
    before, after = scope['before'], scope['after']
    if scope['action'] == 'SET_ADSET_DAILY_BUDGET':
        if any(type(n) is not int or not 0 < n < 10**15 for n in (before, after)) or before == after:
            fail('FIRST_LAUNCH_UPDATE_BUDGET_INVALID')
    elif (before, after) != ('ACTIVE', 'PAUSED'):
        fail('FIRST_LAUNCH_UPDATE_STATUS_INVALID')


def request(scope):
    validate_scope(scope)
    field = 'daily_budget' if scope['action'] == 'SET_ADSET_DAILY_BUDGET' else 'status'
    return '/v27.0/' + _target(scope)[1], urlencode({field: scope['after']})


def _project(scope, level, identifier, body):
    if (type(body) is not dict or body.get('id') != identifier or body.get('account_id') != scope['account_id']
            or body.get('status') not in ('ACTIVE','PAUSED')):
        fail('FIRST_LAUNCH_UPDATE_OBJECT_READBACK_MISMATCH')
    objects = scope['objects']
    if level in ('adset','ad') and body.get('campaign_id') != objects['campaign_id']:
        fail('FIRST_LAUNCH_UPDATE_PARENT_MISMATCH')
    if level == 'ad' and body.get('adset_id') != objects['adset_id']:
        fail('FIRST_LAUNCH_UPDATE_PARENT_MISMATCH')
    effective = body.get('effective_status')
    if effective is not None and (type(effective) is not str or not re.fullmatch('[A-Z_]{2,40}', effective)):
        fail('FIRST_LAUNCH_UPDATE_EFFECTIVE_STATE_INVALID')
    result = dict(id=identifier, configured_status=body['status'], effective_status=effective)
    if scope['action'] == 'SET_ADSET_DAILY_BUDGET':
        result['daily_budget_minor'] = _amount(body.get('daily_budget'))
    return result


def _object(scope, level, identifier, get):
    fields = 'id,account_id,status,effective_status'
    if level in ('ad','adset'):
        fields += ',campaign_id'
    if level == 'ad':
        fields += ',adset_id'
    if level == 'adset' and scope['action'] == 'SET_ADSET_DAILY_BUDGET':
        fields += ',daily_budget'
    return _project(scope, level, identifier, get('/v27.0/' + identifier + '?' + urlencode(dict(fields=fields))))


def observe(scope, get, *, preflight):
    account = read_account(get, scope)
    if account.get('timezone_name') != scope['timezone']:
        fail('FIRST_LAUNCH_UPDATE_ACCOUNT_CONTEXT_CHANGED')
    level, identifier = _target(scope)
    target = _object(scope, level, identifier, get)
    if preflight and level != 'ad':
        query = urlencode(dict(fields='id,account_id,status,campaign_id,adset_id', limit=21))
        envelope = get('/v27.0/' + identifier + '/ads?' + query)
        data = envelope.get('data')
        if type(data) is not list or len(data) > 20 or (envelope.get('paging') or {}).get('next'):
            fail('FIRST_LAUNCH_UPDATE_AFFECTED_ADS_UNVERIFIED')
        objects = scope['objects']
        ids = []
        for row in data:
            if (type(row) is not dict or row.get('account_id') != scope['account_id']
                    or row.get('campaign_id') != objects['campaign_id']
                    or level == 'adset' and row.get('adset_id') != identifier
                    or type(row.get('id')) is not str or not ID.fullmatch(row['id'])):
                fail('FIRST_LAUNCH_UPDATE_AFFECTED_ADS_UNVERIFIED')
            ids.append(row['id'])
        if sorted(ids) != objects['affected_ads']:
            fail('FIRST_LAUNCH_UPDATE_AFFECTED_ADS_UNVERIFIED')
        if scope['action'] == 'SET_ADSET_DAILY_BUDGET':
            campaign = _object(dict(scope, action='PAUSE_CAMPAIGN'), 'campaign', objects['campaign_id'], get)
            if (target['configured_status'] != 'PAUSED' or campaign['configured_status'] != 'PAUSED'
                    or any(row.get('status') != 'PAUSED' for row in data)):
                fail('FIRST_LAUNCH_UPDATE_BUDGET_PAUSED_CHAIN_REQUIRED')
    return target


def current_value(scope, observation):
    return observation['daily_budget_minor'] if scope['action'] == 'SET_ADSET_DAILY_BUDGET' else observation['configured_status']


def _draft(launch, action, budget):
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    context = launch['activation_context']
    if action == 'SET_ADSET_DAILY_BUDGET':
        from decimal import Decimal
        limits = context['declared_budget']
        cap = limits['maximum_test_spend']
        currency = context['currency']
        if (type(budget) is not int or not 0 < budget < 10**15 or cap is None
                or currency != limits['currency']
                or Decimal(budget).scaleb(0 if currency == 'JPY' else -2) > min(
                    Decimal(limit) for limit in (cap, limits['protected_daily_spend']) if limit is not None)):
            fail('FIRST_LAUNCH_UPDATE_BUDGET_ABOVE_OR_WITHOUT_BUSINESS_CAP')
    elif budget is not None:
        fail('FIRST_LAUNCH_UPDATE_BUDGET_NOT_APPLICABLE')
    return dict(scope_from_launch(launch), schema='first-launch-update-v1', action=action,
                currency=context['currency'], timezone=context['timezone'],
                objects=_expected_objects(launch,action), after=budget if budget is not None else 'PAUSED')


def run(workspace, directory, action, budget, packet, mode):
    launch = progress(workspace, directory)
    identity = scope_from_launch(launch)
    if mode in ('plan','observe'):
        if action is None or packet is not None:
            fail('FIRST_LAUNCH_UPDATE_ACTION_REQUIRED')
        scope = _draft(launch, action, budget)
        if mode == 'plan':
            return dict(status='NAMED_UPDATE_NEEDS_BEFORE_OBSERVATION', proposal=scope, network_used=False)
    else:
        if packet is None or action is not None or budget is not None:
            fail('FIRST_LAUNCH_UPDATE_ORIGINAL_PACKET_REQUIRED')
        scope = _read_plan_json(packet)['update_scope']
        validate_scope(scope)
        expected = _draft(launch, scope['action'], scope['after'] if scope['action']=='SET_ADSET_DAILY_BUDGET' else None)
        if any(scope[key] != value for key, value in expected.items()):
            fail('FIRST_LAUNCH_UPDATE_SCOPE_CHANGED')
    host = LocalFirstLaunchHost(identity)
    if mode == 'observe':
        key = read_key(identity, 'maintenance')
        active_read = host._validated_lease('read', key)
        before = observe(scope, lambda route: host._get(route, active_read, assert_current=lambda: host.assert_lease(
            active_read, 'read', key)), preflight=True)
        scope['before'] = current_value(scope, before)
        scope['observed_at'] = utcnow().isoformat()
        path, form = request(scope)
        return dict(status='NAMED_UPDATE_READY_FOR_INDEPENDENT_APPROVAL', update_scope=scope,
                    scope_sha256=digest(scope), request=dict(path=path, form=form),
                    before_observation=before, network_used=True, write_authorized=False)
    key = digest(scope)
    location = Path(workspace) / 'managed-updates' / key
    journal = Journal(location, stage_snapshots=True, existing_only=mode=='recover')

    def validate(row):
        fields = ('scope', 'scope_sha256', 'attempt_id', 'reserved_at')
        object_only(row, fields, fields)
        if key != row['scope_sha256'] or scope != row['scope'] or not re.fullmatch('[a-f0-9]{64}', row['attempt_id']):
            fail('FIRST_LAUNCH_UPDATE_ORIGINAL_ATTEMPT_MISMATCH')

    with journal.locked(existing_only=mode=='recover'):
        row, damaged = journal.load(validate)
        if damaged or journal.unpublished_snapshot_count():
            fail('FIRST_LAUNCH_UPDATE_JOURNAL_REVIEW_REQUIRED')
        if mode == 'dispatch':
            if row is not None:
                fail('FIRST_LAUNCH_UPDATE_RECOVERY_ONLY')
            approval = host.rpc('get', 'update', key)
            row = dict(scope_sha256=key, scope=scope, attempt_id=approval['attempt_id'], reserved_at=utcnow().isoformat())
            journal.save(row, validate)
            try:
                result = host.rpc('execute', 'update', key, row['attempt_id'])
            except (InputError, OSError):
                return dict(status='OUTCOME_UNKNOWN', **row)
            return dict(status='ACKNOWLEDGED' if result.get('acknowledged') is True else 'OUTCOME_UNKNOWN', **row)
        if row is None:
            fail('FIRST_LAUNCH_UPDATE_ORIGINAL_ATTEMPT_REQUIRED')
        result = host.rpc('reconcile', 'update', key, row['attempt_id'])
        return dict(update=result, write_authorized=False, **row)
