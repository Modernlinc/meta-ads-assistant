"""Project a named local health check into a shareable support report."""
from . import __version__, client_workspace as workspace
from .connection import utcnow
from .monitor import PAGE_RUN_FAILURES
from .monitor_tools import page_issue_markdown, page_issue_summary, write_report
from .validation import fail
from .workflow import workspace_followup


def monitor_summary(task, route):
    if task == 'page-live':
        observed = route.get('monitor_observation',{})
        scope = observed.get('scope_state','NOT_CHECKED')
    else:
        observed = route
        scope = {'MONITOR_SCOPE_CHANGED':'CHANGED','MONITOR_SCOPE_UNVERIFIED':'UNVERIFIED'}.get(route['reason'])
        if scope is None:
            scope = 'MATCHED' if 'runner_phase' in observed else 'NOT_CHECKED'
    view = dict(scope=scope)
    if scope in ('NOT_CHECKED','UNVERIFIED'):
        return view
    for key in ('runner_phase','runner_saved_phase','runner_error','owner_lock_held','observation_recency','live_error',
                'placement_coverage','placement_error','analysis_blocker','source_observations','rate_limit_wait',
                'page_live_cycle','insights_role_state'):
        if key in observed:
            view[key] = observed[key]
    coverage = observed.get('daily_date_coverage')
    if coverage is not None:
        view['daily_date_coverage'] = {key:coverage[key] for key in ('state','declared_days','observed_days')}
        view['daily_date_coverage']['missing_day_count'] = len(coverage['missing_dates'])
    if observed.get('last_successful_sync') is not None:
        view['prior_success_recorded'] = True
    for kind in PAGE_RUN_FAILURES:
        field = 'page_'+kind
        if field in observed:
            view[field] = page_issue_summary(observed[field])
    return view


def markdown(report):
    route = report['route']
    lines = ['# 工作区脱敏诊断', '',
             f"导出时间 {report['exported_at']}。",
             f"任务 `{report['task']}` · 目标类型 `{route['target_kind']}` · 选择方式 `{report['selection']}`。",
             f"工作区状态 `{route['status']}`。原因 `{route['reason']}`。",
             f"继续查看流程 `{route['next_workflow']}`。",
             f"访问观察 `{route['access_observation']}`。数据状态 `{route['data_freshness']}`。"]
    if 'monitor' in report:
        monitor = report['monitor']
        lines += ['', f"运行范围 `{monitor['scope']}`。"]
        if 'runner_phase' in monitor:
            lines.append(f"运行阶段 `{monitor['runner_phase']}`，已记录错误 `{monitor['runner_error'] or '无'}`。")
        saved = monitor.get('runner_saved_phase')
        if saved and saved!=monitor.get('runner_phase'):
            lines.append(f"最后快照阶段 `{saved}`。")
        if monitor.get('analysis_blocker'):
            lines.append(f"分析待复查 `{monitor['analysis_blocker']}`。")
        daily = monitor.get('daily_date_coverage')
        if daily is not None:
            lines.append(f"查询 {daily['declared_days']} 天，有返回行 {daily['observed_days']} 天，"
                         f"缺行 {daily['missing_day_count']} 天。")
            if daily['missing_day_count']:
                lines.append('在原工作区按同一报表口径核对后台导出，确认具体日期与数据覆盖后再继续复盘。')
        cycle = monitor.get('page_live_cycle')
        if cycle is not None:
            lines.append(f"当前记录的轮次为 {cycle['cycle']}，覆盖状态 `{cycle['state']}`。")
        role = monitor.get('insights_role_state')
        if role is not None:
            lines.append(f"主页分析任务核对结果 `{role}`。")
        sources = monitor.get('source_observations',{})
        if sources:
            metric_states = any('metric_response_states' in row for row in sources.values())
            columns = '| 来源 | 保存结果 | 有效页 | 本轮证据 | 当前错误 | 请求预留待结果 | 既有成功记录 |'
            divider = '|---|---|---:|---|---|---|---|'
            if metric_states:
                columns += ' 指标响应记录 |'
                divider += '---|'
            lines += ['', columns, divider]
            for name,row in sources.items():
                cells = (name,row['result'],str(row['valid_pages']),row['cycle_evidence'],row['error_code'] or '无',
                         '有' if row['request_uncommitted'] else '无','有' if row['success_previously_recorded'] else '无')
                if metric_states:
                    states = row.get('metric_response_states',{})
                    cells += ('、'.join(f'{state} × {count}' for state,count in states.items()) or '—',)
                lines.append('| ' + ' | '.join(cells) + ' |')
        wait = monitor.get('rate_limit_wait')
        if wait is not None:
            state = '等待期内' if wait['pending'] else '等待期已结束'
            lines += ['', f"已记录限流。{state}，依据 `{wait['basis']}`。"]
        lines += page_issue_markdown(monitor)
    lines += ['', '本报告记录导出时可读的本机状态。实际授权与数据更新以具名任务的后续检查为准。']
    return lines


def save_task_diagnostics(directory, output, task, identifier=None, *, record_directory=None,
                          monitor_directory=None, clock=utcnow):
    if task not in workspace.DIAGNOSTIC_TASKS:
        fail('WORKSPACE_DIAGNOSTICS_TASK_REQUIRED')
    if task=='page-read' and monitor_directory is not None:
        fail('WORKSPACE_DIAGNOSTICS_MONITOR_SCOPE')
    if identifier is not None:
        workspace.numeric(identifier)
    observed_at = clock()
    route = workspace.health_check(directory,task,identifier,clock=lambda:observed_at,record_directory=record_directory,
                                   monitor_directory=monitor_directory,existing_only=True)
    selection = 'EXPLICIT' if identifier is not None else 'DEFAULT' if route['target_id'] is not None else 'UNSELECTED'
    public_route = {key:route[key] for key in ('mode','status','reason','target_kind','required_permissions',
                                              'access_observation','data_freshness','monitor_process_verified')}
    public_route['next_workflow'] = workspace_followup(route)
    report = dict(schema='workspace-task-diagnostics-v1',mode='LOCAL_RECORDED_EVIDENCE',package_version=__version__,
                  exported_at=observed_at.isoformat(),task=task,selection=selection,route=public_route,
                  network_used=False,native_credential_accessed=False)
    if monitor_directory is not None:
        report['monitor'] = monitor_summary(task,route)
    return write_report(output,'diagnostics',report,markdown(report),'REDACTED_DIAGNOSTICS_EXPORTED',
                        scope='SINGLE_TASK',network_used=False,native_credential_accessed=False)
