"""Read the recorded launch Ad in an existing account Insights archive."""

from decimal import Decimal

from .ads_reconciliation import read_run_observations
from .analysis import aggregate_ads
from .first_launch_activation import account_days_against_window, daily_window, read_review
from .first_launch_handoff import activation_paths, resume_manual
from .monitor import account_cycle_pagination_unverified
from .review import ads_analysis, daily_date_coverage
from .validation import aware_time, fail


def _stored_control_window(attempt):
    if attempt is None or 'guard_effect' not in attempt:
        return None
    effect = attempt['guard_effect']
    return dict(starts_at=max(aware_time(effect['enforced_start_at']), aware_time(attempt['reserved_at'])).isoformat(),
                ends_at=effect['enforced_stop_at'], currency=effect['currency'], timezone=effect['timezone'],
                maximum_spend_minor=effect['enforced_spend_cap_minor'], basis='STORED_ACTIVATION_ATTEMPT')


def delivery_review(workspace, directory, receipt, since, until, *, activation_review_path=None,
                    manual_before=None, manual_after=None, manual_ad=None):
    explicit_manual = any(path is not None for path in (manual_before, manual_after, manual_ad))
    activation_review_path, before, after, ad_path = activation_paths(
        workspace, directory, review=activation_review_path,
        before=manual_before, after=manual_after, ad=manual_ad)
    launch = resume_manual(workspace, directory, (activation_review_path, before, after, ad_path))
    if launch['status'] != 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED':
        fail('FIRST_LAUNCH_OBJECT_CHAIN_REQUIRED')
    attempt = launch.get('activation_attempt')
    manual_attempt = launch.get('manual_activation_attempt')
    if attempt is not None and explicit_manual:
        fail('FIRST_LAUNCH_REPORT_ACTIVATION_SOURCE_CONFLICT')
    observation, run = read_run_observations(receipt, workspace_insights=True)
    sources = observation['sources']
    query = sources['ads']['plan']
    if (observation['workspace_identity_sha256'] != launch['workspace_identity_sha256']
            or query['account_id'] != launch['account_id']):
        fail('FIRST_LAUNCH_REPORT_WORKSPACE_MISMATCH')
    if (query['since'], query['until']) != (since, until):
        fail('FIRST_LAUNCH_REPORT_WINDOW_MISMATCH')
    ad = launch['stages'][-1]['readback']['object']
    report = dict(status='FIRST_LAUNCH_DELIVERY_REVIEWED', ad_id=ad['id'],
                  account_id=launch['account_id'], stage_sha256=launch['stages'][-1]['reservation']['stage_sha256'],
                  run=run, reviewed_window={'since':since,'until':until}, metrics=None,
                  last_decision=launch['checkpoint'],
                  independent_activation_approval_verified=False, spend_authorized=False,
                  identity_source=('STORED_HOST_CREATION_READBACK' if launch['stages'][-1].get('host_attempt') is not None
                                   else 'LOCAL_DECLARED_CREATION_READBACK'), network_used=False)
    if attempt is not None:
        report['stored_activation_attempt'] = {key:attempt[key] for key in ('attempt_id','review_sha256','state','observation')}
        readback = attempt['observation'] or {}
        report['platform_activation_state'] = dict(configured_status=readback.get('configured_status'),
                                                   effective_status=readback.get('effective_status'),
                                                   observed_at=readback.get('observed_at'))
    stored_window = _stored_control_window(attempt)
    if stored_window is not None:
        report.update(stored_control_window=stored_window, report_days_in_stored_control_window=None,
                      impressions_in_stored_control_window='NOT_ESTABLISHED_FROM_DAILY_BUCKETS')
    activation_review = None
    manual_window = None
    if manual_attempt is not None:
        activation_review = manual_attempt['review']
    elif activation_review_path is not None:
        activation_review = read_review(launch, activation_review_path)
    if activation_review is not None:
        binding = activation_review['binding']
        report.update(activation_review_sha256=activation_review['review_sha256'],
                      proposed_activation_window=dict(starts_at=binding['starts_at'], ends_at=binding['ends_at']),
                      report_days_in_proposed_window=None,
                      impressions_in_proposed_window='NOT_ESTABLISHED_FROM_DAILY_BUCKETS')
        if attempt is not None:
            report['stored_activation_matches_review'] = attempt['review_sha256'] == activation_review['review_sha256']
    if explicit_manual and manual_attempt is None:
        fail('FIRST_LAUNCH_HANDOFF_PRIOR_OBSERVATION_REQUIRED')
    if manual_attempt is not None:
        parent, manual = manual_attempt['after'], manual_attempt['observation']
        binding = activation_review['binding']
        report['manual_activation'] = dict(
            source='OPERATOR_DECLARED_PENDING_EXTERNAL_VERIFICATION', attempt_id=manual_attempt['attempt_id'],
            plan_sha256=binding['plan_sha256'], account_id=binding['account_id'], ad_id=binding['ad_id'],
            review_sha256=activation_review['review_sha256'], evidence_ref=manual['evidence_ref'],
            approval_ref=manual['ad_approval_ref'], control_evidence_ref=parent['control_evidence_ref'],
            attempt_at=manual['attempt_at'], observed_at=manual['observed_at'],
            configured_status=manual['configured_status'], effective_status=manual['effective_status'])
        report['manual_delivery_acceptance'] = 'PENDING_INDEPENDENT_EXTERNAL_EVIDENCE'
        if manual['configured_status'] == 'ACTIVE':
            manual_window = dict(starts_at=manual['attempt_at'], ends_at=parent['enforceable_stop_at'],
                                 currency=binding['currency'], timezone=binding['timezone'],
                                 maximum_spend_minor=parent['enforceable_spend_cap_minor'],
                                 basis='OPERATOR_DECLARED_CONTROL_WINDOW')
            report.update(manual_control_window=manual_window, report_days_in_manual_window=None,
                          impressions_in_manual_window='NOT_ESTABLISHED_FROM_DAILY_BUCKETS')
    scope = ads_analysis(sources, pagination_unverified=account_cycle_pagination_unverified(observation))
    if scope['state'] != 'COMPUTED':
        return dict(report, delivery_state='NOT_OBSERVED', reason=scope['reason'],
                    recommendation='WAIT_FOR_USABLE_REPORT')
    context = scope['context']
    if activation_review is not None:
        report['report_days_in_proposed_window'] = daily_window(activation_review, context)
    if stored_window is not None:
        report['report_days_in_stored_control_window'] = account_days_against_window(stored_window, context)
    if manual_window is not None:
        report['report_days_in_manual_window'] = account_days_against_window(manual_window, context)
    rows = [row for row in sources['ads']['rows'] if row['ad_id'] == ad['id']]
    if any(key in row and row[key] != ad[key] for row in rows for key in ('campaign_id','adset_id')):
        fail('FIRST_LAUNCH_REPORT_PARENT_MISMATCH')
    completed = scope['analysis']['coverage']=='COMPLETE'
    coverage = daily_date_coverage(rows, context)
    report.update(currency=context['currency'], timezone=context['timezone'], attribution=context['attribution'],
                  coverage=coverage, observation=scope['evidence']['insights'],
                  source_state=sources['ads']['result'])
    if not rows:
        return dict(report, delivery_state='AD_ABSENT' if completed else 'NOT_OBSERVED',
                    recommendation='HOLD_NO_OBSERVED_ROWS')
    covered = completed and coverage['state']=='ALL_DATES_RETURNED'
    metrics = aggregate_ads(rows,context,context,'COMPLETE' if covered else 'PARTIAL')['metrics']
    impressions = metrics['impressions']['value']
    delivery = 'NOT_OBSERVED'
    if impressions is not None:
        delivery = 'POSITIVE_IMPRESSIONS_OBSERVED' if Decimal(impressions)>0 else 'OBSERVED_ZERO_IMPRESSIONS'
    report.update(metrics=metrics, delivery_state=delivery,
                  recommendation='REVIEW_SAME_AD_AND_WINDOW' if covered else 'HOLD_INCOMPLETE_WINDOW')
    if activation_review is not None:
        if report['report_days_in_proposed_window']['relation'] == 'WHOLLY_WITHIN_PROPOSED_WINDOW':
            report['impressions_in_proposed_window'] = delivery
        else:
            report['recommendation'] = 'REVIEW_COVERAGE_OF_PROPOSED_WINDOW'
    if (stored_window is not None
            and report['report_days_in_stored_control_window']['relation'] == 'WHOLLY_WITHIN_WINDOW'):
        report['impressions_in_stored_control_window'] = delivery
    if manual_window is not None:
        if report['report_days_in_manual_window']['relation'] == 'WHOLLY_WITHIN_WINDOW':
            report['impressions_in_manual_window'] = delivery
        else:
            report['recommendation'] = 'REVIEW_COVERAGE_OF_MANUAL_WINDOW'
    if (launch['declared_result_type'] == 'PURCHASE' and
            metrics.get('website_purchases', {}).get('value') is None):
        report['purchase_evidence'] = 'WEBSITE_PURCHASE_NOT_OBSERVED'
    return report
