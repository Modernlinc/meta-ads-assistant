"""Bounded ads/live review derived only from validated monitor observations.

Returned account fields establish the reporting context, not OAuth authority.
A direct creative/video match establishes object identity at observation time,
not historical delivery, paid viewers or causal lift. No mutation proposals are
automatically approved or executed by this report.
"""
from collections import Counter
from datetime import date
from zoneinfo import ZoneInfoNotFoundError

from .analysis import WEBSITE_ATTRIBUTION, aggregate_ads, aggregate_hierarchy
from .journal import digest
from .monitor import (ACCOUNT_CYCLE_PAGINATION_UNVERIFIED, AD_INVENTORY_CHANNELS, AD_OBJECT_SOURCES,
                      AD_PLACEMENT_CHANNELS, account_cycle_pagination_unverified, complete_row)
from .placement_review import placement_delivery, placement_markdown
from .read_adapter import HIERARCHY_INSIGHTS_OPERATIONS, INSIGHTS_OPERATIONS, PLACEMENT_DIMENSIONS, WEBSITE_INSIGHTS_OPERATIONS
from .timezones import load_zone
from .validation import InputError


ACCOUNT_DAILY_ROWS_INCOMPLETE = 'ACCOUNT_DAILY_ROWS_INCOMPLETE'


def evidence(source):
    return {'plan_sha256': digest(source['plan']), 'generation': source['generation'],
            'observation_sha256': digest(source['rows']), 'observed_at': source['observed_at'],
            'result': source['result'], 'valid_pages': source['pages'],
            'last_successful_sync': source['last_successful_sync'], 'last_error': source['last_error']}


def parent_links(channel, row, campaigns, adsets):
    campaign = row.get('campaign_id')
    if channel == 'adsets':
        if campaign is None: return 'PARENT_ID_MISSING'
        return 'OBSERVED_PARENT_LINKS' if campaign in campaigns else 'PARENT_NOT_OBSERVED'
    adset_id = row.get('adset_id')
    if campaign is None or adset_id is None: return 'PARENT_ID_MISSING'
    adset = adsets.get(adset_id)
    if adset is None: return 'PARENT_NOT_OBSERVED'
    adset_campaign = adset.get('campaign_id')
    if adset_campaign is None: return 'PARENT_ID_MISSING'
    if adset_campaign != campaign: return 'PARENT_CONFLICT'
    return 'OBSERVED_PARENT_LINKS' if campaign in campaigns else 'PARENT_NOT_OBSERVED'


def inventory_analysis(sources):
    account = sources['account']
    account_id = account['plan']['account_id']
    report = dict(account_id=account_id, account=evidence(account), account_status=None,
                  coverage='ACCOUNT_SCOPE_UNVERIFIED', levels={})
    if (account['result'] != 'COMPLETE' or len(account['rows']) != 1
            or any(source['plan']['account_id'] != account_id or any(row['account_id'] != account_id for row in source['rows'])
                   for source in sources.values())):
        return report
    report['account_status'] = account['rows'][0].get('account_status')
    campaigns = {row['id'] for row in sources['campaigns']['rows']}
    adsets = {row['id']: row for row in sources['adsets']['rows']}
    for channel in AD_OBJECT_SOURCES:
        source = sources[channel]
        rows = [dict(row) for row in sorted(source['rows'],key=lambda row: row['id'])]
        if channel != 'campaigns':
            for row in rows:
                row['parent_links'] = parent_links(channel,row,campaigns,adsets)
        report['levels'][channel] = dict(
            rows=rows, observed_rows=len(rows) if source['pages'] else None, evidence=evidence(source),
            configured_status_counts=dict(Counter(row.get('status','UNKNOWN') for row in rows)),
            effective_status_counts=dict(Counter(row.get('effective_status','UNKNOWN') for row in rows)))
    if all(sources[channel]['result'] in ('COMPLETE','EMPTY') for channel in AD_OBJECT_SOURCES):
        report['coverage'] = 'BOUNDED_COMPLETE'
    elif any(sources[channel]['pages'] for channel in AD_OBJECT_SOURCES):
        report['coverage'] = 'PARTIAL'
    else:
        report['coverage'] = 'NOT_OBSERVED'
    return report


def placement_analysis(sources, *, pagination_unverified=False):
    account = complete_row(sources, 'account')
    source = sources['placements']
    plan = source['plan']
    currency, timezone = account.get('currency'), account.get('timezone_name')
    report = dict(account_id=plan['account_id'], date_range=[plan['since'],plan['until']],
                  level='account', time_increment=1, breakdowns=list(PLACEMENT_DIMENSIONS),
                  currency=currency, timezone=timezone, totals=None, placements=[],
                  coverage='NOT_OBSERVED', reason=source['last_error'],
                  evidence={'account':evidence(sources['account']), 'insights':evidence(source)},
                  website_conversions='NOT_REQUESTED', data_as_of=None, freshness='UNKNOWN_SOURCE_UPDATE_TIME')
    if pagination_unverified:
        return dict(report,coverage='UNVERIFIED',reason=ACCOUNT_CYCLE_PAGINATION_UNVERIFIED)
    if account.get('account_id') != plan['account_id']:
        return dict(report,reason='CURRENT_COMPLETE_ACCOUNT_RESPONSE_REQUIRED')
    if currency is None or timezone is None:
        return dict(report,reason='ACCOUNT_CURRENCY_AND_TIMEZONE_REQUIRED')
    try:
        load_zone(timezone)
    except (ValueError,ZoneInfoNotFoundError):
        return dict(report,reason='IANA_TIMEZONE_REQUIRED')
    rows = source['rows']
    if any(row.get('account_currency') not in (None,currency) for row in rows):
        return dict(report,reason='REPORT_INSIGHTS_CURRENCY_MISMATCH')
    if not source['pages']:
        return dict(report,reason=source['last_error'] or 'PLACEMENTS_NOT_OBSERVED')
    complete = source['result'] in ('COMPLETE','EMPTY')
    coverage = 'BOUNDED_COMPLETE' if complete else 'PARTIAL'
    totals, placements = placement_delivery(rows,complete)
    return dict(report,coverage=coverage,totals=totals,placements=placements)


def daily_date_coverage(rows, context):
    start, stop = (date.fromisoformat(context[key]).toordinal() for key in ('date_start','date_stop'))
    observed = {row['date_start'] for row in rows}
    requested = [date.fromordinal(day).isoformat() for day in range(start,stop+1)]
    missing = [day for day in requested if day not in observed]
    state = 'NO_ROWS' if not observed else 'MISSING_DAILY_ROWS' if missing else 'ALL_DATES_RETURNED'
    return dict(state=state,declared_days=len(requested),observed_days=len(observed),missing_dates=missing)


def ads_analysis(sources, *, pagination_unverified=False):
    ads = sources.get('ads'); account = sources.get('account')
    def blocked(reason): return {'state': 'NOT_COMPUTED', 'reason': reason, 'analysis': None}
    if ads is None or ads['plan']['operation'] not in INSIGHTS_OPERATIONS: return blocked('INSIGHTS_SOURCE_REQUIRED')
    if pagination_unverified: return blocked(ACCOUNT_CYCLE_PAGINATION_UNVERIFIED)
    if not ads['pages']:
        return dict(blocked(ads['last_error'] or 'INSIGHTS_NOT_OBSERVED'),
                    evidence={'insights': evidence(ads)})
    if account is None or account['result'] != 'COMPLETE' or len(account['rows']) != 1:
        return blocked('CURRENT_COMPLETE_ACCOUNT_RESPONSE_REQUIRED')
    plan = ads['plan']; row = account['rows'][0]
    if account['plan']['account_id'] != plan['account_id'] or row['account_id'] != plan['account_id']:
        return blocked('ACCOUNT_SCOPE_MISMATCH')
    if row.get('currency') is None or row.get('timezone_name') is None:
        return blocked('ACCOUNT_CURRENCY_AND_TIMEZONE_REQUIRED')
    context = dict(account_id=plan['account_id'], currency=row['currency'], timezone=row['timezone_name'],
                   date_start=plan['since'], date_stop=plan['until'], level='ad',
                   attribution=WEBSITE_ATTRIBUTION if plan['operation'] in WEBSITE_INSIGHTS_OPERATIONS else 'UNIFIED_ADSET',
                   action_report_time='impression', breakdowns=[], api_version=plan['version'], time_increment=1)
    try:
        coverage = 'COMPLETE' if ads['result'] in ('COMPLETE', 'EMPTY') else 'PARTIAL'
        calculated = aggregate_ads(ads['rows'], context, context, coverage)
        if plan['operation'] in HIERARCHY_INSIGHTS_OPERATIONS:
            calculated['hierarchy'] = aggregate_hierarchy(ads['rows'], context, coverage)
    except InputError as exc:
        if str(exc)=='REPORT_INSIGHTS_CURRENCY_MISMATCH':
            return blocked('REPORT_INSIGHTS_CURRENCY_MISMATCH')
        return blocked('ACCOUNT_CONTEXT_OR_DAILY_ROWS_INVALID')
    return {'state': 'COMPUTED', 'context': context, 'analysis': calculated,
            'daily_date_coverage': daily_date_coverage(ads['rows'],context),
            'context_basis': 'RETURNED_ACCOUNT_FIELDS_AND_FIXED_INSIGHTS_QUERY_PLAN',
            'action_reporting': {'requested_action_report_time':context['action_report_time'],
                                 'requested_attribution':context['attribution'],
                                 'effective_action_report_time':None,'effective_attribution_setting':None,
                                 'evidence':'REQUESTED_SETTINGS_ONLY_EFFECTIVE_PROVIDER_DETAILS_UNVERIFIED'},
            'evidence': {'account': evidence(account), 'insights': evidence(ads)},
            'data_as_of': None, 'freshness': 'UNKNOWN_SOURCE_UPDATE_TIME',
            'query_scope': 'ACCOUNT_DATE_RANGE_NOT_EXACT_BROADCAST_WINDOW',
            'context_authenticated_independently': False}


def object_links(sources, *, pagination_unverified=False):
    result = dict(state='NOT_ESTABLISHED', reason='LIVE_AND_AD_LINK_SOURCES_REQUIRED', matches=[],
                  coverage='NOT_RUN', nonmatching_observed_ads=0, unresolved_observed_ads=0,
                  causal_claim_allowed=False, historical_creative_at_delivery_verified=False,
                  paid_viewer_count=None, incremental_lift=None)
    live = sources.get('live'); links = sources.get('ad_links'); ads = sources.get('ads')
    if live is None or links is None or ads is None: return result
    if pagination_unverified:
        return dict(result,reason=ACCOUNT_CYCLE_PAGINATION_UNVERIFIED,coverage='UNVERIFIED',
                    nonmatching_observed_ads=None,unresolved_observed_ads=None)
    if 'page' in sources:
        page = complete_row(sources,'page')
        if not page or page['id'] != live['plan']['page_id']:
            result['reason'] = 'CURRENT_PAGE_READ_REQUIRED'; return result
    if live['result'] != 'COMPLETE' or len(live['rows']) != 1 or not live['rows'][0].get('video', {}).get('id'):
        result['reason'] = 'CURRENT_LIVE_VIDEO_RELATION_REQUIRED'; return result
    if links['plan']['account_id'] != ads['plan']['account_id']:
        result['reason'] = 'ACCOUNT_SCOPE_MISMATCH'; return result
    if links['result'] not in ('COMPLETE', 'EMPTY', 'GAP'):
        result['reason'] = 'AD_LINK_READ_NOT_USABLE'; return result
    relation = live['rows'][0]; video_id = relation['video']['id']
    result['coverage'] = 'COMPLETE' if links['result'] in ('COMPLETE', 'EMPTY') else 'PARTIAL'
    for ad in links['rows']:
        creative = ad.get('creative', {})
        if not creative.get('video_id'):
            result['unresolved_observed_ads'] += 1
        elif creative['video_id'] != video_id:
            result['nonmatching_observed_ads'] += 1
        else:
            result['matches'].append(dict(account_id=ad['account_id'], ad_id=ad['id'], creative_id=creative['id'],
                                          page_id=relation['from']['id'], live_id=relation['id'], video_id=video_id))
    result['matches'].sort(key=lambda item: item['ad_id'])
    result['evidence'] = {'ad_links': evidence(links), 'live': evidence(live)}
    if 'page' in sources:
        result['evidence']['page'] = evidence(sources['page'])
    result['state'] = 'RETURNED_DIRECT_VIDEO_MATCHES_OBSERVED' if result['matches'] else 'NOT_ESTABLISHED'
    result['reason'] = 'OBJECT_IDENTITY_AT_OBSERVATION_ONLY' if result['matches'] else 'NO_MATCH_IN_OBSERVED_DIRECT_VIDEO_FIELDS'
    return result


def build_review(status):
    """Build a review from validated local snapshots or run archives."""
    sources = status['sources']
    pagination_unverified = account_cycle_pagination_unverified(status)
    if set(sources)==AD_PLACEMENT_CHANNELS:
        return dict(schema='bounded-ads-placement-review-v1',mode=status['mode'],
                    placements=placement_analysis(sources,pagination_unverified=pagination_unverified),
                    causal_claim_allowed=False,live_action_authorized=False,
                    real_connection_verified=False,recommendation_state='REVIEW_ONLY_NO_BUDGET_OR_WRITE_ACTION')
    ads = ads_analysis(sources,pagination_unverified=pagination_unverified)
    links = object_links(sources,pagination_unverified=pagination_unverified)
    inventory = inventory_analysis(sources) if set(sources)==AD_INVENTORY_CHANNELS else None
    linked = {'state': 'NOT_COMPUTED', 'reason': 'COMPUTED_ADS_AND_OBJECT_MATCH_REQUIRED', 'analysis': None}
    if links['reason']==ACCOUNT_CYCLE_PAGINATION_UNVERIFIED:
        linked['reason'] = links['reason']
    if ads['state'] == 'COMPUTED' and links['matches']:
        ids = {row['ad_id'] for row in links['matches']}
        rows = [row for row in sources['ads']['rows'] if row['ad_id'] in ids]
        coverage = 'COMPLETE' if links['coverage'] == 'COMPLETE' and ads['analysis']['coverage'] == 'COMPLETE' else 'PARTIAL'
        linked = {'state': 'COMPUTED', 'ad_ids': sorted(ids),
                  'analysis': aggregate_ads(rows, ads['context'], ads['context'], coverage),
                  'scope': 'DATE_RANGE_METRICS_OF_ADS_WITH_CURRENT_DIRECT_VIDEO_MATCH_NOT_LIVE_ATTRIBUTION',
                  'historical_creative_at_delivery_verified': False}
    issues = []
    for channel, source in sources.items():
        if source['result'] in ('ERROR', 'GAP', 'NOT_RUN'):
            issues.append({'code': 'SOURCE_' + source['result'], 'source': channel,
                           'error_code': source['last_error'], 'action': '核对该来源范围和错误；恢复前复查，不自动扩大授权或重试。'})
    if inventory is not None:
        return dict(schema='bounded-ads-live-review-v1',mode=status['mode'],ads=ads,object_relationship=links,
                    matched_ad_date_range_analysis=linked,inventory=inventory,next_checks=issues,
                    causal_claim_allowed=False,live_action_authorized=False,real_connection_verified=False,
                    recommendation_state='REVIEW_ONLY_NO_BUDGET_OR_WRITE_ACTION',
                    limits=['The observed object status does not establish spend or delivered impressions.',
                            'Bounded query exhaustion does not establish historical inventory or visibility.'])
    if ads['state'] != 'COMPUTED':
        action = ('按原账户和日期重新取得每轮完整分页证据。'
                  if ads['reason']==ACCOUNT_CYCLE_PAGINATION_UNVERIFIED
                  else '先取得同账户的币种、时区和固定日期口径Insights证据，再计算广告指标。')
        issues.append({'code': ads['reason'], 'source': 'ads', 'error_code': None,
                       'action': action})
    elif ads['context']['attribution']==WEBSITE_ATTRIBUTION:
        metrics = ads['analysis']['metrics']
        for name in ('website_purchase_cpa','website_purchase_roas','website_lead_cpl'):
            if metrics[name]['status']!='VALUE':
                issues.append({'code':metrics[name]['reason'],'source':name,'error_code':None,
                               'action':'核对查询覆盖、具名网站事件、7 天点击分桶及账户币种。'})
    if ads['state'] == 'COMPUTED' and 'hierarchy' in ads['analysis']:
        hierarchy = ads['analysis']['hierarchy']
        if any(hierarchy[key]['missing_parent_rows'] or hierarchy[key]['conflicting_parent_rows']
               for key in ('campaigns', 'adsets')):
            issues.append({'code':'INSIGHTS_PARENT_EVIDENCE_INCOMPLETE','source':'ads','error_code':None,
                           'action':'用同账户同日期的报表核对缺失或冲突的父级 ID。账户原始数据仍可独立复核。'})
    if ads['state']=='COMPUTED' and ads['daily_date_coverage']['state']=='MISSING_DAILY_ROWS':
        issues.append({'code':ACCOUNT_DAILY_ROWS_INCOMPLETE,'source':'ads','error_code':None,
                       'action':'用同账户、同日期及同口径的逐日报表核对无返回行的日期，再比较整段表现。'})
    if links['state'] == 'NOT_ESTABLISHED' and links['reason'] != ACCOUNT_CYCLE_PAGINATION_UNVERIFIED:
        issues.append({'code': 'AD_LIVE_LINK_UNPROVEN', 'source': 'ad_links', 'error_code': None,
                       'action': '缺直接Video关系时保留未证实；核查实际创意对象，不凭时间或名称推断。'})
    issues.append({'code': 'CAUSAL_ATTRIBUTION_UNPROVEN', 'source': 'review', 'error_code': None,
                   'action': '对象相同不能证明广告带来观看或增量；保持广告与直播指标分开，另核转化及归因证据。'})
    return {'schema': 'bounded-ads-live-review-v1', 'mode': status['mode'],
            'ads': ads, 'object_relationship': links, 'matched_ad_date_range_analysis': linked,
            'next_checks': issues, 'causal_claim_allowed': False, 'live_action_authorized': False,
            'real_connection_verified': False, 'recommendation_state': 'REVIEW_ONLY_NO_BUDGET_OR_WRITE_ACTION',
            'limits': ['Current creative identity is not proof of which creative was delivered historically.',
                       'Daily ad results cannot be treated as an exact live session window.',
                       'Website conversion metrics use returned 7-day-click buckets only. Meta event counts are not CRM qualified outcomes or audited sales.',
                       'Unique/social/view buckets are not summed or divided into ad clicks.',
                       'Local checksums detect accidental changes, not authenticity against malicious edits.']}


def hierarchy_markdown(hierarchy, website):
    lines = ['', '### 广告系列与广告组', '',
             '按本次 Insights 的逐日广告行、同一日期和归因口径汇总。父级缺失或冲突的行保留在账户计算中。']
    fields = [('spend', '花费'), ('impressions', '展示'), ('clicks', '点击')]
    if website:
        fields += [('website_purchase_cpa', '网站购买 CPA'), ('website_purchase_roas', '网站购买 ROAS'),
                   ('website_lead_cpl', '网站线索 CPL')]

    def display(metric):
        if metric['value'] is not None:
            return metric['value']
        if metric.get('known_subtotal') is not None:
            return metric['known_subtotal'] + '（已知小计）'
        return '未知'

    for key, label in (('campaigns', 'Campaign'), ('adsets', 'Ad Set')):
        level = hierarchy[key]
        lines += ['', f"{label} 覆盖 {level['coverage']}，已归属 {level['assigned_rows']} 行，"
                      f"缺字段 {level['missing_parent_rows']} 行，父级冲突 {level['conflicting_parent_rows']} 行。"]
        groups = level['groups']
        if not groups:
            lines.append('本批没有可归属的行。')
            continue
        columns = [label, 'Campaign ID'] if key == 'adsets' else [label]
        columns += ['广告日行数', *(title for _, title in fields)]
        lines += ['', '| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |']
        for group in groups[:25]:
            ids = [group['id'], group['campaign_id']] if key == 'adsets' else [group['id']]
            values = [*ids, str(group['row_count']), *(display(group['metrics'][metric]) for metric, _ in fields)]
            lines.append('| ' + ' | '.join(values) + ' |')
        if len(groups) > 25:
            lines.append(f'本页按 ID 展示 25 组，本批 {len(groups)} 组数据见 REPORT.json。')
    return lines


def inventory_markdown(review):
    result = review['inventory']
    lines = ['', '## 广告对象盘点', '', f"账户 `{result['account_id']}`。覆盖 `{result['coverage']}`。",
             '配置状态与 Meta 返回的有效状态按各自字段呈现。实际展示与花费请另查同期 Insights。',
             '覆盖仅反映本次账户查询。未观察到的父级保留待核，归档、可见权限和历史对象另行确认。']
    if result['account_status'] is not None:
        lines.append(f"Meta 返回的账户状态代码为 `{result['account_status']}`。")
    labels = {'campaigns': '广告系列', 'adsets': '广告组', 'ad_inventory': '广告'}
    for channel, level in result['levels'].items():
        source = level['evidence']
        count = level['observed_rows'] if level['observed_rows'] is not None else '未知'
        lines += ['', f"### {labels[channel]}", '',
                  f"`{source['result']}`，有效页 {source['valid_pages']}，观察到 {count} 个对象。"
                  f"观察时间 {source['observed_at'] or '未知'}。"]
        if level['rows']:
            lines += ['', '| ID | 配置状态 | 有效状态 | 广告系列 ID | 广告组 ID | 父级核对 |',
                      '|---|---|---|---|---|---|']
            for row in level['rows']:
                values = [row.get(field) or '—' for field in ('id','status','effective_status','campaign_id','adset_id','parent_links')]
                lines.append('| ' + ' | '.join(values) + ' |')
    if review['next_checks']:
        lines += ['', '### 待核数据源', '']
        for issue in review['next_checks']:
            lines.append(f"- {issue['source']} / {issue['code']}：{issue['action']}")
    return lines


def markdown(review):
    if 'inventory' in review:
        return inventory_markdown(review)
    if 'placements' in review:
        return placement_markdown(review)
    ads = review['ads']; links = review['object_relationship']
    lines = ['', '## 广告与直播复盘', '', f"证据模式：{review['mode']}；广告分析状态：{ads['state']}。"]
    if ads['state'] != 'COMPUTED': lines.append('暂不计算：' + ads['reason'] + '。')
    else:
        c = ads['context']
        daily = ads['daily_date_coverage']
        lines += [f"账户 {c['account_id']}；币种 {c['currency']}；时区 {c['timezone']}；日期 {c['date_start']} 至 {c['date_stop']}。",
                  f"广告层级，逐日、无行拆分。请求归因 {c['attribution']}，请求动作归日 {c['action_report_time']}。"
                  '实际生效的动作归日、归因设置和平台数据刷新时间仍待取证。',
                  f"计划 {daily['declared_days']} 天，有返回行 {daily['observed_days']} 天。"]
        scope = '账户查询'
        if daily['missing_dates']:
            lines.append('无返回行的日期为 ' + '、'.join(daily['missing_dates']) + '。这些日期保持未知。')
        if daily['state']=='MISSING_DAILY_ROWS':
            scope = '仅已返回广告行'
        lines += ['', '| 范围 | 指标 | 状态 | 值 | 已知小计 |', '|---|---|---|---|---|']
        groups = [(scope, ads['analysis'])]
        linked = review['matched_ad_date_range_analysis']
        if linked['state'] == 'COMPUTED': groups.append(('当前关联广告/查询日期范围', linked['analysis']))
        for label, analysis in groups:
            for metric, value in analysis['metrics'].items():
                subtotal = (value['known_subtotal'] or '未知') if 'known_subtotal' in value else '不适用'
                lines.append('| ' + ' | '.join((label, metric, value['status'], value['value'] if value['value'] is not None else '未知',
                                                subtotal)) + ' |')
        if c['attribution']==WEBSITE_ATTRIBUTION:
            lines += ['', '网站购买 CPA、网站购买 ROAS 和网站线索 CPL 使用明确返回的 7 天点击分桶。业务合格线索、订单净额和利润需另外核验。']
        if 'hierarchy' in ads['analysis']:
            lines += hierarchy_markdown(ads['analysis']['hierarchy'], c['attribution'] == WEBSITE_ATTRIBUTION)
    lines += ['', f"对象关联：{links['state']}；覆盖：{links['coverage']}。"]
    if links['reason']==ACCOUNT_CYCLE_PAGINATION_UNVERIFIED:
        lines.append('旧多轮记录的分页依据待核，暂缓确认广告创意与直播的关联。')
    else:
        lines.append(f"当前观察中，直接Video不同的广告 {links['nonmatching_observed_ads']} 条；未返回直接Video的广告 {links['unresolved_observed_ads']} 条。这些数量不是全部广告的结论。")
    for row in links['matches']:
        lines.append(f"- 广告 {row['ad_id']} → 创意 {row['creative_id']} → Video {row['video_id']} ← Live {row['live_id']} / Page {row['page_id']}。")
    lines += ['', '这是观察时的对象关联，不能证明历史投放创意、直播期间花费、付费观看数或广告增量效果。日广告数据与lifetime直播指标不可直接求转化率。',
              '本报告供人工复核。下一步核查：', '']
    for issue in review['next_checks']: lines.append(f"- {issue['source']} / {issue['code']}：{issue['action']}")
    return lines
