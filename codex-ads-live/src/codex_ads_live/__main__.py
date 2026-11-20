"""Local tools plus an explicitly confirmed interactive two-GET connection check."""
import json
from pathlib import Path
import sys
from . import __version__
from .cli_arguments import LocalArgumentParser
from .diagnostics import diagnose
from .runtime import LocalRuntime
from .validation import InputError, strict_json
from .vendor.t0_core import ContractError


def main(argv=None, *, first_launch_provider=None, first_launch_preview_provider=None, first_launch_activation_provider=None):
    parser = LocalArgumentParser(prog='run.py', description="Codex Ads & Page Live 本地工具（开发候选）")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--macos-system-proxy", action="store_true",
                        help="Meta 只读请求使用本机已配置的 macOS HTTPS 系统代理")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="只检查本机版本/时区数据/包清单；不收集环境变量、凭据或账号")
    sub.add_parser("network-check", help="对固定 Meta HTTPS 地址发不含凭据的 HEAD 连通性请求")
    prepare = sub.add_parser("prepare", help="新建空白客户准备表和本机自检，不连接服务")
    prepare.add_argument("--output", type=Path, required=True)
    preparation_review=sub.add_parser('preparation-review',help='检查已有准备表的阶段/缺项；声明不当授权，不连接服务')
    preparation_review.add_argument('preparation',type=Path)
    preparation_review.add_argument('--output',type=Path)
    from .meta_write_mapping import OPERATIONS, SNAPSHOT_OPERATIONS
    from .workflow import OPERATOR_WORKFLOWS
    workflow_guide = sub.add_parser('workflow-guide',help='查阅指定客户流程的入口、本人步骤、失败接续和回执')
    workflow_guide.add_argument('--workflow',choices=OPERATOR_WORKFLOWS)
    meta_prepare = sub.add_parser('meta-write-prepare', help='生成非执行写参数草案；不访问凭据或Meta')
    meta_prepare.add_argument('--operation', choices=OPERATIONS, required=True)
    meta_prepare.add_argument('--output', type=Path, required=True)
    meta_review = sub.add_parser('meta-write-review', help='审阅写参数映射/Diff/hash；始终阻断真实执行')
    meta_review.add_argument('draft', type=Path)
    meta_review.add_argument('--snapshots', type=Path, help='可选：核对本地读取快照；文件来源未验真，仍不能执行')
    meta_review.add_argument('--material-dir',type=Path,help='复核完整本地材料绑定目录及其素材副本')
    meta_review.add_argument('--output', type=Path)
    material_prepare=sub.add_parser('material-prepare',help='为已具名CREATE_AD草案生成空白素材审阅表；无网络或批准')
    material_prepare.add_argument('draft',type=Path);material_prepare.add_argument('--output',type=Path,required=True)
    for action in ('material-review','material-bind'):
        command=sub.add_parser(action,help='展示完整素材/权利声明并核本地文件；绑定只生成新草案，不批准或写入Meta')
        command.add_argument('material',type=Path);command.add_argument('--asset-root',type=Path)
        command.add_argument('--output',type=Path,required=action=='material-bind')
        if action=='material-bind':
            command.add_argument('--draft',type=Path,required=True)
            command.add_argument('--workspace',type=Path,help='核对该草案对应的当前工作区')
            command.add_argument('--review-dir',type=Path,help='附同一账户的历史有效询盘复盘')
            command.add_argument('--source-run-receipts',nargs=2,type=Path,metavar=('BEFORE','AFTER'),
                                 help='绑定素材前用两份原采集回执复核提案依据')
    recheck=sub.add_parser('meta-recheck-review',help='比较两份本地固定字段快照；不访问凭据、授予批准或执行写入')
    recheck.add_argument('draft',type=Path)
    recheck.add_argument('--before',type=Path,required=True)
    recheck.add_argument('--material-dir',type=Path,help='此前审阅引用的同一材料绑定目录')
    recheck.add_argument('--after',type=Path,required=True)
    recheck.add_argument('--expected-review-sha256',required=True)
    recheck.add_argument('--output',type=Path)
    for name, help_text in (
        ('activation-review-sim', '列出模拟启用影响的广告组、广告和素材'),
        ('budget-review-sim', '核对模拟日预算调整涉及的广告、受众及素材')
    ):
        impact = sub.add_parser(name, help=help_text)
        impact.add_argument('draft', type=Path)
        impact.add_argument('--allowlist', type=Path, required=True)
        impact.add_argument('--service-dir', type=Path, required=True)
        impact.add_argument('--material', type=Path, action='append', default=[])
        impact.add_argument('--asset-root', type=Path, required=True)
        impact.add_argument('--output', type=Path, required=True)
    mapped=sub.add_parser('mapped-demo',help='五类映射动作及失效/撤回/未知结果的固定合成演示；无真实批准或Meta请求')
    mapped.add_argument('--output',type=Path,required=True)
    mapped_mode=mapped.add_mutually_exclusive_group()
    mapped_mode.add_argument('--keep-interrupted',action='store_true',help='保留两个中断动作供后续回读')
    mapped_mode.add_argument('--first-launch',action='store_true',help='同一合成账户的四步暂停建单演练')
    mapped_mode.add_argument('--resume-first-launch',action='store_true',help='回读原首投模拟账本后仅接续尚未登记的步骤')
    reconcile_mapped=sub.add_parser('mapped-reconcile-sim',help='从既有合成服务回读单个模拟动作')
    reconcile_mapped.add_argument('--state-dir',type=Path,required=True)
    reconcile_mapped.add_argument('--service-dir',type=Path,required=True)
    reconcile_mapped.add_argument('--action',required=True)
    for action in ('mapped-status','mapped-revoke-sim','mapped-stop-sim'):
        command=sub.add_parser(action,help='查看或停止已有映射动作模拟账本；不批准、发送或撤销远端动作')
        command.add_argument('--state-dir',type=Path,required=True)
        if action=='mapped-revoke-sim': command.add_argument('--action',required=True)
    snapshot_prepare=sub.add_parser('snapshot-prepare',help='准备单动作具名读取计划；不读取凭据或连接Meta')
    snapshot_prepare.add_argument('--operation',choices=SNAPSHOT_OPERATIONS,required=True)
    snapshot_prepare.add_argument('--output',type=Path,required=True)
    for action in ('snapshot-preview','snapshot-collect'):
        command=sub.add_parser(action,help='预览或本人已授权后交互采集固定对象快照；无广告写入')
        command.add_argument('intent',type=Path)
        command.add_argument('--credential-record-dir',type=Path)
        if action=='snapshot-collect': command.add_argument('--output',type=Path,required=True)
    connection_prepare = sub.add_parser("connection-prepare", help="新建不含秘密的空白连接意图；不访问凭据或Meta")
    connection_prepare.add_argument("--output", type=Path, required=True)
    connection_preview = sub.add_parser("connection-preview", help="预览已填写的具名范围及阻断项；不访问凭据或Meta")
    connection_preview.add_argument("intent", type=Path)
    connection_preview.add_argument("--credential-record-dir", type=Path)
    connection_check = sub.add_parser("connection-check", help="仅本人已授权后交互式执行：临时/具名OS凭据、最多2次GET；不创建授权")
    connection_check.add_argument("intent", type=Path)
    connection_check.add_argument("--output", type=Path, required=True)
    connection_check.add_argument("--credential-record-dir", type=Path)
    connection_review = sub.add_parser('connection-review',help='回读具名账户或主页的已完成连接留档，不访问系统凭据或 Meta')
    connection_review.add_argument('--run-dir',type=Path,required=True,help='含连接检查 RECEIPT.json 的目录')
    connection_review.add_argument('--intent-sha256',help='可选，从已核对的预览回执固定原意图摘要')
    credential_prepare = sub.add_parser("credential-prepare", help="创建非秘密登记；本人另行录入系统凭据库，本命令不访问系统凭据")
    credential_prepare.add_argument("--app-id", required=True)
    credential_prepare.add_argument("--principal-id", required=True)
    credential_prepare.add_argument("--platform", choices=("macos", "windows"), required=True)
    credential_prepare.add_argument("--output", type=Path, required=True)
    for action in ("credential-status", "credential-disable"):
        command = sub.add_parser(action, help="读取/停用非秘密登记；不读取、删除系统凭据，不撤销Meta授权")
        command.add_argument("--record-dir", type=Path, required=True)
    diagnostic = sub.add_parser("diagnose", help="分类元数据诊断；不接收凭据、真实ID、姓名或原始API响应")
    diagnostic.add_argument("input", type=Path)
    method = sub.add_parser("method", help="核验随包方法全文与依赖；不执行上游脚本")
    method.add_argument("--intent", choices=("ads_plan", "creative", "analytics", "positioning"), required=True)
    demo = sub.add_parser("demo", help="固定合成案例贯通演示；拒绝已有输出目录")
    demo.add_argument("--output", type=Path, required=True)
    monitor_demo = sub.add_parser("monitor-demo", help="广告/直播状态/评论的合成监控及有界报告，不连Meta")
    monitor_demo.add_argument("--output", type=Path, required=True)
    monitor_demo.add_argument("--include-metrics", action="store_true", help="同时模拟四指标的0/缺失/空/错误/结构值；不连接Meta")
    monitor_demo.add_argument("--include-review", action="store_true", help="包含四指标、账户口径、Insights和直接Video关系的合成复盘")
    for action in ('monitor-live-start-sim','monitor-live-resume-sim'):
        command = sub.add_parser(action, help='独立 Page Live 的有界前台演练，使用固定合成对象')
        command.add_argument('--state-dir',type=Path,required=True)
        command.add_argument('--interval',type=int,default=5)
    monitor_prepare = sub.add_parser("monitor-prepare", help="新建不含秘密的监控计划模板；不请求Meta")
    monitor_prepare.add_argument("--output", type=Path, required=True)
    for action in ("monitor-preview", "monitor-start", "monitor-resume"):
        command = sub.add_parser(action, help="预览或本人已授权后的有界前台读取；启动/恢复均重新核验授权")
        command.add_argument("intent", type=Path)
        command.add_argument("--credential-record-dir", type=Path)
        command.add_argument("--workspace", type=Path, help="工作区生成的计划须提供原客户工作区")
        if action != "monitor-preview": command.add_argument("--state-dir", type=Path, required=True)
    for action in ('page-live-contract-preview','page-live-contract-check'):
        command = sub.add_parser(action, help='具名现成 Page Live 的一次性只读验收。实际检查须本人当次授权')
        command.add_argument('intent',type=Path)
        command.add_argument('--workspace',type=Path,required=True)
        command.add_argument('--credential-record-dir',type=Path,required=True)
        if action=='page-live-contract-check':
            command.add_argument('--state-dir',type=Path,required=True,help='全新状态目录')
    for action, purpose in (('page-live-contract-review','单轮取证'),('page-live-run-review','前台监控')):
        command = sub.add_parser(action,help=f'离线核对 Page Live {purpose}归档')
        command.add_argument('--run-dir',type=Path,required=True,help='含原运行外层 RECEIPT.json 的目录')
        command.add_argument('--intent-sha256',help='可选，指定原计划的意图摘要')
    for action in ("monitor-status", "monitor-health", "monitor-pause", "monitor-stop", "monitor-report"):
        command = sub.add_parser(action, help="已有读取监控器的本地控制/报告；不读取凭据")
        command.add_argument("--state-dir", type=Path, required=True)
        if action == "monitor-report":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--previous-page-run", action="store_true", help="回读前一次具名 Page Live 的最后正式快照")
    comparison = sub.add_parser("monitor-compare", help="比较两个已保存的广告日期范围或具名采集批次")
    for label in ('previous','current'):
        source = comparison.add_mutually_exclusive_group(required=True)
        source.add_argument(f'--{label}-state-dir',type=Path,help='监控目录的当前已保存观察')
        source.add_argument(f'--{label}-run-receipt',type=Path,help='具名采集归档 runs/<编号>/RECEIPT.json')
    comparison.add_argument("--workspace", type=Path)
    comparison.add_argument('--alert-rules',type=Path,help='本次具名账户的本地阈值规则 JSON')
    comparison.add_argument('--qualified-leads',type=Path,help='两期有效询盘汇总 JSON，须配合 --workspace')
    comparison.add_argument("--output", type=Path, required=True)
    separate = sub.add_parser('monitor-live-ads-review', help='用广告和 Page Live 的本地快照或固定采集归档复盘')
    for label in ('ads','live'):
        source = separate.add_mutually_exclusive_group(required=True)
        source.add_argument(f'--{label}-state-dir',type=Path,help='监控状态目录')
        source.add_argument(f'--{label}-run-receipt',type=Path,help='固定采集批次的外层 RECEIPT.json')
    separate.add_argument('--from-review-receipt',type=Path,help='核对原复盘回执记录的两份快照或运行')
    separate.add_argument('--output',type=Path,required=True)
    for action in ('monitor-reconcile-prepare','monitor-reconcile'):
        command = sub.add_parser(action,help='用保存的广告数据准备或对账本地 Ads Manager 导出')
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument('--state-dir',type=Path,help='读取监控目录当前观察')
        source.add_argument('--run-receipt',type=Path,help='固定采集批次的 runs/<编号>/RECEIPT.json')
        command.add_argument('--output',type=Path,required=True)
        if action=='monitor-reconcile':
            command.add_argument('--export-csv',type=Path,required=True)
            command.add_argument('--export-settings',type=Path,required=True)
    approval_status = sub.add_parser("approval-status", help="读取已有模拟审批账本")
    approval_status.add_argument("--state-dir", type=Path, required=True)
    creation_status = sub.add_parser("creation-status", help="读取已有PAUSED三层创建模拟记录；无真实写工具")
    creation_status.add_argument("--state-dir", type=Path, required=True)
    proposal_prepare = sub.add_parser("proposal-prepare-sim", help="生成可审的固定模拟案例；复用既有文案，不授权真实广告动作")
    proposal_prepare.add_argument("--output", type=Path, required=True)
    proposal_import = sub.add_parser("proposal-import-sim", help="校验SIM案例并在全新目录建立模拟服务/审批账本")
    proposal_import.add_argument("case", type=Path)
    proposal_import.add_argument("--state-dir", type=Path, required=True)
    for action in ("proposal-review", "proposal-approve-sim", "proposal-execute-sim", "proposal-reconcile-sim", "proposal-revoke-sim", "proposal-status", "proposal-stop-sim"):
        command = sub.add_parser(action, help="逐项审阅/控制本地模拟提案；无真实身份验证或写接口")
        command.add_argument("--state-dir", type=Path, required=True)
        if action not in ("proposal-status", "proposal-stop-sim"): command.add_argument("--action", required=True)
        if action == "proposal-review": command.add_argument("--output", type=Path)
        if action == "proposal-approve-sim":
            command.add_argument("--review-hash", required=True)
            command.add_argument("--ttl-seconds", type=int, default=600)
    workflow_status = sub.add_parser("workflow-status", help="读取已有合成工作流状态；不授予真实权限")
    workflow_status.add_argument("--state-dir", type=Path, required=True)
    for action in ("start","resume","status","pause","stop","health"):
        command=sub.add_parser(action,help="本地MOCK运行器；没有真实账户请求")
        command.add_argument("--state-dir",type=Path,required=True)
        if action in ("start","resume"):
            command.add_argument("--interval",type=float,default=1)
            command.add_argument("--cycles",type=int,default=None,help="省略则前台持续运行；Ctrl-C中断")
    from .client_workspace import add_commands
    from .graph_network import graph_network_scope
    add_commands(sub)
    args = parser.parse_args(argv)
    with graph_network_scope(args.macos_system_proxy):
        return _dispatch(args, first_launch_provider=first_launch_provider,
                         first_launch_preview_provider=first_launch_preview_provider,
                         first_launch_activation_provider=first_launch_activation_provider)


def _dispatch(args, *, first_launch_provider=None, first_launch_preview_provider=None, first_launch_activation_provider=None):
    unicode_terminal = sys.stdout.isatty() and sys.stdout.encoding == 'utf-8'
    try:
        if args.command.startswith("workspace-"):
            from .client_workspace import run_command
            if args.command == 'workspace-first-launch-progress':
                if first_launch_provider is None and (args.dispatch_approved is not None or args.recover_host is not None):
                    from .first_launch_local_host import load_host
                    first_launch_provider = load_host(args.workspace,args.plan_dir).creation()
                result = run_command(args, first_launch_provider=first_launch_provider)
            elif args.command == 'workspace-first-launch-preview':
                if first_launch_preview_provider is None and args.fetch:
                    from .first_launch_local_host import load_host
                    first_launch_preview_provider = load_host(args.workspace,args.plan_dir).preview()
                result = run_command(args, first_launch_preview_provider=first_launch_preview_provider)
            elif args.command == 'workspace-first-launch-activation':
                if first_launch_activation_provider is None and args.mode in ('dispatch', 'recover'):
                    from .first_launch_local_host import load_host
                    first_launch_activation_provider = load_host(args.workspace,args.plan_dir).activation()
                result = run_command(args, first_launch_activation_provider=first_launch_activation_provider)
            else:
                result = run_command(args)
        elif args.command == "preflight":
            from .onboarding import preflight
            result = preflight()
        elif args.command == "network-check":
            from .graph_network import check_graph_network
            result = check_graph_network()
        elif args.command == "prepare":
            from .onboarding import prepare
            result = prepare(args.output)
        elif args.command == 'preparation-review':
            from .preparation_review import review_preparation
            result=review_preparation(args.preparation,args.output)
        elif args.command in ('meta-write-prepare', 'meta-write-review'):
            from .meta_write_mapping import prepare_draft, load_draft, export_review
            if args.command == 'meta-write-prepare': result = prepare_draft(args.operation, args.output)
            elif args.snapshots:
                from .write_snapshots import load_snapshot_bundle, export_snapshot_review
                result = export_snapshot_review(load_draft(args.draft),load_snapshot_bundle(args.snapshots),args.output,
                                                material_directory=args.material_dir)
            else: result = export_review(load_draft(args.draft),args.output,material_directory=args.material_dir)
        elif args.command=='meta-recheck-review':
            from .meta_write_mapping import load_draft
            from .write_snapshots import load_snapshot_bundle
            from .write_recheck import export_recheck
            result=export_recheck(load_draft(args.draft),load_snapshot_bundle(args.before),load_snapshot_bundle(args.after),
                                 args.expected_review_sha256,args.output,material_directory=args.material_dir)
        elif args.command.startswith('material-'):
            from .materials import prepare_material, load_material, export_material
            from .meta_write_mapping import load_draft
            if args.command=='material-prepare': result=prepare_material(load_draft(args.draft),args.output)
            elif args.command=='material-bind':
                result=export_material(load_material(args.material),args.asset_root,args.output,load_draft(args.draft),
                                       workspace=args.workspace,review_directory=args.review_dir,
                                       source_run_receipts=args.source_run_receipts)
            else:
                result=export_material(load_material(args.material),args.asset_root,args.output)
        elif args.command in ('activation-review-sim', 'budget-review-sim'):
            from .activation import export_activation
            result = export_activation(args.draft, args.allowlist, args.service_dir, args.material,
                                       args.asset_root, args.output, budget=args.command == 'budget-review-sim')
        elif args.command=='mapped-demo':
            from .mapped_demo import run_first_launch_demo, run_mapped_demo
            if args.first_launch or args.resume_first_launch:
                result=run_first_launch_demo(args.output,resume=args.resume_first_launch)
            else:
                result=run_mapped_demo(args.output,keep_interrupted=args.keep_interrupted)
        elif args.command=='mapped-reconcile-sim':
            from .mapped_actions import reconcile_saved_simulation
            result=reconcile_saved_simulation(args.state_dir,args.service_dir,args.action)
        elif args.command.startswith('mapped-'):
            from .mapped_actions import MappedActionLedger
            if not (args.state_dir/'operator-state.json').is_file(): raise InputError('MAPPED_STATE_NOT_FOUND')
            ledger=MappedActionLedger(args.state_dir)
            if args.command=='mapped-stop-sim': result=ledger.stop()
            else:
                if args.command=='mapped-revoke-sim': ledger.revoke(args.action)
                result=ledger.status()
        elif args.command.startswith('snapshot-'):
            from .snapshot_entry import prepare_snapshot, load_snapshot_intent, preview_snapshot, run_snapshot
            if args.command=='snapshot-prepare': result=prepare_snapshot(args.operation,args.output)
            elif args.command=='snapshot-preview': result=preview_snapshot(load_snapshot_intent(args.intent),record_directory=args.credential_record_dir)
            else: result=run_snapshot(load_snapshot_intent(args.intent),args.output,record_directory=args.credential_record_dir)
        elif args.command=='connection-review':
            from .connection_evidence import review_connection
            result = review_connection(args.run_dir,expected_intent_sha256=args.intent_sha256)
        elif args.command.startswith("connection-"):
            from .connection import prepare_connection, load_intent, preview, check_connection
            if args.command == "connection-prepare": result = prepare_connection(args.output)
            elif args.command == "connection-preview": result = preview(load_intent(args.intent), record_directory=args.credential_record_dir)
            else: result = check_connection(load_intent(args.intent), args.output, record_directory=args.credential_record_dir)
        elif args.command.startswith("credential-"):
            from .credential_record import prepare_record, read_record, public_record, disable_record
            if args.command == "credential-prepare":
                result = prepare_record(args.output, args.app_id, args.principal_id, args.platform)
            elif args.command == "credential-status": result = public_record(read_record(args.record_dir))
            else: result = disable_record(args.record_dir)
        elif args.command=="diagnose":
            if args.input.stat().st_size > 1_000_000:
                raise InputError("INPUT_TOO_LARGE")
            data = strict_json(args.input.read_text(encoding="utf-8"))
            result = diagnose(data)
        elif args.command == "method":
            from .method_bundle import bundled_route
            result = bundled_route(args.intent)
        elif args.command == "demo":
            from .demo import run_demo
            result = run_demo(args.output)
        elif args.command == "monitor-demo":
            from .monitor_tools import run_monitor_demo
            result = run_monitor_demo(args.output, include_metrics=args.include_metrics, include_review=args.include_review)
        elif args.command in ('monitor-live-start-sim','monitor-live-resume-sim'):
            from .monitor_tools import run_page_live_rehearsal
            result = run_page_live_rehearsal(args.state_dir,args.interval,resume=args.command=='monitor-live-resume-sim')
        elif args.command in ("monitor-prepare", "monitor-preview", "monitor-start", "monitor-resume"):
            from .monitor_runner import prepare_monitor, load_monitor_intent, preview_monitor, run_monitor
            if args.command == "monitor-prepare": result = prepare_monitor(args.output)
            elif args.command == "monitor-preview":
                result = preview_monitor(load_monitor_intent(args.intent), record_directory=args.credential_record_dir, workspace=args.workspace)
            else:
                result = run_monitor(load_monitor_intent(args.intent), args.state_dir,
                                     resume=args.command == "monitor-resume", record_directory=args.credential_record_dir, workspace=args.workspace)
        elif args.command in ('page-live-contract-preview','page-live-contract-check'):
            from .monitor_runner import load_monitor_intent, preview_monitor, run_monitor
            intent = load_monitor_intent(args.intent)
            scope = dict(workspace=args.workspace,record_directory=args.credential_record_dir,contract_probe=True)
            if args.command=='page-live-contract-preview':
                result = preview_monitor(intent,**scope)
            else:
                result = run_monitor(intent,args.state_dir,**scope)
        elif args.command in ('page-live-contract-review','page-live-run-review'):
            from .page_contract_evidence import review_page_contract, review_page_run
            review = review_page_contract if args.command=='page-live-contract-review' else review_page_run
            result = review(args.run_dir,expected_intent_sha256=args.intent_sha256)
        elif args.command == "monitor-compare":
            from .monitor_tools import open_monitor, save_period_review, save_period_review_from_receipts
            from .period_alerts import load_rules
            from .qualified_leads import load_evidence
            archived = args.previous_run_receipt is not None
            if archived != (args.current_run_receipt is not None):
                raise InputError('PERIOD_REVIEW_MATCHING_INPUT_TYPES_REQUIRED')
            rules = load_rules(args.alert_rules) if args.alert_rules is not None else None
            evidence = load_evidence(args.qualified_leads) if args.qualified_leads is not None else None
            options = dict(alert_rules=rules,qualified_lead_evidence=evidence)
            if archived:
                result = save_period_review_from_receipts(args.previous_run_receipt,args.current_run_receipt,
                                                          args.output,args.workspace,**options)
            else:
                result = save_period_review(open_monitor(args.previous_state_dir),open_monitor(args.current_state_dir),
                                            args.output,args.workspace,**options)
        elif args.command == 'monitor-live-ads-review':
            from .monitor_tools import open_monitor
            from .separate_ads_live import save_archived_separate_review, save_separate_review
            archived = args.ads_run_receipt is not None
            if archived != (args.live_run_receipt is not None):
                raise InputError('SEPARATE_REVIEW_MATCHING_INPUT_TYPES_REQUIRED')
            if archived:
                result = save_archived_separate_review(args.ads_run_receipt,args.live_run_receipt,args.output,
                                                       from_review_receipt=args.from_review_receipt)
            else:
                result = save_separate_review(open_monitor(args.ads_state_dir,existing_only=True),
                                              open_monitor(args.live_state_dir,existing_only=True),args.output,
                                              from_review_receipt=args.from_review_receipt)
        elif args.command in ('monitor-reconcile-prepare','monitor-reconcile'):
            from .ads_reconciliation import prepare_reconciliation, reconcile
            from .monitor_tools import open_monitor
            monitor = open_monitor(args.state_dir,existing_only=True) if args.state_dir is not None else None
            if args.command=='monitor-reconcile-prepare':
                result = prepare_reconciliation(monitor,args.output,run_receipt=args.run_receipt)
            else:
                result = reconcile(monitor,args.export_csv,args.export_settings,args.output,run_receipt=args.run_receipt)
        elif args.command.startswith("monitor-"):
            from .monitor_tools import foreground_status, open_monitor, save_report
            control = args.command in ('monitor-pause','monitor-stop')
            monitor = open_monitor(args.state_dir,existing_only=not control)
            if args.command == "monitor-report": result = save_report(monitor, args.output, previous_page_run=args.previous_page_run)
            elif control: result = monitor.control(args.command.split("-")[1])
            else: result = foreground_status(monitor)
        elif args.command == "approval-status":
            from .approval_ledger import DurableApprovalSimulation
            if not (args.state_dir / "operator-state.json").is_file():
                raise InputError("APPROVAL_STATE_NOT_FOUND")
            result = DurableApprovalSimulation(args.state_dir).status()
        elif args.command.startswith("proposal-"):
            from .proposal_desk import prepare_case, import_case, load_case, ProposalDesk
            if args.command == "proposal-prepare-sim": result = prepare_case(args.output)
            elif args.command == "proposal-import-sim": result = import_case(load_case(args.case), args.state_dir)
            else:
                desk = ProposalDesk(args.state_dir)
                if args.command == "proposal-review": result = desk.review(args.action, args.output)
                elif args.command == "proposal-approve-sim": result = desk.approve_sim(args.action, args.review_hash, args.ttl_seconds)
                elif args.command == "proposal-execute-sim": result = desk.execute_sim(args.action)
                elif args.command == "proposal-reconcile-sim": result = desk.reconcile_sim(args.action)
                elif args.command == "proposal-revoke-sim": result = desk.revoke_sim(args.action)
                elif args.command == "proposal-stop-sim": result = desk.stop()
                else: result = desk.status()
        elif args.command == 'workflow-guide':
            from .workflow import operator_guide
            result = operator_guide(args.workflow)
        elif args.command == "workflow-status":
            from .workflow_state import SyntheticWorkflow
            if not (args.state_dir / "operator-state.json").is_file():
                raise InputError("WORKFLOW_STATE_NOT_FOUND")
            result = SyntheticWorkflow(args.state_dir).status()
        elif args.command == "creation-status":
            from .creation import DurablePausedCreation
            if not (args.state_dir / "operator-state.json").is_file():
                raise InputError("CREATION_STATE_NOT_FOUND")
            result = DurablePausedCreation(args.state_dir).status()
        else:
            runtime=LocalRuntime(args.state_dir)
            if args.command in ("start","resume"):
                result=runtime.run(args.interval,args.cycles,resume=args.command=="resume")
            elif args.command in ("stop","pause"):
                result=runtime.control(args.command)
            else: result=runtime.status()
    except (InputError, ContractError) as exc:
        print(json.dumps({"status":"REJECTED","error_code":str(exc)}, ensure_ascii=not unicode_terminal))
        return 2
    except (OSError, UnicodeError):
        print('{"status":"REJECTED","error_code":"LOCAL_IO_FAILURE"}')
        return 2
    except KeyboardInterrupt:
        print('{"status":"INTERRUPTED","error_code":"USER_INTERRUPT"}')
        return 130
    print(json.dumps(result, ensure_ascii=not unicode_terminal, indent=2, allow_nan=False))
    if args.command == 'network-check' and result['status'] != 'META_HTTPS_REACHABLE': return 2
    if args.command in ('workspace-plan','workspace-replan') and result['status'] == 'REJECTED': return 2
    if args.command in ('workspace-refresh','workspace-page-refresh') and result['status'] != 'HEALTHY': return 2
    if args.command == 'connection-check' and result['status'] != 'NAMED_ACCOUNT_READ_PASSED':
        return 130 if result['status'] == 'INTERRUPTED' else 2
    if args.command=='connection-review' and not result['recorded_named_read']:
        return 1
    if args.command in ('page-live-contract-review','page-live-run-review') and result['recorded_status']!='MONITOR_RUN_ENDED':
        return 1
    if args.command in ('monitor-start','monitor-resume','page-live-contract-check') and result['status'] != 'MONITOR_RUN_ENDED':
        return 130 if result['status'] == 'INTERRUPTED' else 2
    if args.command in ('proposal-execute-sim', 'proposal-reconcile-sim') and result['status'] != 'VERIFIED_SIM': return 2
    if (args.command=='mapped-demo' and (args.first_launch or args.resume_first_launch)
            and result['status']!='FIRST_LAUNCH_SIM_VERIFIED'):
        return 2
    if args.command=='mapped-reconcile-sim' and result['readback_outcome']!='VERIFIED_SIM': return 2
    if args.command=='snapshot-collect' and result['status']!='SNAPSHOT_CAPTURED_REVIEW_BLOCKED':
        return 130 if result['status']=='INTERRUPTED' else 2
    if args.command=='meta-recheck-review' and result['status']=='REAPPROVAL_REQUIRED': return 3
    return 0


if __name__ == "__main__": sys.exit(main())
