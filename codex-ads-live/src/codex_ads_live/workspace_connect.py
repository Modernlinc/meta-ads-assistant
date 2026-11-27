"""Meta setup and bounded account discovery on the existing client journal."""
from datetime import timedelta
from pathlib import Path
import re
import sys
from urllib.parse import urlencode

from . import client_workspace as workspace
from .connection import CHECK_ERRORS, _debug, _get_connection_json, utcnow, verified_metadata
from .credential_record import RecordGuard, read_record
from .http_read import ReadBinding
from .journal import digest
from .local_paths import checked_path
from .native_credentials import NativeCredentialBroker
from .validation import InputError, aware_time, fail, object_only

ROUTES = ("user-graph", "host-connector", "system-user")
REFERENCE = {
    "app": "https://developers.facebook.com/apps/",
    "explorer": "https://developers.facebook.com/tools/explorer/",
    "debugger": "https://developers.facebook.com/tools/debug/accesstoken/",
    "marketing": "https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization",
    "connector": "https://www.facebook.com/business/help/1456422242197840",
    "system_user": "https://developers.facebook.com/docs/business-management-apis/system-users",
    "proof": "https://developers.facebook.com/docs/graph-api/guides/secure-requests/",
}
CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,2048}")
MAX_PAGES = 5
PAGE_SIZE = 20

STEPS = {
    "INITIALIZE_WORKSPACE": ("workspace-connect --start", "在客户项目使用新工作区目录，初始化本机记录，选接入方式。"),
    "CHOOSE_ROUTE": (None, "确认使用自有 App，或由已经安装官方连接器的宿主授权；企业系统用户适用于其分配的资产。"),
    "CONNECT_IN_OFFICIAL_HOST": ("workspace-connect --host-receipt FILE", "在提供官方 Meta 连接器的宿主内由本人授权并执行“列出我的广告账户”。助手可导入无敏感内容的账户回执。"),
    "HOST_ACCOUNT_READY": ("在原宿主连接器读取，或用 media-buyer prepare 准备草案", "已选择宿主返回的具名账户。主流程继续使用原宿主会话。"),
    "SYSTEM_USER_CONFIGURATION": ("credential-prepare", "分开登记本企业有限有效期的只读系统用户与同 App 开发者检查凭据。"),
    "DISCOVER_SYSTEM_ACCOUNTS": ("workspace-connect --discover --proof-not-required", "本人确认系统用户与业务组合后，从固定账户端点读取已分配资产。"),
    "SYSTEM_USER_READ_READY": ("native-insights / native-inventory", "沿原系统用户路线进行具名读取，重新检查 App、人员和资产。"),
    "PREPARE_APP": ("workspace-connect --app-id APP --business-id BUSINESS",
                    "打开 Meta App Dashboard 创建或复用客户 App，按 Marketing API 用途和资产关系核角色及权限，再记录 App 和业务组合编号。"),
    "REVIEW_APP_PROOF": (None, "按 Meta 官方要求核对该 App 的安全配置。需要 App Secret Proof 的账户当前不能走本机 User 路线。"),
    "ENROLL_USER_REFERENCE": ("credential-prepare", "本人通过 Meta 官方授权取得本 App 的 User Token，按本次范围确认 ads_read。用官方令牌调试工具核 App 与人员编号，再按本机凭据登记步骤操作。"),
    "DISCOVER_ACCOUNTS": ("workspace-connect --discover --proof-not-required", "本人确认读取当前 App 和身份下的可访问广告账户。"),
    "SELECT_ACCOUNT": ("workspace-connect --select-account ID --ownership-evidence EV_…", "从已观察清单选择一个账户，保留客户对账户归属的确认。"),
    "NO_OBSERVED_ACCOUNTS": ("workspace-connect --discover --proof-not-required", "在 Meta 核所选 App、人员角色和广告账户归属。分页不齐时仅使用已观察项。"),
    "CHECK_NAMED_ACCOUNT": ("workspace-connection-prepare → workspace-bind → workspace-refresh", "沿已选账户生成具名读取计划，绑定登记并经本人确认读取账户基本信息。"),
    "RECONNECT": ("credential-prepare → workspace-connection-prepare → workspace-bind → workspace-refresh",
                  "按原路线核所选 App、人员和权限。授权失效时由本人重新授权，再只核受影响的账户。"),
    "REUSE_WORKSPACE": ("workspace-plan", "沿已有账户或 Page 的读取范围准备日常任务。草案可从 media-buyer 进入。"),
}


def validate_state(state):
    fields = ("route", "discovery")
    object_only(state, fields, fields)
    if state["route"] not in ROUTES:
        fail("META_CONNECT_ROUTE")
    result = state["discovery"]
    if result is None:
        return
    fields = ("observed_at", "record_sha256", "state", "pages_read", "accounts", "error_code")
    object_only(result, (*fields,'host_source'), fields)
    if state['route']=='host-connector':
        from .operator_review import evidence_text

        source = result.get('host_source')
        object_only(source,('provider','host','ref'),('provider','host','ref'))
        if source['provider']!='META_OFFICIAL_ADS_MCP':
            fail('META_CONNECT_HOST_SOURCE')
        evidence_text(source['host'])
        workspace.pattern(source['ref'],r'EV_[A-Z0-9_]{1,80}','META_CONNECT_HOST_SOURCE')
    elif 'host_source' in result:
        fail('META_CONNECT_HOST_SOURCE')
    aware_time(result["observed_at"])
    workspace.pattern(result["record_sha256"], r"[a-f0-9]{64}", "META_CONNECT_RECORD_SHA")
    if result["state"] not in ("COMPLETE", "PARTIAL", "FAILED"):
        fail("META_DISCOVERY_STATE")
    pages = result["pages_read"]
    accounts = result["accounts"]
    if type(pages) is not int or not 0 <= pages <= MAX_PAGES or type(accounts) is not list or len(accounts) > pages * PAGE_SIZE:
        fail("META_DISCOVERY_ACCOUNTS")
    error = result["error_code"]
    if (error is not None and error not in DISCOVERY_ERRORS) or (result["state"] == "COMPLETE") != (error is None):
        fail("META_DISCOVERY_STATE")
    if (result["state"] == "FAILED") != (pages == 0):
        fail("META_DISCOVERY_STATE")
    seen = set()
    for row in accounts:
        fields = ('account_id','currency','account_status')
        object_only(row, (*fields,'name'), fields)
        ident = workspace.numeric(row["account_id"])
        if ident in seen:
            fail("META_DISCOVERY_ACCOUNTS")
        seen.add(ident)
        if row["currency"] is not None:
            workspace.pattern(row["currency"], r"[A-Z]{3}", "META_DISCOVERY_CURRENCY")
        if row["account_status"] is not None and (type(row["account_status"]) is not int or not 0 <= row["account_status"] <= 999):
            fail("META_DISCOVERY_STATUS")
        if 'name' in row:
            _account_name(row['name'])


DISCOVERY_ERRORS = CHECK_ERRORS | {
    "META_DISCOVERY_INVALID_RESPONSE", "META_DISCOVERY_ACCOUNT_ID", "META_DISCOVERY_CURRENCY",
    "META_DISCOVERY_STATUS", "META_DISCOVERY_CURSOR", "META_DISCOVERY_PAGE_LIMIT", "META_DISCOVERY_ACCOUNT_CHANGED",
    "META_DISCOVERY_INTERRUPTED", "META_DISCOVERY_FAILED",
}


def _account_name(value):
    from .business_profile import SECRET_PATTERN

    if type(value) is not str or not 1<=len(value.strip())<=160 or not value.isprintable() or SECRET_PATTERN.search(value):
        fail('META_DISCOVERY_ACCOUNT_NAME')
    return value


def _page(body):
    if type(body) is not dict or type(body.get("data")) is not list or len(body["data"]) > PAGE_SIZE:
        fail("META_DISCOVERY_INVALID_RESPONSE")
    result = []
    for raw in body["data"]:
        if type(raw) is not dict:
            fail("META_DISCOVERY_INVALID_RESPONSE")
        ident = raw.get("account_id")
        if type(ident) is not str or not re.fullmatch(r"[1-9][0-9]{0,39}", ident) or raw.get("id") != f"act_{ident}":
            fail("META_DISCOVERY_ACCOUNT_ID")
        currency = raw.get("currency")
        status = raw.get("account_status")
        if currency is not None:
            workspace.pattern(currency, r"[A-Z]{3}", "META_DISCOVERY_CURRENCY")
        if status is not None and (type(status) is not int or not 0 <= status <= 999):
            fail("META_DISCOVERY_STATUS")
        projected = dict(account_id=ident, currency=currency, account_status=status)
        if raw.get('name'):
            projected['name'] = _account_name(raw['name'])
        result.append(projected)
    paging = body.get("paging", {})
    if type(paging) is not dict:
        fail("META_DISCOVERY_CURSOR")
    if not paging.get("next"):
        return result, None
    cursors = paging.get("cursors")
    after = cursors.get("after") if type(cursors) is dict else None
    if type(after) is not str or not CURSOR.fullmatch(after):
        fail("META_DISCOVERY_CURSOR")
    return result, after


def discover_pages(get):
    """Never follow Meta's next URL. Send only validated cursors to the fixed edge."""
    accounts, seen, cursor, pages = {}, set(), None, 0
    error = None
    for _ in range(MAX_PAGES):
        params = {"fields": "id,account_id,name,currency,account_status", "limit": PAGE_SIZE}
        if cursor is not None:
            params["after"] = cursor
        try:
            rows, following = _page(get("/v27.0/me/adaccounts?" + urlencode(params)))
            pending = {}
            for row in rows:
                ident = row["account_id"]
                prior = pending.setdefault(ident, row)
                if prior != row or (ident in accounts and accounts[ident] != row):
                    fail("META_DISCOVERY_ACCOUNT_CHANGED")
            accounts.update(pending)
            pages += 1
            if following is None:
                break
            if following in seen:
                fail("META_DISCOVERY_CURSOR")
            seen.add(following)
            cursor = following
        except InputError as exc:
            error = str(exc) if str(exc) in DISCOVERY_ERRORS else "META_DISCOVERY_FAILED"
            break
        except KeyboardInterrupt:
            error = "META_DISCOVERY_INTERRUPTED"
            break
    else:
        error = "META_DISCOVERY_PAGE_LIMIT"
    state = "COMPLETE" if error is None else "PARTIAL" if pages else "FAILED"
    return dict(state=state, accounts=list(accounts.values()), pages_read=pages, error_code=error)


def status(directory, record_directory=None, *, route=None):
    directory = checked_path(directory, link_error="WORKSPACE_PATH_LINK", volume_error="WORKSPACE_VOLUME")
    if not directory.exists():
        return _guidance("INITIALIZE_WORKSPACE", route=route)
    profile = workspace.read(directory, existing_only=True)
    onboarding = profile.get("connection_onboarding", {})
    route = route or onboarding.get("route") or ("user-graph" if profile["app_id"] else None)
    identifier = profile["defaults"]["ACCOUNT"]
    record_directory = record_directory or directory / ('system-credential-record' if route=='system-user' else 'credential-record')
    route_status = None
    if route is None:
        step = "CHOOSE_ROUTE"
    elif route == "host-connector":
        observation = onboarding.get('discovery')
        if observation and any(row['account_id']==identifier for row in observation['accounts']):
            step = 'HOST_ACCOUNT_READY'
        elif observation and observation['accounts']:
            step = 'SELECT_ACCOUNT'
        else:
            step = 'CONNECT_IN_OFFICIAL_HOST'
    elif route == "system-user":
        from .system_user_read import credential_records

        if not profile['app_id'] or not profile['business_id']:
            step = 'PREPARE_APP'
        else:
            try:
                record, _, _ = credential_records(directory,record_directory,profile)
            except InputError:
                step = 'SYSTEM_USER_CONFIGURATION'
            else:
                observation = onboarding.get('discovery')
                if not observation or observation['record_sha256'] != digest(record):
                    step = 'DISCOVER_SYSTEM_ACCOUNTS'
                elif any(row['account_id']==identifier for row in observation['accounts']):
                    step = 'SYSTEM_USER_READ_READY'
                elif observation['accounts']:
                    step = 'SELECT_ACCOUNT'
                else:
                    step = 'NO_OBSERVED_ACCOUNTS'
    elif not profile["app_id"] or not profile["business_id"]:
        step = "PREPARE_APP"
    elif identifier and profile["binding"]:
        route_status = workspace.health_check(directory, "ads-analysis", record_directory=record_directory, existing_only=True)
        step = "RECONNECT" if route_status["status"] in ("AUTH_REQUIRED", "PERMISSION_REQUIRED") else "REUSE_WORKSPACE"
        if route_status["reason"] in ("VERIFY_NEW_ACCOUNT_ONLY", "RECONFIRM_TARGET_AND_ACCESS", "CHECK_THIS_ASSET"):
            step = "CHECK_NAMED_ACCOUNT"
        elif route_status["reason"] == "REVIEW_ADAPTER_OR_PROFILE_VERSION":
            return _guidance("RECONNECT", route=route, route_check=route_status, account_id=identifier)
    elif identifier:
        step = "CHECK_NAMED_ACCOUNT"
    else:
        try:
            record = read_record(record_directory, existing_only=True)
            if record["app_id"] != profile["app_id"] or record["state"] != "PREPARED":
                fail("CREDENTIAL_RECORD_BINDING_MISMATCH")
        except InputError:
            step = "ENROLL_USER_REFERENCE"
        else:
            observation = onboarding.get("discovery")
            if observation and observation["record_sha256"] == digest(record):
                if observation["accounts"]:
                    step = "SELECT_ACCOUNT"
                elif observation["state"] == "COMPLETE":
                    step = "NO_OBSERVED_ACCOUNTS"
                else:
                    step = "DISCOVER_ACCOUNTS"
            else:
                step = "DISCOVER_ACCOUNTS"
    return _guidance(step, route=route, account_id=identifier, discovery=onboarding.get("discovery"),
                     route_check=route_status, app_id=profile["app_id"], business_id=profile["business_id"],
                     host_permission_verified_locally=False if route=='host-connector' else None)


def _guidance(step, **details):
    command, instructions = STEPS[step]
    return dict(status="META_CONNECTION_GUIDANCE", step=step, next_command=command, instructions=instructions,
                official_sources=REFERENCE, read_authorized=False, write_authorized=False, **details)


def configure(directory, route, app_id, business_id):
    def change(profile):
        for key, value in (("app_id", app_id), ("business_id", business_id)):
            if value is not None:
                workspace.numeric(value)
                if profile[key] not in (None, value):
                    fail("WORKSPACE_EXISTING_MAPPING_CHANGED")
                profile[key] = value
        prior = profile.get("connection_onboarding", {})
        selected = route or prior.get("route") or "user-graph"
        discovery = prior.get("discovery") if prior.get("route") == selected else None
        profile["connection_onboarding"] = dict(route=selected, discovery=discovery)
    workspace.update(directory, change)


def _scope(profile):
    return tuple(profile[key] for key in ("client_id", "app_id", "business_id")) + (
        profile.get("connection_onboarding", {}).get("route", "user-graph"),)


def discover(directory, record_directory, proof_not_required, *, clock=utcnow):
    start = workspace.read(directory, existing_only=True)
    route = _scope(start)[-1]
    if not proof_not_required:
        return _guidance("REVIEW_APP_PROOF", route=route)
    if route == 'system-user':
        from .system_user_read import scoped_session

        with scoped_session(directory,start,None,digest(_scope(start)),'LIST AD ACCOUNTS',
                            record_directory,clock,discovery=True) as (fetch, fingerprint):
            result = discover_pages(fetch)
        observation = dict(result, observed_at=clock().isoformat(), record_sha256=fingerprint)

        def save_system(profile):
            from .system_user_read import credential_records

            primary, _, _ = credential_records(directory,record_directory,profile)
            if _scope(profile) != _scope(start) or digest(primary)!=fingerprint:
                fail('WORKSPACE_CHANGED_DURING_CHECK')
            profile['connection_onboarding'] = dict(route=route,discovery=observation)

        workspace.update(directory,save_system)
        return status(directory,record_directory)
    app = start["app_id"]
    if app is None or start["business_id"] is None or _scope(start)[-1] != "user-graph":
        fail("META_DISCOVERY_USER_ROUTE_REQUIRED")
    record = read_record(record_directory, existing_only=True)
    if record["app_id"] != app:
        fail("WORKSPACE_CONNECTION_RECORD_APP_MISMATCH")
    record_hash = digest(record)
    intent = dict(app_id=app, principal_id=record["principal_id"], credential_reference=record["reference"])
    guard = RecordGuard(record_directory, intent, record_hash)
    guard.check(existing_only=True)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        fail("CONNECTION_INTERACTIVE_TERMINAL_REQUIRED")
    phrase = f"LIST AD ACCOUNTS WITH APP {app} FOR {record['principal_id']}"
    print(f"当前范围为一次身份核验和最多 {MAX_PAGES} 页只读账户查询。不会改变 Meta 资产。")
    if input(f"确认以上范围，请输入 {phrase}：") != phrase:
        fail("CONNECTION_NOT_CONFIRMED")
    started = clock()
    deadline = started + timedelta(minutes=5)
    binding = ReadBinding(digest(intent), app, record["principal_id"], started.isoformat(),
                          deadline.isoformat(), deadline.isoformat(), (), False, False, False)

    def checkpoint():
        guard.check(existing_only=True)
        if _scope(workspace.read(directory, existing_only=True)) != _scope(start):
            fail("WORKSPACE_CHANGED_DURING_CHECK")
        if not started <= clock() < deadline:
            fail("CONNECTION_REVIEW_EXPIRED")

    with NativeCredentialBroker(record["reference"], binding, enabled=True).lease(binding) as credential:
        checkpoint()
        debug = _debug(credential, checkpoint, clock=clock, time_remaining=lambda: (deadline-clock()).total_seconds())
        metadata = verified_metadata(debug, intent, clock(), deadline)

        def get(path):
            if clock() >= aware_time(metadata["effective_read_expires_at"]):
                fail("CONNECTION_EXPIRED_OR_CLOCK_SKEW")
            return _get_connection_json(path, credential, checkpoint, clock=clock,
                                        time_remaining=lambda: (deadline-clock()).total_seconds())

        result = discover_pages(get)
        checkpoint()
    credential = None
    observation = dict(result, observed_at=clock().isoformat(), record_sha256=record_hash)

    def save(profile):
        if _scope(profile) != _scope(start):
            fail("WORKSPACE_CHANGED_DURING_CHECK")
        guard.check(existing_only=True)
        profile["connection_onboarding"] = dict(route="user-graph", discovery=observation)

    workspace.update(directory, save)
    return status(directory, record_directory)


def select(directory, record_directory, identifier, evidence):
    workspace.pattern(evidence, r"EV_[A-Z0-9_]{1,80}", "WORKSPACE_OWNERSHIP_REFERENCE")
    key = workspace.asset_key("ACCOUNT", identifier)

    def choose(profile):
        onboarding = profile.get("connection_onboarding", {})
        observed = onboarding.get("discovery") or {}
        if onboarding.get('route')!='host-connector':
            record = read_record(record_directory, existing_only=True)
            RecordGuard(record_directory, dict(app_id=profile["app_id"], principal_id=record["principal_id"],
                                              credential_reference=record["reference"]), digest(record)).check(existing_only=True)
            if observed.get('record_sha256') != digest(record):
                fail("META_CONNECT_ACCOUNT_NOT_DISCOVERED")
        if not any(row["account_id"] == identifier for row in observed.get("accounts", [])):
            fail("META_CONNECT_ACCOUNT_NOT_DISCOVERED")
        mapping = dict(kind="ACCOUNT", id=identifier, ownership_evidence_ref=evidence)
        current = profile["assets"].get(key)
        if current is not None and current != mapping:
            fail("WORKSPACE_EXISTING_MAPPING_CHANGED")
        profile["assets"][key] = mapping
        profile["defaults"]["ACCOUNT"] = identifier

    workspace.update(directory, choose)
    return status(directory, record_directory)


def import_host_receipt(directory,path):
    from .operator_review import load_input

    document = load_input(path)
    fields = ('schema','source','observed_at','accounts','coverage','pages_read')
    object_only(document,fields,fields)
    if document['schema']!='meta-host-account-list-v1' or document['coverage'] not in ('COMPLETE','PARTIAL'):
        fail('META_CONNECT_HOST_SOURCE')
    if aware_time(document['observed_at'])>utcnow():
        fail('META_CONNECT_HOST_SOURCE')
    result = dict(observed_at=document['observed_at'],record_sha256=digest(document),state=document['coverage'],
                  pages_read=document['pages_read'],accounts=document['accounts'],
                  error_code=None if document['coverage']=='COMPLETE' else 'META_DISCOVERY_PAGE_LIMIT',
                  host_source=document['source'])
    state = dict(route='host-connector',discovery=result)
    validate_state(state)

    def save(profile):
        if profile.get('connection_onboarding',{}).get('route')!='host-connector':
            fail('META_CONNECT_HOST_ROUTE_REQUIRED')
        profile['connection_onboarding'] = state

    workspace.update(directory,save)
    return status(directory)


def add_command(sub):
    command = sub.add_parser("workspace-connect", help="按工作区状态指引三条 Meta 接入与续接路线",
        description="只传 --workspace 查看当前步骤。先用 --start 初始化；按 CUSTOMER_SOP.md 在官方界面完成所需授权。")
    command.add_argument("--workspace", type=Path, required=True, help="同一客户的工作区目录")
    command.add_argument("--credential-record-dir", type=Path, help="可选的非秘密登记目录；不会接收令牌")
    command.add_argument("--start", action="store_true", help="仅首次创建本地工作区")
    command.add_argument("--route", choices=ROUTES, help="自有 App、官方宿主连接器或企业系统用户")
    command.add_argument("--app-id", help="从官方控制台核对的 App ID")
    command.add_argument("--business-id", help="从业务设置核对的业务资产组合 ID")
    actions = command.add_mutually_exclusive_group()
    actions.add_argument("--discover", action="store_true", help="经本人交互确认后发现自有 App 或系统用户可读账户")
    actions.add_argument("--select-account", help="选择已被当前路线观察到的广告账户纯数字 ID")
    actions.add_argument("--host-receipt",type=Path,help="导入官方宿主提供的去标识账户清单")
    command.add_argument("--proof-not-required", action="store_true", help="仅在客户核实所选 App 无此要求时使用")
    command.add_argument("--ownership-evidence")


def run(args):
    directory = args.workspace
    if (args.ownership_evidence is None) != (args.select_account is None):
        fail("META_CONNECT_SELECTION_EVIDENCE_REQUIRED")
    if args.proof_not_required and not args.discover:
        fail("META_CONNECT_UNUSED_PROOF")
    if args.start:
        workspace.initialize(directory)
    if args.route or args.app_id or args.business_id:
        configure(directory, args.route, args.app_id, args.business_id)
    record_directory = args.credential_record_dir
    if (args.discover or args.select_account) and record_directory is None:
        route = _scope(workspace.read(directory, existing_only=True))[-1]
        record_directory = directory/('system-credential-record' if route=='system-user' else 'credential-record')
    if args.discover:
        return discover(directory, record_directory, args.proof_not_required)
    if args.select_account:
        return select(directory, record_directory, args.select_account, args.ownership_evidence)
    if args.host_receipt:
        return import_host_receipt(directory,args.host_receipt)
    return status(directory, record_directory)
