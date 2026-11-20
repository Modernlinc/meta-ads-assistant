"""Descriptive comparison of two validated account observations."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN

from .analysis import WEBSITE_ATTRIBUTION, aggregate_ads
from .journal import digest
from .monitor import ACCOUNT_CYCLE_PAGINATION_UNVERIFIED, account_cycle_basis, account_cycle_pagination_unverified
from .review import ACCOUNT_DAILY_ROWS_INCOMPLETE, ads_analysis, daily_date_coverage
from .timezones import load_zone
from .validation import aware_time, fail


CONVERSION_METRICS = ('website_purchase_cpa', 'website_purchase_roas', 'website_lead_cpl')
RATIO_INPUTS = {
    'ctr_percent': ('clicks','impressions',100),
    'cpm': ('spend','impressions',1000),
    'website_purchase_cpa': ('spend','website_purchases',1),
    'website_purchase_roas': ('website_purchase_value','spend',1),
    'website_lead_cpl': ('spend','website_leads',1),
}


def arithmetic_value(metrics, name):
    if name in RATIO_INPUTS:
        numerator,denominator,factor = RATIO_INPUTS[name]
        return Decimal(metrics[numerator]['value'])*factor/Decimal(metrics[denominator]['value'])
    return Decimal(metrics[name]['value'])


def compare_metrics(previous, current, names):
    changes = {}
    with localcontext() as context:
        context.prec = 64
        context.rounding = ROUND_HALF_EVEN
        for name in names:
            before, after = previous[name]['value'], current[name]['value']
            entry = dict(previous=before, current=after, delta=None, change_percent=None,
                         previous_status=previous[name]['status'], current_status=current[name]['status'])
            if before is not None and after is not None:
                baseline = arithmetic_value(previous,name)
                delta = arithmetic_value(current,name) - baseline
                entry['delta'] = format(delta,'.4f' if name in RATIO_INPUTS else 'f')
                if baseline:
                    entry['change_percent'] = format(delta / baseline * 100,'.4f')
            changes[name] = entry
    return changes


def group_ads(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['ad_id']].append(row)
    return grouped


def sample_review(profile, context, metrics, days):
    if profile is None:
        return dict(state='BUSINESS_PROFILE_NOT_AVAILABLE')
    reference = dict(profile_sha256=digest(profile), declared_result_type=profile['result_type'])
    if profile['currency'] != context['currency']:
        return dict(reference, state='BUSINESS_PROFILE_CURRENCY_MISMATCH')
    event = {'PURCHASE':'website_purchases','LEAD':'website_leads'}.get(profile['result_type'])
    if event is None or event not in metrics:
        return dict(reference, state='BUSINESS_RESULT_EVIDENCE_REQUIRED')
    window, minimum = profile['observation_days'], profile['minimum_results']
    counts = dict(previous=metrics[event]['previous'],current=metrics[event]['current'])
    reference.update(sample_metric=event, sample_counts=counts)
    if window is None or minimum is None:
        state = 'DECLARED_SAMPLE_RULE_INCOMPLETE'
    elif days < window:
        state = 'BELOW_DECLARED_OBSERVATION_DAYS'
    elif None in counts.values():
        state = 'PLATFORM_EVENT_COUNT_INCOMPLETE'
    elif min(map(Decimal,counts.values())) < minimum:
        state = 'BELOW_DECLARED_MINIMUM_RESULTS'
    else:
        state = 'SAMPLE_FLOOR_MET_IN_PLATFORM_DATA'
    return dict(reference, state=state, declared_observation_days=window, declared_minimum_results=minimum,
                business_result_definition_verified=False)


def build_period_review(previous, current, workspace=None, *, alert_rules=None, qualified_lead_evidence=None):
    if alert_rules is not None:
        from . import period_alerts
        alert_rules = period_alerts.validate_rules(alert_rules)
    if qualified_lead_evidence is not None:
        from . import qualified_leads
        if workspace is None:
            fail('QUALIFIED_LEADS_WORKSPACE_REQUIRED')
        qualified_lead_evidence = qualified_leads.validate(qualified_lead_evidence)
    report = dict(schema='bounded-ads-period-review-v1', state='NOT_COMPARABLE', reason=None, mode=None,
                  periods={}, account_metrics={}, shared_ad_metrics={}, shared_ad_only_spend_delta=None,
                  ad_composition=None, sample_review=None, data_as_of=None, freshness='UNKNOWN_SOURCE_UPDATE_TIME',
                  causal_claim_allowed=False,
                  live_action_authorized=False, remote_write_authorized=False)

    def blocked(reason):
        if alert_rules is not None:
            report['alert_review'] = period_alerts.pending(alert_rules,reason)
        if qualified_lead_evidence is not None:
            report['sample_review'] = dict(state='QUALIFIED_LEADS_PERIOD_COMPARISON_REQUIRED',
                                           qualified_lead_evidence_sha256=digest(qualified_lead_evidence))
            report['qualified_lead_proposal_basis'] = qualified_leads.proposal_basis(report['sample_review'],None,report['periods'])
        return dict(report, reason=reason)

    if previous['mode'] != current['mode']:
        return blocked('OBSERVATION_MODE_MISMATCH')
    report['mode'] = previous['mode']
    identities = [state.get('workspace_identity_sha256') for state in (previous,current)]
    if report['mode']=='READ_ONLY' and all(identities) and identities[0]!=identities[1]:
        return blocked('OBSERVATION_WORKSPACE_MISMATCH')
    windows = [ads_analysis(state.get('sources',{})) for state in (previous,current)]
    for label, window, state in zip(('previous','current'),windows,(previous,current)):
        if window['state']!='COMPUTED':
            report['periods'][label] = dict(state=window['state'], reason=window['reason'])
        else:
            report['periods'][label] = dict(context=window['context'], evidence=window['evidence'],
                                           action_reporting=window['action_reporting'],
                                           coverage=window['analysis']['coverage'], row_count=window['analysis']['row_count'])
        basis = account_cycle_basis(state)
        if basis is not None:
            report['periods'][label]['account_cycle'] = basis
    if any(map(account_cycle_pagination_unverified,(previous,current))):
        return blocked(ACCOUNT_CYCLE_PAGINATION_UNVERIFIED)
    if any(window['state']!='COMPUTED' for window in windows):
        return blocked('TWO_ADS_OBSERVATIONS_REQUIRED')
    left,right = (window['context'] for window in windows)
    if any(left[key]!=right[key] for key in left if key not in ('date_start','date_stop')):
        return blocked('REPORT_CONTEXT_MISMATCH')
    first_start,first_end,second_start,second_end = (
        date.fromisoformat(context[key]) for context in (left,right) for key in ('date_start','date_stop'))
    days = (first_end-first_start).days + 1
    if first_end>=second_start:
        return blocked('PERIODS_OVERLAP_OR_OUT_OF_ORDER')
    if days != (second_end-second_start).days + 1:
        return blocked('PERIOD_LENGTH_MISMATCH')
    if any(window['analysis']['coverage']!='COMPLETE' for window in windows):
        return blocked('COMPLETE_PERIODS_REQUIRED')
    if any(not window['analysis']['row_count'] for window in windows):
        return blocked('EMPTY_PERIOD_HAS_NO_NUMERIC_TOTAL')
    zone = load_zone(left['timezone'])
    for label, window, last_day in zip(('previous','current'),windows,(first_end,second_end)):
        stamp = window['evidence']['insights']['observed_at']
        observed_day = aware_time(stamp).astimezone(zone).date() if stamp is not None else None
        if observed_day is None or observed_day<=last_day:
            return blocked('ACCOUNT_LOCAL_PERIOD_NOT_CLOSED_AT_OBSERVATION')
        report['periods'][label]['calendar_days_after_period_end_at_observation'] = (observed_day-last_day).days

    previous_metrics,current_metrics = (window['analysis']['metrics'] for window in windows)
    for label, window in zip(('previous','current'),windows):
        daily = window['daily_date_coverage']
        report['periods'][label]['observed_days'] = daily['observed_days']
        if daily['missing_dates']:
            report['periods'][label]['missing_dates'] = daily['missing_dates']
    if any(period['observed_days']<days for period in report['periods'].values()):
        return blocked(ACCOUNT_DAILY_ROWS_INCOMPLETE)
    profile = None
    configured_identity = None
    if workspace is not None:
        from .client_workspace import asset_key, read, workspace_identity
        configured = read(workspace)
        account = left['account_id']
        if not account.isdecimal() or asset_key('ACCOUNT',account) not in configured['assets']:
            fail('PERIOD_REVIEW_WORKSPACE_ACCOUNT_MISMATCH')
        configured_identity = workspace_identity(configured)
        if report['mode']=='READ_ONLY' and any(identity!=configured_identity for identity in identities):
            return blocked('WORKSPACE_SCOPE_UNVERIFIED')
        if report['mode']=='READ_ONLY':
            report['workspace_identity_sha256'] = configured_identity
        profile = configured.get('business_profiles',{}).get(account)
    changes = compare_metrics(previous_metrics,current_metrics,previous_metrics)
    ad_rows = [group_ads(state['sources']['ads']['rows']) for state in (previous,current)]
    old_ids,new_ids = (set(rows) for rows in ad_rows)
    shared_ids = old_ids & new_ids
    names = ('spend', *CONVERSION_METRICS) if left['attribution']==WEBSITE_ATTRIBUTION else ('spend','ctr_percent','cpm')
    per_ad = {}
    for ad_id in sorted(shared_ids):
        old_rows,new_rows = (rows[ad_id] for rows in ad_rows)
        previous_dates = daily_date_coverage(old_rows,left)
        current_dates = daily_date_coverage(new_rows,right)
        previous_coverage = 'PARTIAL' if previous_dates['missing_dates'] else 'COMPLETE'
        current_coverage = 'PARTIAL' if current_dates['missing_dates'] else 'COMPLETE'
        before = aggregate_ads(old_rows,left,left,previous_coverage)['metrics']
        after = aggregate_ads(new_rows,right,right,current_coverage)['metrics']
        coverage = 'COMPLETE' if previous_coverage==current_coverage=='COMPLETE' else 'PARTIAL'
        per_ad[ad_id] = dict(previous_observed_days=previous_dates['observed_days'],
                             current_observed_days=current_dates['observed_days'],coverage=coverage,
                             period_coverage=dict(previous=previous_dates,current=current_dates),
                             metrics=compare_metrics(before,after,names))
    spend_deltas = [ad['metrics']['spend']['delta'] for ad in per_ad.values()]
    shared_spend_delta = None
    if spend_deltas and None not in spend_deltas:
        with localcontext() as arithmetic:
            arithmetic.prec = 64
            shared_spend_delta = format(sum(map(Decimal,spend_deltas),Decimal(0)),'f')
    report.update(state='COMPARED',days_per_period=days, account_metrics=changes,
                shared_ad_metrics=per_ad, shared_ad_only_spend_delta=shared_spend_delta,
                same_weekday_mix=days%7==0 or first_start.weekday()==second_start.weekday(),
                ad_composition=dict(shared_count=len(shared_ids),only_previous=sorted(old_ids-new_ids),
                                    only_current=sorted(new_ids-old_ids)),
                sample_review=sample_review(profile,right,changes,days))
    if qualified_lead_evidence is not None:
        if report['mode']!='READ_ONLY':
            report['sample_review'] = dict(state='QUALIFIED_LEADS_REQUIRES_SCOPED_ACCOUNT_READS',
                                           qualified_lead_evidence_sha256=digest(qualified_lead_evidence))
        else:
            report['sample_review'] = qualified_leads.review(qualified_lead_evidence,profile,report)
        report['qualified_lead_proposal_basis'] = qualified_leads.proposal_basis(report['sample_review'],profile,report['periods'])
    if alert_rules is not None:
        identity = previous.get('workspace_identity_sha256')
        scope_verified = (report['mode']=='MOCK' or bool(identity and identity==current.get('workspace_identity_sha256')
                                                         and configured_identity in (None,identity)))
        report['alert_review'] = period_alerts.evaluate(alert_rules,report,previous_metrics,current_metrics,
                                                      scope_verified=scope_verified)
    return report


def alert_markdown(report):
    alerts = report.get('alert_review')
    if alerts is None: return []
    lines = ['', '## 阈值复查提示', '', f"规则摘要 `{alerts['rules_sha256']}`。"
             f"状态 `{alerts['state']}`，出现 {alerts['review_signals']} 项复查提示。",
             f"适用账户 `{alerts['rule_scope']['account_id']}`，币种 `{alerts['rule_scope']['currency']}`。"
             f"采集至少距各期期末 {alerts['minimum_lag_days']} 个账户当地日历日。",
             '', '| 指标 | 方向 | 阈值 % | 相对变化 % | 样本 | 结果 |', '|---|---|---:|---:|---|---|']
    for row in alerts['checks']:
        sample = row['sample_values'] or {}
        previous, current = (sample.get(label) or '未知' for label in ('previous','current'))
        lines.append(f"| {row['metric']} | {row['direction']} | {row['threshold_percent']} | "
                     f"{row.get('observed_change_percent','未知')} | {row['sample_metric']} {previous} / {current} "
                     f"（各期下限 {row['minimum_per_period']}） | {row['reason'] or row['status']} |")
    lines += ['', '阈值按原始合计和未舍入的比值判断。命中后复核口径、样本及本期业务变化，再由投手决定下一步。']
    return lines


def qualified_lead_markdown(review):
    if not review or 'qualified_lead_evidence_sha256' not in review:
        return []
    lines = ['', '## 有效询盘复核', '', f"证据摘要 `{review['qualified_lead_evidence_sha256']}`。结果 `{review['state']}`。"]
    windows = review.get('qualified_lead_periods')
    if windows is None:
        return lines
    lines += ['', f"币种 `{review['currency']}`。按业务档案至少观察 {review['declared_observation_days'] or '未指定'} 天，"
              f"各期至少 {review['declared_minimum_results'] or '未指定'} 条有效询盘。",
              '', '| 时段 | 花费 | Meta 网站线索 | Meta 网站 CPL | 客户核定有效询盘 | 有效询盘成本 | 汇总证据 |',
              '|---|---:|---:|---:|---:|---:|---|']
    for label, window in windows.items():
        cost = window['qualified_lead_cost']
        values = (window['spend'],window['website_leads'],window['website_lead_cpl'],window['qualified_count'],
                  cost['value'] if cost['value'] is not None else cost['state'],window['evidence_ref'])
        period_label = '前期' if label=='previous' else '后期'
        lines.append('| '+' | '.join([period_label,*('未知' if value is None else str(value) for value in values)])+' |')
    lines += ['', '有效询盘使用客户提供的账户与日期汇总。计数定义、来源和归属由客户留证。'
              '样本不足或口径待核时，先复查再决定投放动作。']
    return lines


def qualified_lead_proposal_markdown(basis):
    if basis is None:
        return []
    lines = ['', '## 下一轮提案依据', '', f"建议状态 `{basis['decision']}`。{basis['next_check']}"]
    if basis['currency'] is None:
        return lines
    currency = basis['currency']
    lines += ['', f"目标成本 `{basis['target_cost_per_result'] or '未填'}`，核对结果 `{basis['target_check']}`。",
              f"测试总上限 `{basis['maximum_test_spend'] or '未填'}`，"
              f"现有日投放保护额 `{basis['protected_daily_spend'] or '未填'}`，币种 {currency}。"
              f"样本估算状态 `{basis['capacity_check']}`。"]
    inputs = basis['current_cost_inputs']
    if inputs is not None:
        spend = inputs['spend'] if inputs['spend'] is not None else '未知'
        lines.append(f"后期观察为花费 {spend} {currency}、有效询盘 {inputs['qualified_count']} 条。")
    capacity = basis['estimated_test_capacity']
    if capacity is not None:
        lines += ['', f"按后期有效询盘成本与目标成本中较高值 {capacity['cost_per_result']} {currency} 估算，"
                  f"在测试总上限内约可取得 {capacity['outcomes_at_test_cap']} 条，"
                  f"达到 {basis['minimum_results']} 条约需 {capacity['spend_for_declared_minimum']} {currency}。",
                  '判断使用原始花费与计数。下一轮实际成本、样本成熟和投放决定仍需核对。']
    return lines


def markdown(report):
    lines = ['# 广告分期对比','',f"结果 {report['state']}，证据模式 {report['mode'] or '混合'}。",'']
    if 'workspace_identity_sha256' in report:
        lines += ['前后两期已核对为同一指定工作区，JSON 与回执保存工作区摘要。','']
    inputs = report.get('input_runs')
    if inputs is not None:
        for label,title in (('previous','前期'),('current','后期')):
            entry = inputs[label]
            lines.append(f"{title}归档 `{entry['run_id']}`，采集结果 `{entry['status']}`。"
                         f"回执摘要 `{entry['receipt_sha256']}`。")
        lines.append('')
    for label, period in report['periods'].items():
        context = period.get('context')
        if context:
            lines.append(f"- {label}　{context['date_start']} 至 {context['date_stop']}，账户 {context['account_id']}，"
                         f"{context['currency']}，{context['timezone']}，{context['attribution']}。覆盖 {period['coverage']}。")
        else:
            lines.append(f"- {label}　{period['reason']}。")
        if 'account_cycle' in period:
            cycle = period['account_cycle']
            lines.append(f"  采集轮次 {cycle['cycle']}，分页记录 `{cycle['pagination']}`。")
        if 'missing_dates' in period:
            lines.append('  无返回行的日期为 ' + '、'.join(period['missing_dates']) + '。')
    if report['state']!='COMPARED':
        return (lines+['',f"待复核条件 {report['reason']}。"]+qualified_lead_markdown(report['sample_review'])
                +qualified_lead_proposal_markdown(report.get('qualified_lead_proposal_basis'))+alert_markdown(report))
    previous,current = (report['periods'][label]['calendar_days_after_period_end_at_observation']
                        for label in ('previous','current'))
    lines += ['',f"采集时距各期结束相隔的账户当地日历日数，前期 {previous} 天，后期 {current} 天。"]
    lines += ['', '| 指标 | 前期 | 后期 | 差值 | 相对变化% |', '|---|---:|---:|---:|---:|']
    for name, change in report['account_metrics'].items():
        values = [change[key] if change[key] is not None else '未知' for key in ('previous','current','delta','change_percent')]
        lines.append('| '+' | '.join((name,*values))+' |')
    ad = report['ad_composition']
    lines += ['', f"两期均观察到 {ad['shared_count']} 条广告，只在前期观察到 {len(ad['only_previous'])} 条，"
              f"只在后期观察到 {len(ad['only_current'])} 条。单广告对比见 JSON。",
              f"两期星期分布 {'相同' if report['same_weekday_mix'] else '不同'}。"
              f"业务样本检查 {report['sample_review']['state']}。",
              '未返回的值显示未知。前期实际为零时仅显示绝对差值。CTR 差值用百分点。先用原始合计计算比率变化，再做显示舍入。',
              '未观察到广告的一期仍可能有投放。按本次报表口径解释，平台数据刷新与转化回补时间未知。']
    if report['periods']['current']['context']['attribution']==WEBSITE_ATTRIBUTION:
        lines.append('网站转化按固定请求和 API 返回分桶观察。实际归因设置和动作归日仍待核对。')
    known_changes = [(ad_id, row['metrics']['spend']['delta']) for ad_id,row in report['shared_ad_metrics'].items()
                     if row['metrics']['spend']['delta'] is not None]
    if known_changes:
        order = sorted(known_changes,key=lambda item: Decimal(item[1]).copy_abs(),reverse=True)
        lines += ['', '## 两期都观察到的广告中花费变化较大的对象', '',
                  '| 广告 | 后期减前期的花费 |', '|---|---:|']
        lines += [f"| {ad_id} | {delta} |" for ad_id,delta in order[:10]]
    incomplete = [(ad_id,row) for ad_id,row in report['shared_ad_metrics'].items()
                  if row['coverage']=='PARTIAL' and 'period_coverage' in row]
    if incomplete:
        lines += ['', '## 两期都观察到的广告中的日期缺口', '',
                  f"共 {len(incomplete)} 条。单期覆盖完整且指标字段齐全时保留该期结果。全部日期见 JSON。", '',
                  '| 广告 | 前期缺行日期 | 后期缺行日期 | 前期花费 | 后期花费 |', '|---|---|---|---:|---:|']
        for ad_id, row in incomplete[:10]:
            periods = ('previous','current')
            missing = ['、'.join(row['period_coverage'][period]['missing_dates']) or '齐全' for period in periods]
            spend = [row['metrics']['spend'][period] or '未知' for period in periods]
            lines.append('| '+' | '.join((ad_id,*missing,*spend))+' |')
    return (lines+qualified_lead_markdown(report['sample_review'])
            +qualified_lead_proposal_markdown(report.get('qualified_lead_proposal_basis'))+alert_markdown(report))
