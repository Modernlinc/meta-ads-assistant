"""Owner-declared qualified lead counts for two saved account windows."""
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import re

from .business_profile import ACCOUNT_ID, EVIDENCE_REF
from .journal import digest
from .local_paths import checked_path
from .proposal_estimate import display_ratio, estimate_test
from .timezones import load_zone
from .validation import aware_time, fail, object_only, strict_json

SCHEMA = 'ads-qualified-lead-evidence-v1'
FIELDS = {'schema', 'account_id', 'currency', 'timezone', 'business_profile_sha256', 'periods'}
PERIODS = ('previous', 'current')
WINDOW_FIELDS = {'date_start', 'date_stop', 'qualified_count', 'evidence_ref', 'observed_at'}
COUNT_MAX = 1000000000
SAMPLE_FLOOR_MET = 'SAMPLE_FLOOR_MET_IN_DECLARED_QUALIFIED_DATA'
PATTERNS = dict(account_id=ACCOUNT_ID, currency='[A-Z]{3}', timezone=r'[A-Za-z0-9_+/-]{1,80}',
                business_profile_sha256='[0-9a-f]{64}')


def validate(value):
    object_only(value, FIELDS, FIELDS)
    if value['schema'] != SCHEMA:
        fail('QUALIFIED_LEADS_SCHEMA')
    for key, pattern in PATTERNS.items():
        if type(value[key]) is not str or not re.fullmatch(pattern,value[key]):
            fail('QUALIFIED_LEADS_SCOPE_FORMAT')
    object_only(value['periods'],PERIODS,PERIODS)
    for label in PERIODS:
        row = value['periods'][label]
        object_only(row,WINDOW_FIELDS,WINDOW_FIELDS)
        try:
            start, stop = (date.fromisoformat(row[key]) for key in ('date_start','date_stop'))
        except (ValueError,TypeError):
            fail('QUALIFIED_LEADS_PERIOD_FORMAT')
        if start.isoformat() != row['date_start'] or stop.isoformat() != row['date_stop'] or start > stop:
            fail('QUALIFIED_LEADS_PERIOD_FORMAT')
        count = row['qualified_count']
        if count is not None and (type(count) is not int or not 0 <= count <= COUNT_MAX):
            fail('QUALIFIED_LEADS_COUNT')
        if type(row['evidence_ref']) is not str or not re.fullmatch(EVIDENCE_REF,row['evidence_ref']):
            fail('QUALIFIED_LEADS_REFERENCE')
        aware_time(row['observed_at'])
    return value


def load_evidence(path):
    file = checked_path(path,link_error='QUALIFIED_LEADS_PATH_LINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if not file.is_file():
        fail('QUALIFIED_LEADS_FILE')
    with file.open('rb') as handle:
        raw = handle.read(16001)
    if len(raw) > 16000:
        fail('QUALIFIED_LEADS_FILE')
    return validate(strict_json(raw.decode('utf-8')))


def _cost(spend, count):
    if count is None:
        return dict(state='QUALIFIED_COUNT_UNAVAILABLE',value=None)
    if count == 0:
        return dict(state='ZERO_QUALIFIED_COUNT',value=None)
    if spend is None:
        return dict(state='SPEND_UNAVAILABLE',value=None)
    return dict(state='OBSERVED_SPEND_PER_DECLARED_OUTCOME',value=display_ratio(Fraction(Decimal(spend))/count))


def review(evidence, profile, report):
    result = dict(qualified_lead_evidence_sha256=digest(evidence),
                  business_result_definition_verified=False, sample_metric='qualified_leads')
    if profile is None or profile['result_type'] != 'QUALIFIED_LEAD':
        return dict(result,state='QUALIFIED_LEAD_PROFILE_REQUIRED')
    if evidence['business_profile_sha256'] != digest(profile):
        return dict(result,state='BUSINESS_DEFINITION_CHANGED')
    periods = report['periods']
    context = periods['current']['context']
    if (any(evidence[key] != context[key] for key in ('account_id','currency','timezone'))
            or profile['currency'] != evidence['currency']):
        return dict(result,state='QUALIFIED_LEADS_SCOPE_MISMATCH')
    if any(evidence['periods'][label][key] != periods[label]['context'][key]
           for label in PERIODS for key in ('date_start','date_stop')):
        return dict(result,state='QUALIFIED_LEADS_DATES_MISMATCH')
    zone = load_zone(context['timezone'])
    now = datetime.now(timezone.utc)
    for row in evidence['periods'].values():
        observed = aware_time(row['observed_at'])
        if observed > now or observed.astimezone(zone).date() <= date.fromisoformat(row['date_stop']):
            return dict(result,state='QUALIFIED_LEADS_OBSERVATION_TIME_UNVERIFIED')
    rows = evidence['periods']
    counts = {label: rows[label]['qualified_count'] for label in PERIODS}
    spend = report['account_metrics']['spend']
    platform_leads = report['account_metrics'].get('website_leads',{})
    platform_cpl = report['account_metrics'].get('website_lead_cpl',{})
    results = {
        label: dict(rows[label], spend=spend[label], website_leads=platform_leads.get(label),
                    website_lead_cpl=platform_cpl.get(label),
                    qualified_lead_cost=_cost(spend[label],counts[label])) for label in PERIODS
    }
    window, minimum = profile['observation_days'], profile['minimum_results']
    if None in counts.values():
        state = 'QUALIFIED_LEAD_COUNT_INCOMPLETE'
    elif window is None or minimum is None:
        state = 'DECLARED_SAMPLE_RULE_INCOMPLETE'
    elif report['days_per_period'] < window:
        state = 'BELOW_DECLARED_OBSERVATION_DAYS'
    elif min(counts.values()) < minimum:
        state = 'BELOW_DECLARED_MINIMUM_RESULTS'
    else:
        state = SAMPLE_FLOOR_MET
    return dict(result,state=state,profile_sha256=digest(profile),declared_result_type=profile['result_type'],
                evidence_basis='OWNER_DECLARED_ACCOUNT_PERIOD_AGGREGATES',currency=context['currency'],
                declared_observation_days=window,declared_minimum_results=minimum,
                sample_counts=counts,qualified_lead_periods=results)


def proposal_basis(review, profile, periods):
    """Translate checked outcome observations into one non-executable planning step."""
    windows = review.get('qualified_lead_periods')
    result = dict(
        decision='DEFER_CHANGE', sample_state=review['state'],
        qualified_lead_evidence_sha256=review['qualified_lead_evidence_sha256'],
        profile_sha256=review.get('profile_sha256'),
        currency=review.get('currency'), target_cost_per_result=None, target_check='NOT_EVALUATED',
        maximum_test_spend=None, protected_daily_spend=None, capacity_check='NOT_EVALUATED',
        minimum_results=review.get('declared_minimum_results'),
        current_cost_inputs=None, estimated_test_capacity=None, observations={},
        causal_claim_allowed=False, remote_write_authorized=False, dispatchable=False,
        next_check='核对两期账户范围、采集覆盖及有效询盘汇总。',
    )
    if windows is None:
        return result
    context = periods['current']['context']
    target = profile['target_cost_per_result']
    cap = profile['maximum_test_spend']
    result.update(account_id=context['account_id'],timezone=context['timezone'],
                  target_cost_per_result=target,maximum_test_spend=cap,protected_daily_spend=profile['protected_daily_spend'],
                  observations={label:dict(date_start=window['date_start'],date_stop=window['date_stop'],
                                           evidence_ref=window['evidence_ref'],
                                           insights_rows_sha256=periods[label]['evidence']['insights']['observation_sha256'])
                                for label,window in windows.items()})
    if review['state'] != SAMPLE_FLOOR_MET:
        return dict(result,next_check='补齐两期有效询盘汇总，并按档案核对观察天数和最低样本。')
    current = windows['current']
    spend = current['spend']
    count = current['qualified_count']
    result['current_cost_inputs'] = dict(spend=spend,qualified_count=count)
    if spend is None or Decimal(spend) == 0:
        return dict(result,target_check='POSITIVE_OBSERVED_SPEND_REQUIRED',
                    next_check='核对后期花费及询盘归属后再估算测试样本。')
    if target is None:
        return dict(result,target_check='TARGET_NOT_DECLARED',next_check='由客户确认有效询盘目标成本，再复核测试规模。')
    result.update(estimate_test(spend,count,target,cap,review['declared_minimum_results']))
    if result['target_check'] == 'ABOVE_TARGET':
        return dict(result,decision='INVESTIGATE_COST',
                    next_check='按有效询盘资格和渠道归属复查后期成本，选一个待查假设。')
    if result['capacity_check'] == 'SUPPORTS_DECLARED_SAMPLE_ESTIMATE':
        return dict(result,decision='REVIEW_ONE_TEST_PROPOSAL',
                    next_check='由客户核对现有日投放保护额和一个概念的测试条件，再审阅草案。')
    return dict(result,next_check='维持当前投放安排，复核测试总上限和最低样本量。')


def schema():
    patterns = {key:{'type':'string','pattern':'^'+value+'$'} for key,value in PATTERNS.items()}
    window_properties = {
        **{key:{'type':'string','format':'date'} for key in ('date_start','date_stop')},
        'qualified_count':{'type':['integer','null'],'minimum':0,'maximum':COUNT_MAX},
        'evidence_ref':{'type':'string','pattern':'^'+EVIDENCE_REF+'$'},
        'observed_at':{'type':'string','format':'date-time'}
    }
    window = dict(type='object',additionalProperties=False,required=list(window_properties),properties=window_properties)
    periods = dict(type='object',additionalProperties=False,required=list(PERIODS),
                   properties=dict.fromkeys(PERIODS,window))
    properties = dict(schema={'const':SCHEMA},**patterns,periods=periods)
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','title':'Owner-declared qualified lead evidence',
            'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}
