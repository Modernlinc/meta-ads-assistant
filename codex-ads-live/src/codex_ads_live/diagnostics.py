"""Case-derived metadata triage, not a connection or authorization check.

Only categorical metadata is accepted. Never accept credentials, names,
account IDs, screenshots, raw API responses, or customer comments here.
"""
import re
from .validation import aware_time, fail, finite_number, object_only

BOOL_FIELDS = (
    "app_binding_matches", "asset_id_matches", "asset_owner_verified",
    "route_id_differs", "mcp_write_tools_allowed", "mcp_connected",
    "business_2fa_required", "business_passkey_required", "security_recommendations_present",
    "verification_continue_changed_state", "api_read_succeeded",
    "ui_zero_test_count", "ui_contains_drafts", "live_fixture_available", "admin_copy_conflict",
    "display_name_differs",
)
ENUM_FIELDS = {
    "identity_name_check": ("MATCH", "MISMATCH", "UNKNOWN"),
    "app_publication": ("PUBLISHED", "NOT_PUBLISHED", "UNKNOWN"),
    "verification_state": ("NOT_STARTED", "DRAFT", "MORE_INFO", "SUBMITTED", "VERIFIED", "UNKNOWN"),
    "asset_kind": ("PAGE", "AD_ACCOUNT", "PERSONAL_PROFILE", "PROFESSIONAL_PROFILE", "INSTAGRAM_LIVE", "UNKNOWN"),
    "ui_numeric_comparison": ("MATCH", "MISMATCH", "NOT_COMPARABLE", "NOT_RUN"),
}
FIELDS = {"schema_version", "as_of", "authentication_expires_at", "granted_scopes", "data_result", *BOOL_FIELDS, *ENUM_FIELDS}


def validate_metadata(item):
    object_only(item, FIELDS, ("schema_version", "as_of"))
    if type(item["schema_version"]) is not int or item["schema_version"] != 1:
        fail("SCHEMA_VERSION_UNSUPPORTED")
    aware_time(item["as_of"])
    for field in BOOL_FIELDS:
        if field in item and item[field] is not None and type(item[field]) is not bool:
            fail("EXPECTED_BOOLEAN_OR_NULL")
    for field, options in ENUM_FIELDS.items():
        if field in item and item[field] not in options:
            fail("INVALID_ENUM")
    expiry = item.get("authentication_expires_at")
    if expiry is not None:
        aware_time(expiry)
    scopes = item.get("granted_scopes")
    if scopes is not None:
        if type(scopes) is not list or len(scopes) > 64:
            fail("INVALID_SCOPE_LIST")
        if any(type(s) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", s) for s in scopes):
            fail("INVALID_SCOPE_LIST")
        if len(set(scopes)) != len(scopes):
            fail("DUPLICATE_SCOPE")
    if "data_result" in item:
        record = item["data_result"]
        object_only(record, ("state", "value"), ("state",))
        if record["state"] not in ("VALUE", "EMPTY", "MISSING", "ERROR", "NOT_RUN"):
            fail("INVALID_DATA_STATE")
        if record["state"] == "VALUE":
            if "value" not in record or not finite_number(record["value"]):
                fail("VALUE_REQUIRES_FINITE_NUMBER")
        elif "value" in record:
            fail("NON_VALUE_MUST_OMIT_VALUE")
    # A successful empty response cannot simultaneously be a request failure.
    state = item.get("data_result", {}).get("state")
    if (state in ("VALUE", "EMPTY") and item.get("api_read_succeeded") is False) or (state == "ERROR" and item.get("api_read_succeeded") is True):
        fail("CONTRADICTORY_READ_RESULT")
    if item.get("ui_numeric_comparison") in ("MATCH", "MISMATCH") and state != "VALUE":
        fail("NUMERIC_COMPARISON_REQUIRES_VALUE")
    return item


def diagnose(item):
    validate_metadata(item)
    findings = []
    stops = []
    pending = []

    def add(case, status, fact, inference, cannot_infer, next_step, owner, authorization, evidence):
        findings.append(dict(case_id=case, status=status, observed_metadata=fact,
                             interpretation=inference, cannot_infer=cannot_infer,
                             next_step=next_step, owner=owner, authorization_gate=authorization,
                             acceptance_evidence=evidence,
                             maturity="INTERNAL_CASE_DERIVED / REUSABLE_CANDIDATE"))

    expiry = item.get("authentication_expires_at")
    if expiry is None:
        pending.append("AUTHENTICATION_EXPIRY_UNKNOWN")
    else:
        expired = aware_time(expiry) <= aware_time(item["as_of"])
        if expired:
            stops.append("AUTHENTICATED_READ")
        add("F01", "EXPIRED" if expired else "VALID_AT_SUPPLIED_TIME_ONLY",
            "提供的到期时间不晚于观察时间" if expired else "提供的到期时间晚于观察时间",
            "停止该认证会话" if expired else "仅元数据尚未到期，仍需端点核验",
            "不证明账户被封、长期认证可用或连接成功", "由本人重新授权并核元数据" if expired else "实际读取时重新检查期限和绑定",
            "资产负责人", "不自动续期或扩权", "非密钥App/type/期限/scope及最小GET")
    if item.get("app_binding_matches") is not True:
        pending.append("APP_BINDING_UNVERIFIED")
        stops.append("AUTHENTICATED_READ")
    if item.get("asset_id_matches") is not True or item.get("asset_owner_verified") is not True:
        pending.append("ASSET_ID_OR_OWNERSHIP_UNVERIFIED")
        stops.append("ASSET_READ")
    scopes = item.get("granted_scopes")
    if scopes is None:
        pending.append("SCOPES_UNKNOWN")
    elif "ads_management" in scopes:
        add("F02", "MINIMUM_SCOPE_NOT_PROVEN", "scope含广告管理权限", "GET-only与凭据最小权限分别验收",
            "不能推断发生写入，也不能认定所有读取权限满足", "批准后比较独立最小scope会话", "资产负责人及实施方",
            "不自动撤销业务授权或新建认证", "独立会话的scope与同端点结果")
    if item.get("mcp_write_tools_allowed") is True:
        add("F03", "CONFIGURATION_REVIEW", "MCP配置允许写工具", "接入前需核写限制",
            "配置不证明已经连接或执行", "检查工具白名单与写入负面测试", "开发负责人及资产负责人",
            "连接及变更开关另授权", "工具合同、只读策略和阻止写入测试")
    if item.get("business_2fa_required") is False or item.get("business_passkey_required") is False:
        add("F04", "POLICY_REVIEW", "至少一项企业登录策略未强制", "需结合人员和恢复安排评估",
            "不能推断个人2FA关闭、被入侵或无恢复能力", "负责人核策略与恢复安排", "业务管理员",
            "变更策略另授权", "业务策略与本人核验结论，不收验证码")
    if item.get("security_recommendations_present") is True:
        add("F05", "CONTEXT_REVIEW", "平台存在建议", "需要业务用途判断",
            "不等于故障，不应为清提示关闭测试账户", "逐项说明处理或保留例外", "业务负责人",
            "资产及人员变更另授权", "建议处置依据、责任人和结果")
    if item.get("identity_name_check") == "MISMATCH":
        add("F06", "OWNER_IDENTITY_CHECK", "主体核对元数据不一致", "尚不能认定哪份名称正确",
            "应用昵称可不同；不能从标题判定法律主体错误", "本人对照法定材料，仅回传匹配结论", "主体负责人",
            "不收全证照，修改与提交分别授权", "核验时间、责任人、匹配结论")
    elif item.get("identity_name_check") == "MATCH" and item.get("display_name_differs") is True:
        add("F06", "LEGAL_NAME_MATCH_DISPLAY_DIFFERENCE_ONLY", "本人已核法定名匹配，展示名不同", "展示名称差异本身不是主体材料错误",
            "不证明其他地址/电话/邮编一致，也不代表审核通过", "保留核验与提交回执；按当前审核状态接续，不重复更名或提交",
            "主体负责人及原经办", "无新证据不自动更改验证资料", "本人名称核验结论、当前平台状态及其余独立缺口")
    if item.get("verification_continue_changed_state") is True:
        add("F07", "STATEFUL_UI_CASE", "经办报告继续按钮改变状态", "该向导可能有状态写入",
            "不能推广为所有继续按钮都写入，不能证明撤回或删除", "保存前后状态，不为复现再次进入", "原经办",
            "进入或改变验证流程先核具体目标", "操作日志和前后状态，二手报告注明来源")
    if item.get("route_id_differs") is True or item.get("asset_id_matches") is False:
        add("F08", "STOP_ID_MISMATCH" if item.get("asset_id_matches") is False else "ROUTE_IS_NOT_CANONICAL_ID",
            "规范编号不一致" if item.get("asset_id_matches") is False else "路由参数与规范编号不同",
            "需要规范ID文本和所有权联合核验", "仅路由差异不证明跨账户；ID匹配不代替归属",
            "对比可见编号与响应ID并核归属", "实施方", "仅具名授权范围只读", "规范ID、响应身份、归属三项证据")
    if item.get("ui_zero_test_count") is True and item.get("api_read_succeeded") is True:
        add("F09", "COUNTER_NOT_REQUEST_RESULT", "API成功且页面测试数为0", "两个证据层应分别记录",
            "0不证明从未调用；页面延迟说明不是所有端点SLA", "核App绑定与各自观察时间", "实施方",
            "不刷调用次数", "具名请求结果和计数观察分列")
    state = item.get("data_result", {}).get("state", "NOT_RUN")
    if state != "VALUE" or item.get("ui_contains_drafts") is True or item.get("ui_numeric_comparison") != "MATCH":
        add("F10", "NUMERIC_NOT_VALIDATED", "数据状态：" + state, "保持值/空/缺失/错误/未运行区分",
            "EMPTY不是0或权限错误；草稿数不是API对象数；明确0仍是VALUE",
            "有自然数据时按日期时区币种层级归因对账", "分析实施方", "不启动广告造数据",
            "同口径数值和容差说明；空值保留限制")
    if item.get("live_fixture_available") is not True:
        pending.append("LIVE_FIXTURE_UNVERIFIED")
        add("F11", "NOT_RUN", "没有已确认的现成直播样本", "直播读取尚不能验收",
            "不证明权限错误；历史直播不能证明实时并发", "有具名自有对象后核Page归属/状态/逐指标时间", "资产负责人及实施方",
            "不自动创建直播或扩权", "对象归属、评论分页和逐指标结果")
    if item.get("admin_copy_conflict") is True:
        add("F12", "ROLE_EVIDENCE_REQUIRED", "管理员页面文案矛盾", "实际人数和角色未由文案证明",
            "资产全权人数不等于业务管理员人数", "核实际业务角色及恢复安排", "业务管理员",
            "邀请和撤销另授权", "脱敏角色数量及核验结论")
    if item.get("asset_kind") in ("PERSONAL_PROFILE", "PROFESSIONAL_PROFILE", "INSTAGRAM_LIVE"):
        stops.append("UNSUPPORTED_LIVE_TARGET")
    # Publication, verification and actual grants intentionally stay separate.
    return dict(schema_version=1, mode="LOCAL_METADATA_DIAGNOSIS", as_of=item["as_of"],
                connection_validated=False, live_action_authorized=False,
                status="STOP_AFFECTED_STEP" if stops else "EVIDENCE_REVIEW_REQUIRED",
                stop_scopes=sorted(set(stops)), missing_evidence=sorted(set(pending)),
                findings=findings, platform_state={k: item.get(k, "UNKNOWN") for k in ("app_publication", "verification_state")})
