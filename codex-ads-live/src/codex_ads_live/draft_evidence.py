"""Reopen compatible advertising comparisons as local draft evidence."""
from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
import re

from . import qualified_leads
from .analysis import WEBSITE_ATTRIBUTION, validate_context
from .journal import canonical, digest
from .local_paths import checked_path
from .monitor import ACCOUNT_CYCLE_PAGINATION, ACCOUNT_CYCLE_PAGINATION_UNVERIFIED
from .proposal_estimate import display_ratio, estimate_test
from .timezones import load_zone
from .validation import aware_time, fail, object_only, strict_json

COMPARISON_LIMIT = 4_000_000
SHA256 = re.compile('[0-9a-f]{64}')
# Account totals can have more digits than a single Insights row.
OBSERVED_SPEND = re.compile(r'[0-9]{1,28}(?:\.[0-9]{1,12})?')
RUN_ID = re.compile('[0-9a-f]{32}')
RUN_FIELDS = {'run_id','receipt_sha256','report_sha256','status','report_binding','query_scope_binding'}
RUN_STATES = ('MONITOR_RUN_ENDED','MONITOR_REVIEW_REQUIRED','MONITOR_RUN_FAILED','INTERRUPTED')
RUN_VERIFICATIONS = ('RECORDED_IN_COMPARISON','ARCHIVE_ROWS_RECHECKED')


def _read(directory, name, limit):
    path = checked_path(directory/name,link_error='DRAFT_REVIEW_PATH_LINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if not path.is_file():
        fail('DRAFT_REVIEW_FILE_REQUIRED')
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        fail('DRAFT_REVIEW_FILE_TOO_LARGE')
    return raw


def _object(container, key):
    value = container.get(key)
    if type(value) is not dict:
        fail('DRAFT_REVIEW_FORMAT')
    return value


def _archive(directory):
    receipt = strict_json(_read(directory,'RECEIPT.json',16_000).decode('utf-8'))
    if (type(receipt) is not dict or receipt.get('status') != 'PERIOD_REVIEW_WRITTEN'
            or receipt.get('mode') != 'READ_ONLY' or receipt.get('comparison_state') != 'COMPARED'):
        fail('DRAFT_REVIEW_COMPLETED_COMPARISON_REQUIRED')
    files = receipt.get('files')
    if type(files) is not list or any(type(item) is not dict for item in files):
        fail('DRAFT_REVIEW_FORMAT')
    matches = [item for item in files if item.get('path') == 'COMPARISON.json']
    raw = _read(directory,'COMPARISON.json',COMPARISON_LIMIT)
    sha256 = hashlib.sha256(raw).hexdigest()
    if matches != [dict(path='COMPARISON.json',bytes=len(raw),sha256=sha256)]:
        fail('DRAFT_REVIEW_FILE_CHANGED')
    report = strict_json(raw.decode('utf-8'),max_chars=COMPARISON_LIMIT)
    if (type(report) is not dict or report.get('schema') != 'bounded-ads-period-review-v1'
            or report.get('state') != 'COMPARED' or report.get('mode') != 'READ_ONLY'):
        fail('DRAFT_REVIEW_COMPLETED_COMPARISON_REQUIRED')
    return report, receipt, sha256


def _checked_periods(report, business, account_id):
    periods = _object(report,'periods')
    labels = qualified_leads.PERIODS
    object_only(periods,labels,labels)
    projected_periods = {}
    for label in labels:
        period = _object(periods,label)
        scope = _object(period,'context')
        validate_context(scope)
        if (scope['account_id'],scope['currency']) != (account_id,business['currency']):
            fail('DRAFT_REVIEW_ACCOUNT_SCOPE')
        if period.get('coverage') != 'COMPLETE':
            fail('DRAFT_REVIEW_COMPLETED_COMPARISON_REQUIRED')
        if 'account_cycle' in period:
            cycle = _object(period,'account_cycle')
            object_only(cycle,('cycle','pagination'),('cycle','pagination'))
            if (type(cycle['cycle']) is not int or cycle['cycle'] < 1
                    or cycle['pagination'] not in ('UNRECORDED',ACCOUNT_CYCLE_PAGINATION)):
                fail('DRAFT_REVIEW_FORMAT')
            if cycle['cycle']>1 and cycle['pagination']!=ACCOUNT_CYCLE_PAGINATION:
                fail(ACCOUNT_CYCLE_PAGINATION_UNVERIFIED)
        hash_value = _object(_object(period,'evidence'),'insights').get('observation_sha256')
        if type(hash_value) is not str or not SHA256.fullmatch(hash_value):
            fail('DRAFT_REVIEW_FORMAT')
        projected_periods[label] = dict(context=scope,evidence={'insights':{'observation_sha256':hash_value}})
    old,new = (projected_periods[label]['context'] for label in labels)
    if (any(old[key] != new[key] for key in old if key not in ('date_start','date_stop'))
            or old['date_stop'] >= new['date_start']):
        fail('DRAFT_REVIEW_ACCOUNT_SCOPE')
    days = (date.fromisoformat(new['date_stop']) - date.fromisoformat(new['date_start'])).days + 1
    if days != (date.fromisoformat(old['date_stop']) - date.fromisoformat(old['date_start'])).days + 1:
        fail('DRAFT_REVIEW_ACCOUNT_SCOPE')
    return projected_periods, days


def _amount(value):
    if value is not None and (type(value) is not str or not OBSERVED_SPEND.fullmatch(value)):
        fail('DRAFT_REVIEW_FORMAT')
    return value


def _rebuild(report, basis, business, account_id):
    """Recalculate the qualified-lead basis from a strict projection."""
    sample = _object(report,'sample_review')
    windows = _object(sample,'qualified_lead_periods')
    labels = qualified_leads.PERIODS
    object_only(windows,labels,labels)
    raw_spend = _object(_object(report,'account_metrics'),'spend')
    projected_periods, days = _checked_periods(report,business,account_id)
    inputs = {}
    sums = {}
    for label in labels:
        row = _object(windows,label)
        if not qualified_leads.WINDOW_FIELDS <= row.keys() or label not in raw_spend:
            fail('DRAFT_REVIEW_FORMAT')
        inputs[label] = {key:row[key] for key in qualified_leads.WINDOW_FIELDS}
        sums[label] = _amount(raw_spend[label])
    new = projected_periods['current']['context']
    evidence = qualified_leads.validate(dict(
        schema=qualified_leads.SCHEMA,account_id=account_id,currency=new['currency'],timezone=new['timezone'],
        business_profile_sha256=digest(business),periods=inputs))
    # Spend and declared outcomes are sufficient for proposal calculation.
    check = qualified_leads.review(evidence,business,dict(periods=projected_periods,days_per_period=days,
                                                          account_metrics={'spend':sums}))
    rebuilt = qualified_leads.proposal_basis(check,business,projected_periods)
    if canonical(rebuilt) != canonical(basis):
        fail('DRAFT_REVIEW_BASIS_MISMATCH')
    return rebuilt


def _platform_amounts(metrics, name):
    values = _object(metrics,name)
    amounts = {}
    for label in qualified_leads.PERIODS:
        status = values.get(label+'_status')
        if label not in values or status not in ('VALUE','EMPTY','PARTIAL','MISSING','UNKNOWN'):
            fail('DRAFT_REVIEW_PURCHASE_METRIC_INVALID')
        value = _amount(values[label])
        if (status=='VALUE') != (value is not None):
            fail('DRAFT_REVIEW_PURCHASE_METRIC_INVALID')
        amounts[label] = value
    return amounts


def _purchase_basis(report, business, account_id):
    from .period_review import sample_review

    periods, days = _checked_periods(report,business,account_id)
    context = periods['current']['context']
    if context['attribution']!=WEBSITE_ATTRIBUTION:
        fail('DRAFT_REVIEW_WEBSITE_PURCHASE_SCOPE_REQUIRED')
    metrics = _object(report,'account_metrics')
    spend = _platform_amounts(metrics,'spend')
    counts = _platform_amounts(metrics,'website_purchases')
    costs = _platform_amounts(metrics,'website_purchase_cpa')
    sample = sample_review(business,context,{'website_purchases':counts},days)
    if sample!=report['sample_review']:
        fail('DRAFT_REVIEW_PURCHASE_SAMPLE_MISMATCH')
    observations = {}
    zone = load_zone(context['timezone'])
    for label, period in periods.items():
        period_context = period['context']
        evidence = _object(_object(report['periods'][label],'evidence'),'insights')
        observed = aware_time(evidence.get('observed_at'))
        end = date.fromisoformat(period_context['date_stop'])
        lag = (observed.astimezone(zone).date()-end).days
        if lag<1 or lag!=report['periods'][label].get('calendar_days_after_period_end_at_observation'):
            fail('DRAFT_REVIEW_PURCHASE_OBSERVATION_TIME')
        cost, paid, count = costs[label], spend[label], counts[label]
        if cost is not None and (paid is None or count is None or Decimal(count)<=0
                                 or cost!=display_ratio(Fraction(Decimal(paid))/Fraction(Decimal(count)))):
            fail('DRAFT_REVIEW_PURCHASE_COST_MISMATCH')
        observations[label] = dict(date_start=period_context['date_start'],date_stop=period_context['date_stop'],
                                   observed_at=observed.isoformat(),calendar_days_after_period_end=lag,
                                   insights_rows_sha256=evidence['observation_sha256'],
                                   spend=paid,attributed_website_purchases=count,attributed_purchase_cpa=cost)
    target, cap = business['target_cost_per_result'], business['maximum_test_spend']
    result = dict(evidence_basis='META_WEBSITE_PURCHASE_EXPLICIT_7D_CLICK',
                  account_id=account_id,currency=context['currency'],timezone=context['timezone'],
                  profile_sha256=digest(business),sample_state=sample['state'],observations=observations,
                  action_report_time_requested=context['action_report_time'],action_report_time_effective='UNKNOWN',
                  data_as_of=None,business_result_definition_verified=False,
                  decision='DEFER_CHANGE',target_cost_per_result=target,maximum_test_spend=cap,
                  protected_daily_spend=business['protected_daily_spend'],minimum_results=business['minimum_results'],
                  target_check='NOT_EVALUATED',capacity_check='NOT_EVALUATED',estimated_test_capacity=None,
                  causal_claim_allowed=False,remote_write_authorized=False,dispatchable=False,
                  next_check='核对各期样本、购买事件和归因回补，并按同范围核对实际订单。')
    if sample['state']!='SAMPLE_FLOOR_MET_IN_PLATFORM_DATA':
        return result
    if costs['current'] is None or Decimal(spend['current'])==0:
        return dict(result,target_check='POSITIVE_OBSERVED_PURCHASE_COST_REQUIRED')
    if target is None:
        return dict(result,target_check='TARGET_NOT_DECLARED')
    result.update(estimate_test(spend['current'],Decimal(counts['current']),target,cap,business['minimum_results']))
    if result['target_check']=='ABOVE_TARGET':
        return dict(result,decision='INVESTIGATE_COST',next_check='先核对购买事件、归因回补和同范围订单，再调查成本差异。')
    if result['capacity_check']=='SUPPORTS_DECLARED_SAMPLE_ESTIMATE':
        return dict(result,decision='REVIEW_ONE_TEST_PROPOSAL',
                    next_check='核对购买事件、实际订单和归因回补，再按客户确认的条件审阅一个测试假设。')
    return result


def validate_comparison_input_runs(runs):
    error = 'DRAFT_REVIEW_SOURCE_REFERENCES_INVALID'
    if type(runs) is not dict or runs.keys()!=set(qualified_leads.PERIODS):
        fail(error)
    for run in runs.values():
        if (type(run) is not dict or run.keys()!=RUN_FIELDS
                or type(run['run_id']) is not str or not RUN_ID.fullmatch(run['run_id'])
                or any(type(run[key]) is not str or not SHA256.fullmatch(run[key])
                       for key in ('receipt_sha256','report_sha256'))
                or run['status'] not in RUN_STATES or run['report_binding']!='RUN_REPORT_RECEIPT_MATCHED'
                or run['query_scope_binding']!='PLAN_CONSENT_QUERY_MATCHED'):
            fail(error)
    if runs['previous']['run_id']==runs['current']['run_id']:
        fail(error)


def _source_evidence(report, receipt, run_receipts, workspace_identity):
    if 'input_runs' not in report and 'input_runs' not in receipt:
        if run_receipts is not None: fail('DRAFT_REVIEW_SOURCE_REFERENCES_REQUIRED')
        return {}
    runs = report.get('input_runs')
    validate_comparison_input_runs(runs)
    if runs!=receipt.get('input_runs'):
        fail('DRAFT_REVIEW_SOURCE_REFERENCES_INVALID')
    if run_receipts is None:
        return dict(input_runs=runs,input_runs_verification='RECORDED_IN_COMPARISON')
    if type(run_receipts) not in (tuple,list) or len(run_receipts)!=2:
        fail('DRAFT_REVIEW_TWO_SOURCE_RECEIPTS_REQUIRED')
    from .ads_reconciliation import read_run_observations
    from .period_review import build_period_review

    observations = []
    for label,path in zip(qualified_leads.PERIODS,run_receipts):
        state,run = read_run_observations(path,workspace_insights=True)
        if run!=runs[label] or state['workspace_identity_sha256']!=workspace_identity:
            fail('DRAFT_REVIEW_SOURCE_RUN_MISMATCH')
        observations.append(state)
    replay = build_period_review(*observations)
    if (replay['state']!='COMPARED' or replay['periods']!=report['periods']
            or replay['account_metrics']!=report['account_metrics']):
        fail('DRAFT_REVIEW_SOURCE_TOTALS_MISMATCH')
    return dict(input_runs=runs,input_runs_verification='ARCHIVE_ROWS_RECHECKED')


def load_draft_context(directory, account_id, business, *, workspace_identity, run_receipts=None):
    directory = checked_path(directory,link_error='DRAFT_REVIEW_PATH_LINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    report, receipt, sha256 = _archive(directory)
    field = 'workspace_identity_sha256'
    if field not in report and field not in receipt:
        fail('DRAFT_REVIEW_WORKSPACE_BINDING_REQUIRED')
    archived_identity = report.get(field)
    if (type(archived_identity) is not str or not SHA256.fullmatch(archived_identity)
            or archived_identity!=receipt.get(field)):
        fail('DRAFT_REVIEW_WORKSPACE_BINDING_INVALID')
    if archived_identity!=workspace_identity:
        fail('DRAFT_REVIEW_WORKSPACE_MISMATCH')
    basis = report.get('qualified_lead_proposal_basis')
    sample = report.get('sample_review')
    if basis is None and type(sample) is dict and sample.get('declared_result_type')=='PURCHASE':
        if business is None or business['result_type']!='PURCHASE' or sample.get('profile_sha256')!=digest(business):
            fail('DRAFT_REVIEW_BUSINESS_PROFILE_CHANGED')
        rebuilt = _purchase_basis(report,business,account_id)
    else:
        if type(basis) is not dict or basis.get('account_id') is None:
            fail('DRAFT_REVIEW_QUALIFIED_BASIS_REQUIRED')
        if basis['account_id'] != account_id:
            fail('DRAFT_REVIEW_ACCOUNT_SCOPE')
        if business is None or basis.get('profile_sha256') != digest(business):
            fail('DRAFT_REVIEW_BUSINESS_PROFILE_CHANGED')
        rebuilt = _rebuild(report,basis,business,account_id)
        if (receipt.get('qualified_lead_evidence_sha256') != rebuilt['qualified_lead_evidence_sha256']
                or receipt.get('qualified_lead_proposal_decision') != rebuilt['decision']):
            fail('DRAFT_REVIEW_BASIS_MISMATCH')
    sources = _source_evidence(report,receipt,run_receipts,archived_identity)
    return dict(schema='workspace-draft-review-context-v1',comparison_sha256=sha256,
                workspace_identity_sha256=archived_identity,
                verification='ARCHIVED_REPORT_RECEIPT_AND_CURRENT_WORKSPACE_AND_BUSINESS_PROFILE',
                historical_observations=True,source_refetched=False,
                proposal_basis=rebuilt,dispatchable=False,remote_write_authorized=False,**sources)
