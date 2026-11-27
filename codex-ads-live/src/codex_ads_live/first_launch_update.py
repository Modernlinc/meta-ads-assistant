"""Exact, independently approved updates to the recorded launch objects."""

from datetime import timezone
from pathlib import Path
import re
from urllib.parse import urlencode

from .first_launch_authority import read_key
from .first_launch_controls import _amount, read_account
from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
from .first_launch_plan import _read_plan_json
from .first_launch_progress import progress
from .journal import Journal, canonical, digest
from .meta_write_mapping import SUPPORTED_CURRENCIES, utcnow, validate_targeting
from .validation import InputError, aware_time, fail, object_only

ID = re.compile(r'[1-9][0-9]{0,39}')
CHANGE_FIELDS = {'SET_ADSET_BID_AMOUNT': 'bid_amount', 'SET_ADSET_TARGETING': 'targeting',
                 'SET_ADSET_END_TIME': 'end_time', 'SET_AD_CREATIVE': 'creative'}
ACTIONS = ('PAUSE_AD', 'PAUSE_ADSET', 'PAUSE_CAMPAIGN', 'SET_ADSET_DAILY_BUDGET', *CHANGE_FIELDS)
BID_STRATEGIES = ('LOWEST_COST_WITH_BID_CAP', 'COST_CAP')
IDENTITY = ('workspace_sha256', 'plan_sha256', 'business_id', 'app_id', 'account_id', 'page_id')


def _expected_objects(launch, action):
    rows = [stage['readback']['object'] for stage in launch['stages']]
    ad = rows[-1]
    affected = [ad['id']]
    if action not in ('PAUSE_AD', 'SET_AD_CREATIVE'):
        batch = launch.get('batch_items')
        if batch is None:
            affected = sorted(obj['id'] for obj in rows if obj.get('creative_id') is not None)
        else:
            affected = sorted(item['object_id'] for item in batch
                              if item['operation'] == 'CREATE_AD' and item['state'] == 'READBACK_MATCHED')
    return dict(campaign_id=ad['campaign_id'], adset_id=ad['adset_id'], ad_id=ad['id'], affected_ads=affected)


def _target(scope):
    action = scope['action']
    level = 'ad' if action in ('PAUSE_AD', 'SET_AD_CREATIVE') else 'campaign' if action == 'PAUSE_CAMPAIGN' else 'adset'
    return level, scope['objects'][level + '_id']


def validate_scope(scope):
    fields = (*IDENTITY, 'schema', 'action', 'objects', 'currency', 'timezone', 'before', 'after', 'observed_at')
    object_only(scope, (*fields,'existing_ad'), fields)
    if (scope['schema'] != 'first-launch-update-v1' or scope['action'] not in ACTIONS
            or scope['currency'] not in SUPPORTED_CURRENCIES
            or type(scope['timezone']) is not str or not 1 <= len(scope['timezone']) <= 120):
        fail('FIRST_LAUNCH_UPDATE_SCOPE_INVALID')
    observed = aware_time(scope['observed_at'])
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
    elif scope['action'] in CHANGE_FIELDS:
        for value in (before, after):
            _change_value(scope['action'], value)
        if before == after or (scope['action'] == 'SET_ADSET_BID_AMOUNT' and before['strategy'] != after['strategy']):
            fail('FIRST_LAUNCH_UPDATE_NO_CHANGE_OR_STRATEGY_CHANGED')
        if scope['action'] == 'SET_ADSET_END_TIME' and not observed < aware_time(after) < aware_time(before):
            fail('FIRST_LAUNCH_UPDATE_FUTURE_SHORTENING_REQUIRED')
    elif (before, after) != ('ACTIVE', 'PAUSED'):
        fail('FIRST_LAUNCH_UPDATE_STATUS_INVALID')
    if 'existing_ad' in scope:
        prior = scope['existing_ad']
        keys = ('snapshot_sha256','creative_id','affected_snapshot_sha256')
        object_only(prior,keys,keys)
        anchor = prior['affected_snapshot_sha256']
        if (type(prior['creative_id']) is not str or not ID.fullmatch(prior['creative_id'])
                or type(prior['snapshot_sha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',prior['snapshot_sha256'])
                or (_target(scope)[0]=='ad' and anchor is not None)
                or (_target(scope)[0]!='ad' and (type(anchor) is not str or not re.fullmatch(r'[a-f0-9]{64}',anchor)))
                or scope['action']=='SET_AD_CREATIVE'):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_ORIGIN')


def _change_value(action, value):
    if action == 'SET_ADSET_TARGETING':
        validate_targeting(value)
    elif action == 'SET_ADSET_BID_AMOUNT':
        keys = ('strategy', 'amount_minor')
        object_only(value, keys, keys)
        if (value['strategy'] not in BID_STRATEGIES or type(value['amount_minor']) is not int
                or not 0 < value['amount_minor'] < 10**12):
            fail('FIRST_LAUNCH_UPDATE_BID_PROFILE_REQUIRED')
    elif action == 'SET_ADSET_END_TIME':
        if aware_time(value).astimezone(timezone.utc).isoformat() != value:
            fail('FIRST_LAUNCH_UPDATE_END_TIME_UTC_REQUIRED')
    elif type(value) is not str or not ID.fullmatch(value):
        fail('FIRST_LAUNCH_UPDATE_CREATIVE_ID_REQUIRED')


def request(scope):
    validate_scope(scope)
    action, after = scope['action'], scope['after']
    field = CHANGE_FIELDS.get(action, 'daily_budget' if action == 'SET_ADSET_DAILY_BUDGET' else 'status')
    if action == 'SET_AD_CREATIVE':
        after = canonical({'creative_id': after})
    elif action == 'SET_ADSET_TARGETING':
        after = canonical(after)
    elif action == 'SET_ADSET_BID_AMOUNT':
        after = after['amount_minor']
    return '/v27.0/' + _target(scope)[1], urlencode({field: after})


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
    if level == _target(scope)[0] and scope['action'] in CHANGE_FIELDS:
        action = scope['action']
        if action == 'SET_ADSET_BID_AMOUNT':
            result['configured_value'] = {'strategy': body.get('bid_strategy'), 'amount_minor': _amount(body.get('bid_amount'))}
        elif action == 'SET_ADSET_TARGETING':
            result['configured_value'] = body.get('targeting')
        elif action == 'SET_ADSET_END_TIME':
            result['configured_value'] = aware_time(body.get('end_time')).astimezone(timezone.utc).isoformat()
            result['current_start_time'] = aware_time(body.get('start_time')).astimezone(timezone.utc).isoformat()
        else:
            creative = body.get('creative')
            result['configured_value'] = creative.get('id') if isinstance(creative, dict) else None
        _change_value(action, result['configured_value'])
    return result


def _object(scope, level, identifier, get):
    fields = 'id,account_id,status,effective_status'
    if level in ('ad','adset'):
        fields += ',campaign_id'
    if level == 'ad':
        fields += ',adset_id'
    if level == 'adset' and scope['action'] == 'SET_ADSET_DAILY_BUDGET':
        fields += ',daily_budget'
    if level == _target(scope)[0] and scope['action'] in CHANGE_FIELDS:
        fields += ',' + CHANGE_FIELDS[scope['action']]
        if scope['action'] == 'SET_ADSET_BID_AMOUNT':
            fields += ',bid_strategy'
        if scope['action'] == 'SET_ADSET_END_TIME':
            fields += ',start_time'
    return _project(scope, level, identifier, get('/v27.0/' + identifier + '?' + urlencode(dict(fields=fields))))


def observe(scope, get, *, preflight):
    account = read_account(get, scope)
    if account.get('timezone_name') != scope['timezone']:
        fail('FIRST_LAUNCH_UPDATE_ACCOUNT_CONTEXT_CHANGED')
    level, identifier = _target(scope)
    target = _object(scope, level, identifier, get)
    if preflight and 'existing_ad' in scope:
        ad = scope['objects']['ad_id']
        path = '/v27.0/' + ad + '?' + urlencode(
            {'fields':'id,account_id,campaign_id,adset_id,status,creative{id,account_id,object_story_spec}'})
        row = get(path)
        _project(scope,'ad',ad,row)
        creative = row.get('creative')
        if (type(creative) is not dict or creative.get('id')!=scope['existing_ad']['creative_id']
                or creative.get('account_id') not in (None,scope['account_id'])
                or type(creative.get('object_story_spec')) is not dict
                or creative['object_story_spec'].get('page_id')!=scope['page_id']):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_PAGE_OR_CREATIVE_CHANGED')
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
        if scope['action'] == 'SET_ADSET_DAILY_BUDGET' or scope['action'] in CHANGE_FIELDS:
            campaign = _object(dict(scope, action='PAUSE_CAMPAIGN'), 'campaign', objects['campaign_id'], get)
            if (target['configured_status'] != 'PAUSED' or campaign['configured_status'] != 'PAUSED'
                    or any(row.get('status') != 'PAUSED' for row in data)):
                fail('FIRST_LAUNCH_UPDATE_BUDGET_PAUSED_CHAIN_REQUIRED')
        if scope['action'] == 'SET_ADSET_BID_AMOUNT':
            query = urlencode({'fields': 'id,account_id,buying_type,daily_budget,lifetime_budget,special_ad_categories'})
            parent = get('/v27.0/' + objects['campaign_id'] + '?' + query)
            if (parent.get('id'), parent.get('account_id'), parent.get('buying_type')) != (
                    objects['campaign_id'], scope['account_id'], 'AUCTION') or any(
                    parent.get(name) not in ('0', 0) for name in ('daily_budget', 'lifetime_budget')):
                fail('FIRST_LAUNCH_UPDATE_BID_ADSET_BUDGET_REQUIRED')
        if scope['action'] == 'SET_ADSET_TARGETING':
            query = urlencode({'fields': 'id,account_id,special_ad_categories'})
            parent = get('/v27.0/' + objects['campaign_id'] + '?' + query)
            if (parent.get('id'), parent.get('account_id'), parent.get('special_ad_categories')) != (
                    objects['campaign_id'], scope['account_id'], []):
                fail('FIRST_LAUNCH_UPDATE_TARGETING_RESTRICTED_CAMPAIGN')
        if scope['action'] == 'SET_ADSET_END_TIME':
            after = aware_time(scope['after'])
            if not max(utcnow(), aware_time(target['current_start_time'])) < after < aware_time(target['configured_value']):
                fail('FIRST_LAUNCH_UPDATE_FUTURE_SHORTENING_REQUIRED')
    if preflight and scope['action'] == 'SET_AD_CREATIVE':
        objects = scope['objects']
        parents = (_object(dict(scope, action='PAUSE_CAMPAIGN'), 'campaign', objects['campaign_id'], get),
                   _object(dict(scope, action='PAUSE_ADSET'), 'adset', objects['adset_id'], get))
        if any(item['configured_status'] != 'PAUSED' for item in (target, *parents)):
            fail('FIRST_LAUNCH_UPDATE_CREATIVE_PAUSED_CHAIN_REQUIRED')
        query = urlencode({'fields': 'id,account_id,object_story_spec'})
        creative = get('/v27.0/' + scope['after'] + '?' + query)
        story = creative.get('object_story_spec')
        if (creative.get('id'), creative.get('account_id')) != (scope['after'], scope['account_id']) or (
                not isinstance(story, dict) or story.get('page_id') != scope['page_id']):
            fail('FIRST_LAUNCH_UPDATE_CREATIVE_PAGE_OR_ACCOUNT_MISMATCH')
    return target


def current_value(scope, observation):
    if scope['action'] in CHANGE_FIELDS:
        return observation['configured_value']
    return observation['daily_budget_minor'] if scope['action'] == 'SET_ADSET_DAILY_BUDGET' else observation['configured_status']


def _draft(launch, action, budget, change=None):
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    context = launch['activation_context']
    if action == 'SET_ADSET_DAILY_BUDGET':
        from decimal import Decimal
        limits = context['declared_budget']
        cap = limits.get('maximum_test_spend')
        currency = context['currency']
        if (type(budget) is not int or not 0 < budget < 10**15 or cap is None
                or currency != limits['currency']
                or Decimal(budget).scaleb(0 if currency == 'JPY' else -2) > min(
                    Decimal(limit) for limit in (cap, limits['protected_daily_spend']) if limit is not None)):
            fail('FIRST_LAUNCH_UPDATE_BUDGET_ABOVE_OR_WITHOUT_BUSINESS_CAP')
    elif budget is not None:
        fail('FIRST_LAUNCH_UPDATE_BUDGET_NOT_APPLICABLE')
    if action in CHANGE_FIELDS:
        _change_value(action, change)
        if action == 'SET_AD_CREATIVE':
            ads = [row['object_id'] for row in launch.get('batch_items', [])
                   if row['operation'] == 'CREATE_CREATIVE' and row['state'] == 'READBACK_MATCHED']
            if change not in ads or change == launch['stages'][-1]['readback']['object']['creative_id']:
                fail('FIRST_LAUNCH_UPDATE_CREATIVE_REVIEWED_SIBLING_REQUIRED')
    elif change is not None:
        fail('FIRST_LAUNCH_UPDATE_CHANGE_NOT_APPLICABLE')
    after = change if action in CHANGE_FIELDS else budget if budget is not None else 'PAUSED'
    return dict(scope_from_launch(launch), schema='first-launch-update-v1', action=action,
                currency=context['currency'], timezone=context['timezone'],
                objects=_expected_objects(launch,action), after=after,
                **({'existing_ad':launch['existing_ad']} if 'existing_ad' in launch else {}))


def _existing_launch(workspace, path, ad_id, page_id, affected, action):
    from .client_workspace import asset_key, draft_review_scope, read

    profile = read(workspace, existing_only=True)
    if (profile.get('connection_onboarding') or {}).get('route')=='host-connector':
        fail('FIRST_LAUNCH_HOST_CONNECTOR_DRAFT_ONLY')
    if page_id is None or asset_key('PAGE',page_id) not in profile['assets']:
        fail('FIRST_LAUNCH_EXISTING_UPDATE_NAMED_PAGE_REQUIRED')

    def snapshot(filename):
        envelope = _read_plan_json(filename)
        object_only(envelope,('payload','sha256'),('payload','sha256'))
        data = envelope['payload']
        if (type(data) is not dict or envelope['sha256']!=digest(data) or data.get('schema')!='native-inventory-v1'
                or data.get('coverage')!='COMPLETE' or type(data.get('plan')) is not dict
                or type(data['plan'].get('scope')) is not dict or type(data.get('rows')) is not list
                or data.get('currency') not in SUPPORTED_CURRENCIES or type(data.get('timezone')) is not str):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_COMPLETE_INVENTORY_REQUIRED')
        return data,envelope['sha256']

    snapshot_data, anchor = snapshot(path)
    scope = snapshot_data['plan']['scope']
    account = scope['account_id']
    workspace_scope = draft_review_scope(profile, account)
    identity = workspace_scope['workspace_identity']
    if (scope['workspace_sha256'] != identity or scope['mode']!='get' or scope['kind']!='ad'
            or scope['ids']!=[ad_id] or len(snapshot_data['rows'])!=1):
        fail('FIRST_LAUNCH_EXISTING_UPDATE_ORIGIN')
    ad = snapshot_data['rows'][0]
    if ad.get('id')!=ad_id or ad.get('account_id')!=account or any(
            type(ad.get(key)) is not str or not ID.fullmatch(ad[key]) for key in ('campaign_id','adset_id','creative_id')):
        fail('FIRST_LAUNCH_EXISTING_UPDATE_ORIGIN')
    currency, zone = (snapshot_data[key] for key in ('currency', 'timezone'))
    review = dict(snapshot_sha256=anchor,creative_id=ad['creative_id'],affected_snapshot_sha256=None)
    ads = [ad_id]
    if action=='SET_AD_CREATIVE':
        fail('FIRST_LAUNCH_EXISTING_CREATIVE_REPLACEMENT_REQUIRES_SEPARATE_REVIEW')
    if action=='PAUSE_AD' and affected is not None:
        fail('FIRST_LAUNCH_EXISTING_UPDATE_SELECTION_REQUIRED')
    if action!='PAUSE_AD':
        if affected is None:
            fail('FIRST_LAUNCH_EXISTING_UPDATE_AFFECTED_SNAPSHOT_REQUIRED')
        rows, affected_sha = snapshot(affected)
        related = rows['plan']['scope']
        parent = 'campaign' if action=='PAUSE_CAMPAIGN' else 'adset'
        if (related['workspace_sha256'],related['account_id'],related['mode'],related['kind'],related['parent_kind'],
                related['parent_id'],rows['currency'],rows['timezone']) != (
                identity,account,'list','ad',parent,ad[parent+'_id'],currency,zone):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_ORIGIN')
        if any(type(row) is not dict or type(row.get('id')) is not str or not ID.fullmatch(row['id'])
               or row.get('account_id')!=account or row.get(parent+'_id')!=ad[parent+'_id'] for row in rows['rows']):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_ORIGIN')
        ads = sorted({row['id'] for row in rows['rows']})
        if not 1 <= len(ads) <= 20 or ad_id not in ads:
            fail('FIRST_LAUNCH_UPDATE_AFFECTED_ADS_UNVERIFIED')
        review['affected_snapshot_sha256'] = affected_sha
    context = dict(currency=currency,timezone=zone,declared_budget=workspace_scope['business_profile'] or {})
    batch = [dict(object_id=identifier,operation='CREATE_AD',state='READBACK_MATCHED') for identifier in ads]
    return dict(status='FIRST_LAUNCH_OBJECT_CHAIN_RECORDED', workspace_identity_sha256=identity,
                plan_sha256=digest(review),business_id=profile['business_id'],app_id=profile['app_id'],
                account_id=account,page_id=page_id,activation_context=context,batch_items=batch,existing_ad=review,
                stages=[dict(readback=dict(object=ad))])


def run(workspace, directory, action, budget, packet, mode, *, ad_id=None, change=None,
        existing_snapshot=None, existing_page=None, affected_snapshot=None):
    if existing_snapshot is not None:
        if directory is not None or ad_id is None or (action is None and packet is None):
            fail('FIRST_LAUNCH_EXISTING_UPDATE_SELECTION_REQUIRED')
        existing_action = action if action is not None else _read_plan_json(packet)['update_scope']['action']
        launch = _existing_launch(workspace, existing_snapshot, ad_id, existing_page, affected_snapshot, existing_action)
    else:
        if existing_page is not None or affected_snapshot is not None:
            fail('FIRST_LAUNCH_EXISTING_UPDATE_SELECTION_REQUIRED')
        launch = progress(workspace, directory, ad_id=ad_id)
    identity = scope_from_launch(launch)
    if mode in ('plan','observe'):
        if action is None or packet is not None:
            fail('FIRST_LAUNCH_UPDATE_ACTION_REQUIRED')
        scope = _draft(launch, action, budget, change)
        if mode == 'plan':
            return dict(status='NAMED_UPDATE_NEEDS_BEFORE_OBSERVATION', proposal=scope, network_used=False,
                        review_requirements=dict(
                            freeze=['account_id', 'objects', 'currency', 'timezone', 'observed_at', 'before', 'after'],
                            time_window='EXPIRES_WITH_INDEPENDENT_MANAGED_APPROVAL',
                            authority='FIRST_LAUNCH_AUTHORITY',
                            recover='READ_ORIGINAL_ATTEMPT_FIRST',
                            unavailable_actions='REVIEW_MANUALLY_WITHOUT_DISPATCH'))
    else:
        if packet is None or action is not None or budget is not None or change is not None:
            fail('FIRST_LAUNCH_UPDATE_ORIGINAL_PACKET_REQUIRED')
        scope = _read_plan_json(packet)['update_scope']
        validate_scope(scope)
        expected = _draft(launch, scope['action'], scope['after'] if scope['action']=='SET_ADSET_DAILY_BUDGET' else None,
                          scope['after'] if scope['action'] in CHANGE_FIELDS else None)
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
