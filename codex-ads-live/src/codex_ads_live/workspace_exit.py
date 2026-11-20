"""Record bounded local exit evidence for explicitly selected workspace inputs."""
from contextlib import ExitStack

from . import client_workspace as workspace
from .connection import utcnow
from .credential_record import read_record
from .journal import Journal, digest
from .monitor_tools import open_monitor, write_report
from .validation import InputError, fail


PENDING_SIMULATED_ACTIONS = ('PROPOSED','APPROVED_SIM','RECHECKING','UNKNOWN')


def observe_mapped_ledger(directory, index):
    from .mapped_actions import MappedActionLedger

    entry = dict(input_index=index,status='UNVERIFIED',owner_lock='UNKNOWN',reason=None)
    try:
        ledger = MappedActionLedger(directory,existing_only=True)
        with ExitStack() as locks:
            try:
                locks.enter_context(ledger.owner.locked(existing_only=True))
                entry['owner_lock'] = 'RELEASED'
            except FileNotFoundError:
                # A ledger that has never executed may have no execution lock file.
                entry['owner_lock'] = 'NOT_OBSERVED'
            except InputError as exc:
                if str(exc)!='STATE_BUSY': raise
                entry['owner_lock'] = 'HELD'
            saved = ledger.recorded_exit_actions()
        actions = saved['actions']
        pending = [row for row in actions if row['state'] in PENDING_SIMULATED_ACTIONS]
        entry.update(mode='MOCK',stopped=saved['stopped'],recorded_actions=len(actions),pending_actions=pending)
        if entry['owner_lock']=='HELD':
            return dict(entry,status='OWNER_LOCK_HELD',reason='WAIT_FOR_SIMULATED_EXECUTION_EXIT')
        if entry['owner_lock']=='NOT_OBSERVED' and any(row['execution_id'] is not None for row in actions):
            return dict(entry,reason='EXECUTION_LOCK_EVIDENCE_MISSING')
        if pending:
            return dict(entry,status='PENDING_ACTIONS',reason='REVIEW_OR_READ_BACK_ORIGINAL_SIMULATED_ACTIONS')
        return dict(entry,status='NO_PENDING_ACTIONS')
    except InputError as exc:
        return dict(entry,reason=str(exc))
    except (OSError,UnicodeError):
        return dict(entry,reason='LOCAL_LEDGER_STATE_UNAVAILABLE')


def observe_runner(directory, identity, index):
    entry = dict(input_index=index, status='UNVERIFIED', saved_phase=None,
                 owner_lock='UNKNOWN', reason=None)
    try:
        monitor = open_monitor(directory, existing_only=True)
        if monitor.mode != 'READ_ONLY':
            return dict(entry, reason='UNBOUND_MONITOR_MODE')
        owner = Journal(monitor.journal.directory/'runner-owner',mode='READ_ONLY',existing_only=True)
        owner_lock = 'RELEASED'
        with ExitStack() as locks:
            try:
                locks.enter_context(owner.locked(existing_only=True))
            except InputError as exc:
                if str(exc)!='STATE_BUSY': raise
                owner_lock = 'HELD'
            with monitor.journal.locked(wait_seconds=.5,existing_only=True):
                state = monitor._load()
        if state is None or 'workspace_identity_sha256' not in state:
            return dict(entry, reason='WORKSPACE_RUN_IDENTITY_MISSING')
        if state['workspace_identity_sha256'] != identity:
            return dict(entry, reason='WORKSPACE_RUN_IDENTITY_MISMATCH')
        entry.update(owner_lock=owner_lock,saved_phase=state['phase'])
        if owner_lock == 'HELD':
            return dict(entry,status='OWNER_LOCK_HELD',reason='WAIT_FOR_FOREGROUND_TASK_EXIT')
        if state['phase'] == 'RUNNING':
            return dict(entry,reason='RUNNING_PHASE_WITHOUT_OWNER_LOCK')
        return dict(entry,status='OWNER_LOCK_RELEASED')
    except InputError as exc:
        return dict(entry,reason=str(exc))
    except (OSError, UnicodeError):
        return dict(entry,reason='LOCAL_RUN_STATE_UNAVAILABLE')


def _binding_groups(profile, basis):
    groups = {}

    def group(binding):
        return groups.setdefault(binding['record_hash'],dict(ad_binding=False,account_ids=set(),page_ids=set(),
                                                             observed={},binding_basis=basis))
    account = profile['binding']
    if account is not None:
        group(account).update(ad_binding=True,account_ids=set(account['accounts']))
    for page_id, binding in sorted(profile.get('page_bindings',{}).items()):
        group(binding)['page_ids'].add(page_id)
    return groups


def _validate_historical_profile(profile):
    if type(profile) is dict and profile.get('schema')=='client-profile-v1':
        workspace.validate_profile(profile,version='client-profile-v1')
    else:
        workspace.validate_profile(profile)


def _historical_groups(journal, snapshots, identity, selected):
    groups = {}
    for path in snapshots:
        profile, _ = journal.read_snapshot(path,_validate_historical_profile)
        if workspace.workspace_identity(profile)!=identity:
            continue
        for reference, row in _binding_groups(profile,'HISTORICAL_WORKSPACE_BINDING').items():
            if reference in selected:
                aggregate = groups.setdefault(reference,row)
                aggregate['ad_binding'] |= row['ad_binding']
                aggregate['account_ids'].update(row['account_ids'])
                aggregate['page_ids'].update(row['page_ids'])
    return groups


def observe_records(profile, directories, journal, snapshots):
    groups = _binding_groups(profile,'CURRENT_WORKSPACE_BINDING')
    observations = []
    records = []
    for index, path in enumerate(directories,1):
        try:
            record = read_record(path,existing_only=True)
        except InputError as exc:
            observations.append(dict(input_index=index,status='UNVERIFIED',reason=str(exc)))
            continue
        prepared = digest(dict(record,state='PREPARED',disabled_at=None))
        entry = dict(input_index=index,status=record['state'],reason=None)
        observations.append(entry)
        records.append((prepared,record,entry))

    selected = {reference for reference,_,_ in records}
    history_state = 'NOT_CHECKED'
    missing_reason = 'RECORD_NOT_BOUND_TO_WORKSPACE'
    if selected:
        try:
            history = _historical_groups(journal,snapshots,workspace.workspace_identity(profile),selected)
            history_state = 'CHECKED'
        except (OSError, ValueError, TypeError, OverflowError):
            history = {}
            history_state = 'UNVERIFIED'
            missing_reason = 'WORKSPACE_RECORD_HISTORY_UNVERIFIED'
        for reference, previous in history.items():
            current = groups.get(reference)
            if current is None:
                groups[reference] = previous
                continue
            accounts = previous['account_ids']-current['account_ids']
            pages = previous['page_ids']-current['page_ids']
            ad_removed = previous['ad_binding'] and not current['ad_binding']
            if accounts or pages or ad_removed:
                current['former_assets'] = dict(ad_binding_removed=ad_removed,account_ids=sorted(accounts),page_ids=sorted(pages))

    for prepared,record,entry in records:
        if prepared not in groups:
            entry.update(status='UNVERIFIED',reason=missing_reason)
            continue
        row = groups[prepared]
        row['observed'][digest(record)] = (record['state'],record['disabled_at'])
    bindings = []
    for row in groups.values():
        observed = row.pop('observed')
        if len(observed)==1:
            status, disabled_at = next(iter(observed.values()))
        else:
            status = 'CONFLICTING_RECORD_COPIES' if observed else 'NOT_OBSERVED'
            disabled_at = None
        bindings.append(dict(row,status=status,disabled_at=disabled_at,account_ids=sorted(row['account_ids']),
                             page_ids=sorted(row['page_ids'])))
    return bindings, observations, history_state


def markdown(report):
    lines = ['# 客户工作区退出交接', '', f"核对时间 {report['observed_at']}。状态 `{report['assessment']}`。",
             '', '## 指定运行器', '',
             '只核对命令中提供的目录。持锁时，即使保存的状态为 STOPPED，仍需等前台进程返回。']
    if report['runners']:
        lines += ['', '| 输入顺序 | 保存状态 | 所有者锁 | 核对结果 |', '|---:|---|---|---|']
        for row in report['runners']:
            lines.append(f"| {row['input_index']} | {row['saved_phase'] or '未知'} | {row['owner_lock']} | "
                         f"{row['reason'] or row['status']} |")
    else:
        lines.append('本次未提供运行器目录。')
    lines += ['', '## 本地登记', '']
    for row in report['bindings']:
        accounts = ', '.join(row['account_ids']) or ('账户连接登记' if row['ad_binding'] else '—')
        pages = ', '.join(row['page_ids']) or '—'
        disabled = f"停用时间 {row['disabled_at']}。" if row['disabled_at'] else ''
        scope = '历史绑定' if row['binding_basis']=='HISTORICAL_WORKSPACE_BINDING' else '当前绑定'
        lines.append(f"- {scope}。广告账户 {accounts}。Page {pages}。登记 `{row['status']}`。{disabled}")
        if 'former_assets' in row:
            former = row['former_assets']
            former_accounts = ', '.join(former['account_ids']) or ('曾有关联登记' if former['ad_binding_removed'] else '—')
            former_pages = ', '.join(former['page_ids']) or '—'
            lines.append(f"  - 同一登记曾覆盖历史广告账户 {former_accounts}。历史主页 {former_pages}。")
    for row in report['record_inputs']:
        if row['reason']:
            lines.append(f"- 登记目录输入 {row['input_index']} 待核，原因 `{row['reason']}`。")
    if not report['bindings']:
        lines.append('当前工作区没有已绑定的本地登记。')
    if report['record_history_state']=='UNVERIFIED':
        lines.append('已提供登记的历史绑定证据待核。请保留原快照并核对退出范围。')
    if 'mapped_ledgers' in report:
        lines += ['', '## 指定的本地模拟审批账本', '',
                  '按输入顺序核对提供的账本及执行锁。结果仅对应这些独立模拟记录。']
        for row in report['mapped_ledgers']:
            lines.append(f"- 账本 {row['input_index']}。状态 `{row['status']}`，"
                         f"执行锁 `{row['owner_lock']}`，接续 `{row['reason'] or '—'}`。")
            for action in row.get('pending_actions',()):
                execution = action['execution_id'] or '尚无执行编号'
                lines.append(f"  - `{action['action_id']}` · `{action['state']}` · `{execution}`。")
        lines.append('UNKNOWN 沿原账本和独立模拟服务回读。等待中的模拟草案可在原账本审阅或撤回。')
    lines += ['', '## 退出交接核对', '',
              '在启动任务的终端核对进程退出。工作区以外的运行器、历史输出和备份按实际范围核对。',
              f"本地档案核对单状态 `{report['local_profile']['status']}`。另行决定文件保留及备份。",
              '系统凭据条目与 Meta 授权分别留证。报告含所选工作区资产 ID，请在项目内保管。']
    return lines


def save_report(directory, output, *, monitor_directories=(), record_directories=(), mapped_ledger_directories=(),
                clock=utcnow):
    journal = workspace.journal_for(directory,existing_only=True)
    with journal.locked(existing_only=True):
        profile = workspace.load(journal)
        local = workspace.removal_scope(journal,profile)
        snapshots = journal.snapshots()[:-1]
    identity = workspace.workspace_identity(profile)
    runners = [observe_runner(path,identity,index) for index,path in enumerate(monitor_directories,1)]
    if not record_directories and local['os_reference_configured']:
        record_directories = (journal.directory/'credential-record',)
    bindings, inputs, history_state = observe_records(profile,record_directories,journal,snapshots)
    ledgers = [observe_mapped_ledger(path,index) for index,path in enumerate(mapped_ledger_directories,1)]
    observed = (bool(runners) and all(row['status']=='OWNER_LOCK_RELEASED' for row in runners)
                and history_state!='UNVERIFIED'
                and all(row['status']=='DISABLED_LOCAL' for row in bindings)
                and all(row['status']=='DISABLED_LOCAL' for row in inputs)
                and all(row['status']=='NO_PENDING_ACTIONS' for row in ledgers))
    report = dict(schema='workspace-exit-report-v1',mode='LOCAL_RECORDED_EVIDENCE',observed_at=clock().isoformat(),
                  assessment='LOCAL_EXIT_EVIDENCE_RECORDED' if observed else 'REVIEW_REQUIRED',
                  runner_coverage='EXPLICIT_DIRECTORIES_ONLY',runners=runners,bindings=bindings,record_inputs=inputs,
                  record_history_state=history_state,
                  local_profile=local,process_exit_verified=False,os_item_removal_verified=False,
                  meta_revocation_verified=False,network_used=False,native_credential_accessed=False)
    if ledgers:
        report.update(mapped_ledger_coverage='EXPLICIT_SYNTHETIC_LEDGERS_ONLY',mapped_ledgers=ledgers)
    with journal.locked(existing_only=True):
        if digest(workspace.load(journal))!=digest(profile):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        return write_report(output,'EXIT_HANDOFF',report,markdown(report),'WORKSPACE_EXIT_REPORT_WRITTEN',
                            assessment=report['assessment'],record_history_state=history_state,
                            network_used=False,native_credential_accessed=False)
