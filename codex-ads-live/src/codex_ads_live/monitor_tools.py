"""Local monitor inspection, a synthetic demo and bounded observation reports."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from .journal import Journal, canonical
from .live_timeline import timeline_data, timeline_markdown
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS, PostMetricReadPlan
from .monitor import ACCOUNT_CYCLE_PAGINATION_UNVERIFIED, CHANNELS, PAGE_RUN_FAILURES, ReadMonitor
from .read_adapter import (CLICK_WINDOW, CONVERSION_HIERARCHY_INSIGHTS, RECENT_LIVE_COMMENTS, WEBSITE_LEAD,
                           WEBSITE_PURCHASE, PageLiveReadPlan, ReadPlan, MockTransport)
from .review import ACCOUNT_DAILY_ROWS_INCOMPLETE, build_review, markdown as review_markdown
from .validation import aware_time, fail, strict_json


FOREGROUND_OWNER_RELEASED = 'FOREGROUND_OWNER_RELEASED'
FOREGROUND_OWNER_UNVERIFIED = 'FOREGROUND_OWNER_UNVERIFIED'


def new_directory(directory):
    root = checked_path(directory, link_error='MONITOR_OUTPUT_SYMLINK', volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if root.exists() or root.is_symlink(): fail('MONITOR_OUTPUT_REQUIRES_NEW_DIRECTORY')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    return root


def open_monitor(directory, *, existing_only=False):
    root = checked_path(directory, link_error='MONITOR_STATE_SYMLINK', volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    marker = root / 'operator-state.json'
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1000: fail('MONITOR_STATE_NOT_FOUND')
    data = strict_json(marker.read_text(encoding='utf-8'))
    if type(data) is not dict or data.get('mode') not in ('MOCK', 'READ_ONLY'): fail('MONITOR_MARKER')
    return ReadMonitor(root, mode=data['mode'],existing_only=existing_only)


def foreground_status(monitor, *, include_comment_resume=False):
    """Observe the persisted monitor and the local foreground owner without repair."""
    observe_owner = monitor.foreground_owner_probe()
    if observe_owner is None:
        return monitor.status()

    try:
        status = monitor.status(existing_only=True, include_saved_phase=True, foreground_probe=observe_owner,
                                include_comment_resume=include_comment_resume)
    except FileNotFoundError:
        if monitor.journal.snapshots():
            raise
        status = dict(phase='NOT_CONFIGURED', mode=monitor.mode, real_connection_verified=False,
                      owner_lock_observation=observe_owner())
    lock_observation = status['owner_lock_observation']
    status['monitor_process_verified'] = lock_observation=='HELD' and status['phase']=='RUNNING'
    if status['phase']=='RUNNING' and lock_observation!='HELD':
        phase, error = (('INTERRUPTED',FOREGROUND_OWNER_RELEASED) if lock_observation=='RELEASED'
                        else ('UNVERIFIED',FOREGROUND_OWNER_UNVERIFIED))
        status.update(phase=phase,health=phase,last_error=error)
    return status


def recent_comment_sample(source):
    observed = sorted((row['created_time'] for row in source['rows'] if 'created_time' in row),key=aware_time)
    coverage = ('BOUNDED_COMPLETE' if source['result'] in ('COMPLETE','EMPTY') else
                'BOUNDED_PARTIAL' if source['pages'] else 'UNOBSERVED')
    sample = dict(scope='CURRENT_BOUNDED_TOP_LEVEL_SAMPLE',state=source['result'],coverage=coverage,
                  requested_order='reverse_chronological',valid_pages=source['pages'],
                  observed_comment_count=len(source['rows']) if source['pages'] else None,
                  rows_with_creation_time=len(observed), oldest_created_at=observed[0] if observed else None,
                  newest_created_at=observed[-1] if observed else None, observed_at=source['observed_at'])
    since = source['plan'].get('since')
    if since is not None:
        sample.update(requested_since=since,interruption_coverage='UNKNOWN')
    return sample


def page_issue_summary(failure):
    issue = failure['issue']
    return dict(error_code=failure['error_code'],credential=issue['credential'],stage=issue['stage'],
                next_action=issue['next_action'],evidence_state=failure['evidence_state'],
                workspace_health_update=failure['workspace_health_update'])


def page_issue_markdown(observations):
    lines = []
    for kind, label in (('authentication','认证问题'),('permission','权限问题')):
        failure = observations.get('page_'+kind)
        if failure is not None:
            lines += ['', f"{label} `{failure['error_code']}`，涉及 `{failure['credential']}`，阶段 `{failure['stage']}`。",
                      f"接续动作 `{failure['next_action']}`。本机回执状态 `{failure['evidence_state']}`。"]
    return lines


def live_metrics_section(sources):
    """Present the metric fields that are also saved in the per-source observations."""
    metrics = {}
    for channel, name in METRIC_CHANNELS.items():
        source = sources.get(channel)
        metrics[name] = {
            'channel': channel,
            'read_scope': ('PAGE_POST_INSIGHTS' if source['plan']['operation']=='post_insights' else 'LEGACY_SIMULATED_VIDEO_ROUTE') if source else None,
            'object_id': source['plan']['object_id'] if source else None,
            'video_id': source['plan'].get('video_id') if source else None,
            'collection_state': source['result'] if source else 'NOT_CONFIGURED',
            'last_error': source['last_error'] if source else None,
            'last_successful_sync': source['last_successful_sync'] if source else None,
            'observations': source['rows'] if source else [],
        }
    return {
        'state': 'CONFIGURED_OBSERVATIONS' if METRIC_CHANNELS.keys() & sources.keys() else 'NOT_COLLECTED_BY_THIS_MONITOR',
        'metrics': metrics, 'missing_is_zero': False, 'buckets_summed': False,
        'refresh_time_basis': 'UNKNOWN_NOT_INFERRED_FROM_POLL_OR_BUCKET_END',
        'success_time_semantics': 'VALID_RESPONSE_PARSED_NOT_PROOF_OF_NUMERIC_VALUE_OR_FRESHNESS',
        'real_live_validated': False,
    }


def report_data(status):
    if status['phase'] == 'NOT_CONFIGURED': fail('MONITOR_NOT_CONFIGURED')
    observations = {}
    for channel in CHANNELS:
        source = status['sources'].get(channel)
        if source is None:
            observations[channel] = {'state': 'NOT_CONFIGURED', 'last_successful_sync': None}
            continue
        observations[channel] = {
            'state': source['result'], 'collector_phase': source['phase'], 'query': source['plan'],
            'generation': source['generation'], 'valid_pages': source['pages'],
            'current_query_rows': len(source['rows']), 'observed_at': source['observed_at'],
            'data_as_of': None, 'freshness': 'UNKNOWN_SOURCE_UPDATE_TIME',
            'last_successful_sync': source['last_successful_sync'], 'last_error': source['last_error'],
            'coverage': 'CURRENT_BOUNDED_QUERY_ONLY_NOT_GLOBAL_OR_ALL_LIVE_TOTAL',
            'rows': source['rows'],
        }
    live = status['sources'].get('live')
    ended = bool(live and live['phase'] == 'ENDED' and live['result'] == 'COMPLETE')
    review = build_review(status)
    analysis_reason = None
    if 'placements' in review:
        analysis_reason = review['placements']['reason']
    elif 'ads' in status['sources']:
        ads_review = review['ads']
        analysis_reason = ads_review.get('reason')
        if (ads_review['state']=='COMPUTED' and ads_review['analysis']['coverage']=='COMPLETE'
                and ads_review['daily_date_coverage']['state']=='MISSING_DAILY_ROWS'):
            analysis_reason = ACCOUNT_DAILY_ROWS_INCOMPLETE
    if analysis_reason==ACCOUNT_CYCLE_PAGINATION_UNVERIFIED:
        for channel in {'ads','ad_links','placements'} & status['sources'].keys():
            observations[channel]['coverage'] = 'HISTORICAL_ROWS_WITH_UNVERIFIED_CYCLE_PAGINATION'
    report = {
        'schema': 'bounded-monitor-report-v1', 'mode': status['mode'],
        'created_at': datetime.now(timezone.utc).isoformat(), 'monitor_phase': status['phase'],
        'monitor_error': status['last_error'], 'request_attempts_reserved': status['requests_reserved'],
        'collection_cycles':status['cycle_count'],
        'request_budget': status['request_budget'], 'observations': observations,
        'live_end': 'END_STATUS_OBSERVED' if ended else 'NOT_ESTABLISHED',
        'live_end_evidence_mode': status['mode'],
        'four_live_metrics': live_metrics_section(status['sources']),
        'ads_numeric_analysis': review.get('ads', {}).get('state','NOT_APPLICABLE'), 'business_review': review,
        'ads_live_relationship': review.get('object_relationship', {}).get('state','NOT_ESTABLISHED'), 'causal_claim_allowed': False,
        'real_connection_verified': False, 'opaque_cursor_persisted': False,
        'limits': [
            'last_successful_sync can describe an earlier query; it does not make the current partial/error query successful',
            'page exhaustion is a bounded query result, not proof of all comments or all historical objects',
            'observed_at is collection time, not source data_as_of; latest comment creation time is not metric refresh time',
            'a live end status does not establish complete comments, complete metrics or ad effects',
            'local interruption is not proof that a remote ad or live broadcast ended',
        ],
    }
    comments = status['sources'].get('comments')
    if 'live_cycle_history' in status:
        report['live_cycle_timeline'] = timeline_data(status)
    for key in ('account_cycle_coverage','account_cycle_pagination','page_live_cycle_coverage'):
        if key in status: report[key] = status[key]
    if 'unpublished_snapshot_count' in status:
        report['unpublished_snapshot_count'] = status['unpublished_snapshot_count']
    if 'resume_recovery' in status:
        report['resume_recovery'] = status['resume_recovery']
    for key in ('saved_phase','owner_lock_observation','monitor_process_verified','historical_page_run','execution_purpose'):
        if key in status: report[key] = status[key]
    for kind in PAGE_RUN_FAILURES:
        field = 'page_'+kind
        if field in status:
            report[field] = page_issue_summary(status[field])
    if analysis_reason is not None and ('account_cycle_coverage' in status or analysis_reason in (
                                        ACCOUNT_CYCLE_PAGINATION_UNVERIFIED,ACCOUNT_DAILY_ROWS_INCOMPLETE)):
        report['analysis_blocker'] = analysis_reason
    if 'insights_role_observation' in status:
        report['insights_role_observation'] = status['insights_role_observation']
    if 'rate_limit' in status:
        report['rate_limit'] = status['rate_limit']
        report['rate_limit_pending'] = status['rate_limit_pending']
    if comments and comments['plan']['operation']==RECENT_LIVE_COMMENTS:
        report['live_comment_sample'] = recent_comment_sample(comments)
    if 'workspace_identity_sha256' in status:
        report['workspace_identity_sha256'] = status['workspace_identity_sha256']
    expected_video = live['plan'].get('expected_live_video_id') if live else None
    if expected_video is not None:
        report['live_video_binding'] = dict(page_id=live['plan']['page_id'], live_id=live['plan']['object_id'],
                                           expected_video_id=expected_video, collection_state=live['result'],
                                           matched=live['result']=='COMPLETE', last_error=live['last_error'])
    return report


def save_report(monitor, directory, *, previous_page_run=False):
    if type(monitor) is not ReadMonitor: fail('MONITOR_REPORT_REQUIRES_VALIDATED_STATE_READER')
    status = monitor.previous_page_run_status() if previous_page_run else foreground_status(monitor)
    return _write_monitor_report(status,directory)


def save_report_with_state(monitor, directory):
    if type(monitor) is not ReadMonitor: fail('MONITOR_REPORT_REQUIRES_VALIDATED_STATE_READER')
    status = monitor.status()
    return _write_monitor_report(status, directory), status


def _write_monitor_report(status, directory):
    report = report_data(status)
    receipt_details = {'analysis_blocker': report['analysis_blocker']} if 'analysis_blocker' in report else {}
    if 'workspace_identity_sha256' in report:
        receipt_details['workspace_identity_sha256'] = report['workspace_identity_sha256']
    account_only = any(key in report['business_review'] for key in ('inventory','placements'))
    lines = ['# 有界监控观察报告', '', f"模式：{report['mode']}；本地状态：{report['monitor_phase']}。",
             f"已进入 {report['collection_cycles']} 轮采集。",
             '这里记录已保存的有界观察。各轮覆盖分开解释，真实连接和经营效果各自验收。', '',
             '| 数据源 | 查询结果 | 已保存行数 | 最近一次成功 | 当前错误 |', '|---|---|---:|---|---|']
    for channel, row in report['observations'].items():
        if account_only and channel not in status['sources']:
            continue
        lines.append('| ' + ' | '.join(str(x) for x in (channel, row['state'], row.get('current_query_rows', '未配置'),
                     row['last_successful_sync'] or '无', row.get('last_error') or '无')) + ' |')
    if 'execution_purpose' in report:
        receipt_details['execution_purpose'] = report['execution_purpose']
        lines += ['', '本次为具名 Page Live 单轮只读合同检查，逐项结果待人工验收。']
    lines += cycle_coverage_markdown(report.get('account_cycle_coverage') or report.get('page_live_cycle_coverage'))
    if 'account_cycle_pagination' in report:
        lines += ['', f"广告轮次分页方式 `{report['account_cycle_pagination']}`。"]
    if report.get('analysis_blocker')==ACCOUNT_CYCLE_PAGINATION_UNVERIFIED:
        lines.append('广告明细保留供回看。本轮分页口径待核，暂缓合计与关联。')
    if 'unpublished_snapshot_count' in report:
        lines += ['', f"运行目录保留 {report['unpublished_snapshot_count']} 份未发布快照。当前报告使用最后通过校验的正式快照。"]
    if 'resume_recovery' in report:
        lines += ['', '此前保存状态为运行中。本次重新获得原进程锁并确认范围后，从第一页恢复采集。']
    if 'owner_lock_observation' in report:
        lines += ['', f"保存阶段 `{report.get('saved_phase','NOT_CONFIGURED')}`，进程锁观察 `{report['owner_lock_observation']}`。"]
    history = report.get('historical_page_run')
    if history is not None:
        receipt_details['historical_page_run'] = history
        lines += ['', f"从上一次具名直播运行 `{history['run_id']}` 的快照 `{history['snapshot']}` 回读。"
                  f"原保存阶段 `{report['saved_phase']}`。报告依据为该快照，原运行完成回执另行核对。"]
    lines += page_issue_markdown(report)
    hold = report.get('rate_limit')
    if hold is not None:
        lines += ['', f"限流等待截至 {hold['not_before']}，依据 {hold['basis']}。"
                  f"本轮已预留 {hold['automatic_retries_reserved']} 次补试。"]
        if 'local_not_before' in hold:
            lines.append(f"本地保留的剩余等待延至 {hold['local_not_before']}。")
        if history is not None:
            lines.append('这是上次运行的限流记录。恢复当前任务时请查看当前运行状态。')
    if account_only:
        lines += review_markdown(report['business_review'])
        return write_report(directory,'REPORT',report,lines,'BOUNDED_OBSERVATION_REPORT_WRITTEN',**receipt_details)
    lines += ['', f"直播结束：{report['live_end']}（证据模式 {report['mode']}）。",
              '四项直播指标如下，未配置、缺失、空响应和错误均不能填0；多个桶不相加。广告与直播关系见复盘部分，不作因果结论。',
              '广告数值使用同账户币种、时区和固定 Insights 口径。网站转化需另有明确返回的归因分桶。',
              'observed_at是读取时刻；data_as_of未知，freshness也未知。最近成功时间可能属于更早批次，不能替当前错误或未读完的批次背书。',
              '详情和清洗后的行见REPORT.json；没有作者、评论正文、凭据或opaque游标。']
    video_binding = report.get('live_video_binding')
    if video_binding is not None:
        lines += ['', f"具名 Live `{video_binding['live_id']}` 对应 Video `{video_binding['expected_video_id']}`。"
                  f"本批{'核对一致' if video_binding['matched'] else '未核对通过'}，采集状态 {video_binding['collection_state']}。"]
    role = report.get('insights_role_observation')
    if role is not None:
        lines += ['', f"主页 `{role['page_id']}` 的指标任务核对结果为 `{role['state']}`，本轮查询 {role['get_attempts']} 页。"]
    sample = report.get('live_comment_sample')
    if sample is not None:
        count = sample['observed_comment_count']
        lines += ['', '### 本批直播评论样本', '',
                  f"顶层评论，首选倒序。覆盖 {sample['coverage']}，有效页 {sample['valid_pages']}，"
                  f"已观察并去重 {count if count is not None else '未知'} 条。",
                  f"有创建时间的行 {sample['rows_with_creation_time']} 条，"
                  f"最早 {sample['oldest_created_at'] or '未知'}，最晚 {sample['newest_created_at'] or '未知'}。"]
        if 'requested_since' in sample:
            lines.append(f"本次请求的评论时间下界为 `{sample['requested_since']}`。中断期间的评论覆盖未知。")
    lines += timeline_markdown(report.get('live_cycle_timeline'))
    lines += live_metrics_markdown(report['four_live_metrics'])
    lines += review_markdown(report['business_review'])
    return write_report(directory,'REPORT',report,lines,'BOUNDED_OBSERVATION_REPORT_WRITTEN',**receipt_details)


def validate_report_cycle_coverage(observations, coverage, *, error, page_live=False):
    """Check projected sources against the saved latest round."""
    if any(channel not in CHANNELS or type(row) is not dict for channel,row in observations.items()):
        fail(error)
    configured = {channel:row for channel,row in observations.items() if row.get('state')!='NOT_CONFIGURED'}
    groups = {}
    for field in ('sampled_sources','unsampled_sources','incomplete_sources','earlier_cycle_sources'):
        entries = coverage.get(field)
        if (type(entries) is not list or len(entries)>len(configured)
                or any(type(channel) is not str or channel not in configured for channel in entries)
                or entries!=sorted(set(entries))):
            fail(error)
        groups[field] = set(entries)
    sampled, unsampled = groups['sampled_sources'], groups['unsampled_sources']
    earlier = groups['earlier_cycle_sources']
    if (sampled & unsampled or sampled | unsampled != configured.keys() or not earlier <= unsampled
            or sampled and coverage['cycle']==0 or earlier and coverage['cycle']<2):
        fail(error)

    incomplete = set()
    for channel,row in configured.items():
        state = row.get('state')
        if channel in unsampled:
            if (state not in ('NOT_RUN','ERROR') or type(row.get('valid_pages')) is not int
                    or row['valid_pages']!=0 or row.get('rows')!=[] or row.get('observed_at') is not None):
                fail(error)
        elif state not in ('COMPLETE','EMPTY','GAP','ERROR') or row.get('observed_at') is None:
            fail(error)
        elif state in ('GAP','ERROR'):
            recent_sample = (page_live and channel=='comments' and state=='GAP'
                             and type(row.get('valid_pages')) is int and row['valid_pages']>0)
            if not recent_sample:
                incomplete.add(channel)
    if coverage['cycle']==0:
        state = 'NOT_STARTED'
    else:
        state = 'INCOMPLETE' if unsampled or incomplete else 'COMPLETE'
    if coverage.get('state')!=state or groups['incomplete_sources']!=incomplete:
        fail(error)

    if 'uncommitted_request' in coverage:
        request = coverage['uncommitted_request']
        if type(request) is not dict or request.keys()!={'source','generation'}:
            fail(error)
        channel, generation = request['source'], request['generation']
        if type(channel) is not str or channel not in configured or type(generation) is not int or generation<=0:
            fail(error)
        row = configured[channel]
        if (coverage['cycle']==0 or type(row.get('generation')) is not int or row['generation']!=generation
                or row.get('collector_phase')!='READY'
                or channel in sampled and row.get('state')!='GAP'):
            fail(error)


def cycle_coverage_markdown(coverage):
    if coverage is None or coverage['state']=='COMPLETE': return []
    unsampled = '、'.join(coverage['unsampled_sources']) or '无'
    incomplete = '、'.join(coverage['incomplete_sources']) or '无'
    lines = ['', f"本轮未取得结果：{unsampled}。已采集但缺页或失败：{incomplete}。"]
    request = coverage.get('uncommitted_request')
    if request is not None:
        lines.append(f"已预留 `{request['source']}` 的读取，本次预留尚无已保存结果。有效页仅统计已保存的响应。")
    if coverage['earlier_cycle_sources']:
        lines.append('较早轮次的数据留在运行历史中，本轮仅保留最近成功时间。')
    return lines


def live_metrics_markdown(metric_report):
    lines = ['', '| 指标 | 查询对象与接口 | 采集/值状态 | 值 | period | 桶截止时间 | observed_at | data_as_of / freshness |',
             '|---|---|---|---|---|---|---|---|']
    for name, metric in metric_report['metrics'].items():
        rows = metric['observations'] or [None]
        scope = f"{metric['object_id']} / {metric['read_scope']}" if metric['object_id'] else '未配置'
        for row in rows:
            state = metric['collection_state']
            if row and row['state'] != state:
                state += '/' + row['state']
            if metric['last_error']:
                state += f" (`{metric['last_error']}`)"
            data_as_of = (row['data_as_of'] if row else None) or '未知'
            freshness = row['freshness'] if row else 'UNKNOWN'
            values = (name, scope, state,
                      canonical(row['value']) if row and row['value'] is not None else '未知/不可用',
                      (row['period'] if row else None) or '未知',
                      (row['bucket_end_time'] if row else None) or '未知',
                      row['observed_at'] if row else '未观察', f'{data_as_of} / {freshness}')
            lines.append('| ' + ' | '.join(str(value) for value in values) + ' |')
    lines += ['', '桶截止时间用于核对统计窗口。平台数据刷新时间由 data_as_of 单独记录。',
              '指标最近成功只表示该次有界响应被解析，MISSING/EMPTY也可能成功；它不保证数值可用或平台数据新鲜。',
              '真实帖子 Insights 权限、指标可用性和 period 待具名实测。']
    return lines


def save_period_review(previous, current, directory, workspace=None, *, alert_rules=None, qualified_lead_evidence=None):
    if type(previous) is not ReadMonitor or type(current) is not ReadMonitor:
        fail('MONITOR_REPORT_REQUIRES_VALIDATED_STATE_READER')
    return _write_period_review(previous.status(),current.status(),directory,workspace,alert_rules=alert_rules,
                                qualified_lead_evidence=qualified_lead_evidence)


def save_period_review_from_receipts(previous, current, directory, workspace=None, *,
                                     alert_rules=None, qualified_lead_evidence=None):
    from .ads_reconciliation import read_run_observations
    old_state,old_run = read_run_observations(previous,workspace_insights=True)
    new_state,new_run = read_run_observations(current,workspace_insights=True)
    if old_run['run_id']==new_run['run_id']:
        fail('PERIOD_REVIEW_REQUIRES_DISTINCT_ARCHIVED_RUNS')
    return _write_period_review(old_state,new_state,directory,workspace,alert_rules=alert_rules,
                                qualified_lead_evidence=qualified_lead_evidence,
                                input_runs=dict(previous=old_run,current=new_run))


def _write_period_review(previous, current, directory, workspace, *, alert_rules, qualified_lead_evidence,
                         input_runs=None):
    from .period_review import build_period_review, markdown
    report = build_period_review(previous,current,workspace,alert_rules=alert_rules,
                                 qualified_lead_evidence=qualified_lead_evidence)
    extras = {}
    if input_runs is not None:
        report['input_runs'] = input_runs
        extras.update(input_runs=input_runs,network_used=False,native_credential_accessed=False)
    if 'workspace_identity_sha256' in report:
        extras['workspace_identity_sha256'] = report['workspace_identity_sha256']
    if 'alert_review' in report:
        alerts = report['alert_review']
        extras.update(alert_state=alerts['state'],review_signals=alerts['review_signals'],alert_rules_sha256=alerts['rules_sha256'])
    if qualified_lead_evidence is not None:
        sample = report['sample_review']
        extras.update(qualified_lead_review_state=sample['state'],
                      qualified_lead_evidence_sha256=sample['qualified_lead_evidence_sha256'],
                      qualified_lead_proposal_decision=report['qualified_lead_proposal_basis']['decision'])
    return write_report(directory,'COMPARISON',report,markdown(report),'PERIOD_REVIEW_WRITTEN',
                        comparison_state=report['state'], reason=report['reason'],**extras)


def write_report(directory, filename, report, lines, status, **extra):
    root = new_directory(directory)
    contents = {filename+'.json': canonical(report) + '\n', filename+'.md': '\n'.join(lines) + '\n'}
    files = []
    for name, content in contents.items():
        data = content.encode('utf-8')
        with (root / name).open('xb') as handle: handle.write(data); handle.flush(); os.fsync(handle.fileno())
        files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    receipt = dict(status=status, mode=report['mode'], files=files,
                   real_connection_verified=False, live_action_authorized=False, **extra)
    with (root / 'RECEIPT.json').open('x', encoding='utf-8') as handle: handle.write(canonical(receipt) + '\n')
    return receipt


def demo_page(rows, after=None):
    body = {'data': rows}
    if after: body['paging'] = {'cursors': {'after': after}, 'next': 'PRIVATE_NEXT_URL_NOT_PERSISTED'}
    return 200, body


def live_demo_sources(include_metrics, *, pin_video=False):
    live = ReadPlan('live', 'SIM_LIVE', page_id='SIM_PAGE', reviewed_live_contract=True)
    if pin_video:
        live = PageLiveReadPlan('live', 'SIM_LIVE', page_id='SIM_PAGE', reviewed_live_contract=True,
                                expected_live_video_id='SIM_VIDEO')
    comments = ReadPlan(RECENT_LIVE_COMMENTS, 'SIM_LIVE', page_id='SIM_PAGE', reviewed_live_contract=True)
    def live_row(status): return (200, {'id': 'SIM_LIVE', 'status': status, 'from': {'id': 'SIM_PAGE'}, 'video': {'id': 'SIM_VIDEO'}})
    sources = {
        'live': (live, MockTransport([live_row('LIVE'), live_row('LIVE_STOPPED')])),
        'comments': (comments, MockTransport([demo_page([{'id': 'SIM_C2', 'created_time': '2026-11-17T12:00:01Z'}], 'DEMO_COMMENT_CURSOR'),
                                             demo_page([{'id': 'SIM_C1', 'created_time': '2026-11-17T12:00:00Z'}])])),
    }
    if include_metrics:
        post = ReadPlan('live_post','SIM_POST',page_id='SIM_PAGE',reviewed_live_contract=True)
        post_response = (200, {'id':'SIM_POST','from':{'id':'SIM_PAGE'},'attachments':{'data':[
            {'media_type':'video','target':{'id':'SIM_VIDEO'}}]}})
        sources['live_post'] = (post,MockTransport([post_response]*2))
        for index, (channel, metric) in enumerate(METRIC_CHANNELS.items()):
            def envelope(values): return (200, {'data': [{'name': metric, 'period': 'lifetime', 'values': values}]})
            if index == 0: responses = [envelope([{'value': 0}])] * 2
            elif index == 1: responses = [(200, {'data': []})] * 2
            elif index == 2: responses = [envelope([]), (403, {'error': {'code': 200, 'message': 'NOT_PERSISTED'}})]
            else: responses = [envelope([{'value': {'live': 4, 'vod': 2}}])] * 2
            sources[channel] = (PostMetricReadPlan(object_id='SIM_POST',page_id='SIM_PAGE',live_id='SIM_LIVE',video_id='SIM_VIDEO',metric=metric), MockTransport(responses))
    return sources


def run_monitor_demo(directory, include_metrics=False, include_review=False):
    if type(include_metrics) is not bool or type(include_review) is not bool: fail('MONITOR_DEMO_OPTION')
    include_metrics = include_metrics or include_review
    root = new_directory(directory)
    ads = ReadPlan('ads', 'SIM_ACCOUNT', account_id='SIM_ACCOUNT')
    sources = {
        'ads': (ads, MockTransport([demo_page([{'id': 'SIM_AD_A', 'account_id': 'SIM_ACCOUNT', 'status': 'PAUSED'}], 'DEMO_AD_CURSOR'),
                                   demo_page([{'id': 'SIM_AD_B', 'account_id': 'SIM_ACCOUNT', 'status': 'PAUSED'}])])),
        **live_demo_sources(include_metrics),
    }
    if include_review:
        sources['account'] = (ReadPlan('account', 'SIM_ACCOUNT', account_id='SIM_ACCOUNT'), MockTransport([
            (200, {'id': 'act_SIM_ACCOUNT', 'account_id': 'SIM_ACCOUNT', 'currency': 'USD',
                   'timezone_name': 'America/Los_Angeles', 'account_status': 1})] * 2))
        plan = ReadPlan(CONVERSION_HIERARCHY_INSIGHTS, 'SIM_ACCOUNT', account_id='SIM_ACCOUNT',
                        since='2026-11-16', until='2026-11-16')
        common = dict(account_id='SIM_ACCOUNT', account_currency='USD', date_start='2026-11-16', date_stop='2026-11-16')
        row_a = dict(common, ad_id='SIM_AD_A', campaign_id='SIM_CAMPAIGN_A', adset_id='SIM_ADSET_A',
                     spend='10.10', impressions='1000', clicks='20',
                     actions=[{'action_type':WEBSITE_PURCHASE,CLICK_WINDOW:'2'},{'action_type':WEBSITE_LEAD,CLICK_WINDOW:'4'}],
                     action_values=[{'action_type':WEBSITE_PURCHASE,CLICK_WINDOW:'40.40'}])
        row_b = dict(common, ad_id='SIM_AD_B', campaign_id='SIM_CAMPAIGN_A', adset_id='SIM_ADSET_B',
                     spend='4.90', impressions='500', clicks='5',
                     actions=[{'action_type':WEBSITE_PURCHASE,CLICK_WINDOW:'0'},{'action_type':WEBSITE_LEAD,CLICK_WINDOW:'1'}],
                     action_values=[{'action_type':WEBSITE_PURCHASE,CLICK_WINDOW:'0.00'}])
        sources['ads'] = (plan, MockTransport([
            demo_page([row_a], 'DEMO_INSIGHTS_CURSOR'), demo_page([row_b])]))
        sources['ad_links'] = (ReadPlan('ad_videos', 'SIM_ACCOUNT', account_id='SIM_ACCOUNT'), MockTransport([
            demo_page([dict(id='SIM_AD_A', account_id='SIM_ACCOUNT', creative={'id': 'SIM_CREATIVE_A', 'video_id': 'SIM_VIDEO'})], 'DEMO_LINK_CURSOR'),
            demo_page([dict(id='SIM_AD_B', account_id='SIM_ACCOUNT', creative={'id': 'SIM_CREATIVE_B', 'video_id': 'SIM_OTHER_VIDEO'})])]))
    monitor = ReadMonitor(root / 'monitor-state')
    status = monitor.run(sources, interval_seconds=.05, request_budget=20 if include_review else (16 if include_metrics else 6), cycles=2)
    receipt = save_report(monitor, root / 'report')
    return dict(status='SYNTHETIC_MONITOR_DEMO_PASSED', mode='MOCK', observations=status, report_receipt=receipt,
                real_connection_verified=False, credentials_accessed=False)


def run_page_live_rehearsal(directory, interval_seconds=5, *, resume=False):
    if type(interval_seconds) is not int or not 1 <= interval_seconds <= 60:
        fail('LIVE_REHEARSAL_INTERVAL')
    monitor = open_monitor(directory) if resume else ReadMonitor(new_directory(directory))
    root = monitor.journal.directory
    previous = monitor.status().get('sources',{}) if resume else {}
    pin_video = not resume or 'expected_live_video_id' in previous.get('live',{}).get('plan',{})
    sources = {
        'page': (ReadPlan('page','SIM_PAGE',page_id='SIM_PAGE'),MockTransport([(200,{'id':'SIM_PAGE'})]*2)),
        **live_demo_sources(True,pin_video=pin_video),
    }
    if resume and (monitor.mode!='MOCK' or set(previous)!=set(sources)):
        fail('PAGE_LIVE_REHEARSAL_STATE_REQUIRED')
    with Journal(root/'runner-owner',mode='MOCK').locked():
        state = monitor.run(sources,interval_seconds=interval_seconds,request_budget=20,cycles=2,
                            resume=resume,exit_on_pause=True)
        report_directory = root/'runs'/uuid4().hex/'report'
        receipt = save_report(monitor,report_directory)
    ended = state['sources']['live']['phase']=='ENDED'
    return dict(status='PAGE_LIVE_REHEARSAL_RECORDED',mode='MOCK',monitor_phase=state['phase'],
                live_end_observed=ended,resumed=resume,report_directory=str(report_directory),
                report_receipt=receipt,real_connection_verified=False,credentials_accessed=False,network_used=False)
