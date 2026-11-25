"""Project the named Graph objects into a guard for one Ad state change."""

from datetime import timedelta
from urllib.parse import urlencode

from .first_launch_activation_host import ActivationGuard
from .first_launch_host import NATIVE_ID
from .journal import digest
from .meta_write_mapping import utcnow
from .validation import aware_time, fail


def _amount(value):
    if type(value) is str and value.isascii() and value.isdecimal() and len(value) <= 18:
        value = int(value)
    if type(value) is not int or value <= 0:
        fail('FIRST_LAUNCH_LOCAL_ENFORCEABLE_BUDGET_REQUIRED')
    return value


def read_account(get, scope, *, check_currency=True):
    account_id = scope['account_id']
    fields = 'id,account_id,account_status,currency,timezone_name,user_tasks'
    row = get('/v27.0/act_' + account_id + '?' + urlencode(dict(fields=fields)))
    if ((row.get('id'), row.get('account_id'), row.get('account_status')) != ('act_' + account_id, account_id, 1)
            or type(row.get('account_status')) is not int or (check_currency and row.get('currency') != scope['currency'])):
        fail('FIRST_LAUNCH_LOCAL_ACCOUNT_ELIGIBILITY')
    return row


def observe_guard(review, get, deadline):
    scope = review['binding']
    account = read_account(get, scope)

    def object_row(name, fields):
        identifier = scope[name + '_id']
        if type(identifier) is not str or not NATIVE_ID.fullmatch(identifier):
            fail('FIRST_LAUNCH_LOCAL_CONTROL_SCOPE')
        row = get('/v27.0/' + identifier + '?' + urlencode(dict(fields=fields)))
        if row.get('id') != identifier or row.get('account_id') != scope['account_id']:
            fail('FIRST_LAUNCH_LOCAL_CONTROL_SCOPE')
        return row

    ad = object_row('ad', 'id,account_id,status,campaign_id,adset_id,creative{id}')
    campaign = object_row('campaign', 'id,account_id,status,spend_cap')
    adset = object_row('adset', 'id,account_id,status,campaign_id,daily_budget,start_time,end_time')
    if ((account.get('timezone_name'), ad.get('campaign_id'), ad.get('adset_id'),
         (ad.get('creative') or {}).get('id'), adset.get('campaign_id')) !=
            (scope['timezone'], scope['campaign_id'], scope['adset_id'], scope['creative_id'], scope['campaign_id'])):
        fail('FIRST_LAUNCH_LOCAL_CONTROL_RELATION_MISMATCH')
    start = aware_time(adset.get('start_time')).isoformat()
    stop = aware_time(adset.get('end_time')).isoformat()
    cap = _amount(campaign.get('spend_cap'))
    now = utcnow()
    return ActivationGuard(
        review['review_sha256'], scope['preview_observation_sha256'], scope['account_id'],
        scope['ad_id'], scope['campaign_id'], scope['adset_id'], scope['creative_id'], ad.get('status'),
        campaign.get('status'), adset.get('status'), (scope['ad_id'],), scope['currency'], scope['timezone'],
        _amount(adset.get('daily_budget')), cap, start, stop,
        digest(dict(campaign_id=scope['campaign_id'], adset_id=scope['adset_id'], cap=cap, start=start, stop=stop)),
        now.isoformat(), min(now + timedelta(seconds=90), deadline).isoformat())
