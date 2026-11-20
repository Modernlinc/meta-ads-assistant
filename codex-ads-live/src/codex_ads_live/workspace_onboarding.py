"""A scoped onboarding handoff from local prerequisites and saved observations."""
from pathlib import Path

from . import client_workspace as workspace
from .connection import utcnow
from .credential_record import read_record
from .journal import digest
from .monitor_tools import write_report
from .onboarding import preflight
from .validation import InputError, fail


SCOPES = {'ads': ('ACCOUNT',), 'page': ('PAGE',), 'ads-and-page': ('ACCOUNT', 'PAGE')}
TASKS = {'ACCOUNT': 'ads-analysis', 'PAGE': 'page-read'}
RECENT = {'ACCOUNT': 'RECENT_NAMED_ACCOUNT_READ', 'PAGE': 'RECENT_NAMED_PAGE_READ'}
NEXT_CHECKS = {
    'COMPLETE_APP_AND_BUSINESS_MAPPING': '核对已确认的 App 与 Business Portfolio，在已有工作区补齐准备信息。',
    'SELECT_NAMED_ASSET': '指定本次目标 ID，或使用客户已明确选定的默认资产。',
    'CONFIRM_NAMED_MAPPING': '向客户核对该目标的归属，再补当前工作区的具名映射。',
    'RECONFIRM_TARGET_AND_ACCESS': '核对该目标的角色、共享关系和归属，重新绑定这个目标，再复查读取。',
    'CONNECT_EXISTING_APP': '复用已确认的 App，完成本机非秘密登记后用 workspace-connection-prepare 准备具名账户。',
    'CHECK_NAMED_OS_REFERENCE': '核对该目标使用的非秘密登记及当前接入记录。',
    'REAUTHORIZE_EXISTING_APP': '由客户在现有 App 对应的官方流程处理到期授权，然后复查同一目标。',
    'REVIEW_REQUIRED_PERMISSION': '核对本次任务所需权限，再由客户确认实际授权。',
    'VERIFY_NEW_ACCOUNT_ONLY': '核对该广告账户在已有 App、主体和登记中的范围。',
    'VERIFY_PAGE_ROLES_AND_ACCESS': '用 workspace-connection-prepare --kind PAGE 核对具名角色证据与本机登记，再确认绑定。',
    'REVIEW_NAMED_PAGE_ACCESS': '核对这一个 Page 的实际授权和角色，再用 workspace-page-refresh 复查。',
    'REPAIR_THIS_SCOPE': '根据该目标的连接回执核对认证、权限或资产状态。',
    'REVIEW_ADAPTER_OR_PROFILE_VERSION': '按分阶段升级流程核对工作区结构与当前候选版本。',
}


def asset_report(profile, route, kind):
    target = route['target_id']
    key = workspace.asset_key(kind, target) if target is not None else None
    mapped = key in profile['assets']
    check = profile['checks'].get(key)
    recent = route['access_observation'] == RECENT[kind]
    result = 'RECENT' if recent else 'REVIEW_REQUIRED' if check else 'NOT_RECORDED'
    refresh = 'workspace-refresh' if kind == 'ACCOUNT' else 'workspace-page-refresh'
    follow_up = {
        'ACCOUNT': '需要广告表现时，准备对应日期与账户口径的 Insights 计划。',
        'PAGE': '需要直播时，另核具名 Live 对象、评论权限及每项帖子指标。',
    }
    action = (follow_up[kind] if recent else NEXT_CHECKS.get(route['reason'],
              f'获准后用 {refresh} 重新核对这个具名目标。'))
    out = dict(target_id=target, mapped=mapped, connection_evidence=result,
               mode=route['mode'], route_status=route['status'], reason=route['reason'],
               required_permissions=route['required_permissions'],
               last_recorded_at=check['at'] if check else None,
               last_recorded_status=check['status'] if check else None,
               last_recorded_permissions=check['permissions'] if check else [],
               last_recorded_access_expires_at=check['expires'] if check else None,
               next_check=action)
    if kind == 'ACCOUNT':
        out.update(currency=check['currency'] if recent else None, timezone=check['timezone'] if recent else None)
    else:
        out.update(owner_reported_role=route.get('owner_reported_role'),
                   grant_binding=route.get('page_grant_binding'), role_platform_verified=False)
    return out


def markdown(report):
    local = report['local_preflight']
    bindings = report['workspace']
    lines = ['# 客户工作区接入报告', '', f"记录于 {report['assessed_at']}。范围 `{report['scope']}`。",
             f"接入记录状态 `{report['assessment']}`。", '', '## 本机条件', '',
             f"{local['system']} {local['system_release']} · {local['architecture']} · Python {local['python']}。",
             f"本机检查 `{local['status']}`。安装包摘要 `{local['manifest_sha256'] or '未知'}`。",
             '', '| 检查 | 结果 |', '|---|---|']
    for key, value in local['checks'].items():
        lines.append(f'| {key} | {value} |')
    lines += ['', f"App {'已登记' if bindings['app_configured'] else '待登记'}。"
              f"Business Portfolio {'已登记' if bindings['business_configured'] else '待登记'}。"]
    for kind, row in report['assets'].items():
        label = '广告账户' if kind == 'ACCOUNT' else 'Facebook 公共主页'
        lines += ['', f'## {label}', '',
                  f"目标 `{row['target_id'] or '待选择'}`。工作区映射{'已登记' if row['mapped'] else '待登记'}。",
                  f"连接记录 `{row['connection_evidence']}`。范围检查提示 `{row['reason']}`。",
                  f"本任务需 {', '.join(row['required_permissions'])}。最近留档在 {row['last_recorded_at'] or '尚无记录'}。"]
        if row['last_recorded_access_expires_at']:
            lines.append(f"上次记录的授权有效期截至 {row['last_recorded_access_expires_at']}。")
        if row['connection_evidence'] == 'RECENT' and kind == 'ACCOUNT':
            lines.append(f"最近读取的币种 `{row['currency']}` 和账户时区 `{row['timezone']}`。")
        if row['connection_evidence'] == 'RECENT' and kind == 'PAGE':
            lines.append(f"客户声明的主页角色 `{row['owner_reported_role']}`。授权观察 `{row['grant_binding']}`。")
        lines.append(row['next_check'])
    lines += ['', '## 后续验收', '',
              '报告汇总本机预检和工作区留档。具名读取仍按每次运行的实时检查执行。',
              '宿主识别、账户报表、真实直播、客户签收及写入授权分别留证。报告含所选资产 ID，留在客户项目内。']
    return lines


def _recheck_observed_records(profile, routes, requested, default_record):
    """Keep newly changed registration state out of otherwise recent evidence."""
    verified = {}
    for kind, route in routes.items():
        if route['access_observation'] != RECENT[kind]:
            continue
        binding = profile['binding'] if kind == 'ACCOUNT' else profile['page_bindings'][route['target_id']]
        _, record = requested[kind]
        path = Path(record).absolute() if record is not None else default_record
        if path not in verified:
            try:
                verified[path] = digest(read_record(path, existing_only=True))
            except InputError:
                fail('WORKSPACE_ONBOARDING_RECORD_CHANGED')
        if verified[path] != binding['record_hash']:
            fail('WORKSPACE_ONBOARDING_RECORD_CHANGED')


def save_report(directory, output, scope, *, account_id=None, page_id=None, record_directory=None,
                page_record_directory=None, clock=utcnow):
    if scope not in SCOPES:
        fail('WORKSPACE_ONBOARDING_SCOPE')
    selected = SCOPES[scope]
    if (('ACCOUNT' not in selected and (account_id is not None or record_directory is not None))
            or ('PAGE' not in selected and (page_id is not None or page_record_directory is not None))):
        fail('WORKSPACE_ONBOARDING_UNUSED_SCOPE_ARGUMENT')
    profile = workspace.read(directory, existing_only=True)
    for target in (account_id, page_id):
        if target is not None:
            workspace.numeric(target)
    local = preflight()
    assessed = clock()
    requested = {'ACCOUNT': (account_id, record_directory), 'PAGE': (page_id, page_record_directory)}
    routes = {}
    for kind in selected:
        target, record = requested[kind]
        routes[kind] = workspace.health_check(directory, TASKS[kind], target, clock=lambda: assessed,
                                               record_directory=record, existing_only=True)
    snapshot = digest(profile)
    journal = workspace.journal_for(directory, existing_only=True)
    with journal.locked(existing_only=True):
        if digest(workspace.load(journal)) != snapshot:
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        _recheck_observed_records(profile, routes, requested, journal.directory / 'credential-record')
        assets = {kind:asset_report(profile, routes[kind], kind) for kind in selected}
        ready = (local['status'] == 'LOCAL_PREREQUISITES_PRESENT'
                 and all(row['connection_evidence'] == 'RECENT' for row in assets.values()))
        report = dict(schema='workspace-onboarding-report-v1', mode='LOCAL_RECORDED_EVIDENCE',
                      assessed_at=assessed.isoformat(), scope=scope, assets=assets,
                      assessment='LOCAL_CONNECTION_CHECKS_RECORDED' if ready else 'REVIEW_REQUIRED',
                      workspace=dict(profile_sha256=snapshot, schema=profile['schema'], skill_version=profile['skill_version'],
                                     adapter=profile['adapter'], app_configured=profile['app_id'] is not None,
                                     business_configured=profile['business_id'] is not None),
                      local_preflight=local, network_used=False, native_credential_accessed=False,
                      real_connection_verified=False, customer_acceptance='NOT_ASSESSED',
                      live_action_authorized=False, live_and_insights_coverage='NOT_ASSESSED')
        return write_report(output, 'ONBOARDING', report, markdown(report), 'WORKSPACE_ONBOARDING_REPORT_WRITTEN',
                            assessment=report['assessment'], network_used=False, native_credential_accessed=False)
