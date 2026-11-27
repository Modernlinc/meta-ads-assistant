# Meta 账户接入与业务准备

## 当前候选的客户路径

工作区区分自有 App、已配置官方宿主和企业系统用户。原生读取严格按所选路线，宿主账户可留存本地草案。测量配置和自定义购买事件接入同一准备任务。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C01 | 客户工作区、品牌与商品档案 | `workspace-connect → workspace-refresh / native-inventory` / `method → workspace-method-record → media-buyer prepare` / `media-analyze → creative-compare → media-buyer prepare/learn` | A01 A02 A03 |
| C02 | Meta App/Business Portfolio/Ad Account/Page接入 | `workspace-connect → workspace-refresh / native-inventory` / `workspace-method-record / page-live-contract-check / page-live-export-review` | A01 A02 A17 |
| C03 | 权限/资产/账户准备度诊断 | `workspace-connect → workspace-refresh / native-inventory` / `tracking-prepare → media-buyer prepare` | A01 A02 A06 |
| C04 | Pixel/CAPI/UTM/事件/归因追踪审计 | `tracking-prepare → media-buyer prepare` | A06 |
| C05 | 商品/Offer/单位经济/落地页准备度 | `method → workspace-method-record → media-buyer prepare` / `media-buyer prepare/plan → workspace-first-launch-progress` | A03 A07 |
| C06 | 客户洞察/VOC/多流派策略 | `method → workspace-method-record → media-buyer prepare` / `public-ad-query → workspace-public-ad-observations → workspace-creative-research` / `media-analyze → creative-compare → media-buyer prepare/learn` | A03 A04 A05 |
| C20 | Pixel/CAPI/GTM事件诊断 | `tracking-prepare → media-buyer prepare` / `native-insights → media-buyer review/optimize → workspace-first-launch-update` | A06 A13 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

将客户具名资产、本机工作区和经营准备关联，便于首次核对及后续接续。

能力 ID C01、C02、C03、C04、C05、C17、C20、C29。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「核对具名广告账户权限和商品试投缺项。」

App、Business、Ad Account、Page 编号、归属及授权；商品、落地页、Offer、市场、币种和已知成本。

```text
meta-access-review --input codex-ads-live/fixtures/meta-access.example.json
```

## 流程和专业依据

初始化或恢复工作区 → 核对人员与资产 → 预览范围 → 本人确认后具名读取 → 业务证据审阅。

**官方事实或工具合同**　App Review、Business Verification、访问层和资产角色分开核查。经营准备保留缺项和冲突。具名读取用满足 scope 的凭据引用。 [Meta 访问层](https://developers.facebook.com/documentation/development/graph-api/overview/access-levels) · [应用审核](https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/app-review)

**从业者方法与客户业务规则**　从业者准备清单用于确定补件顺序，企业验证或访问层状态只接受官方回执。客户规则要求原工作区、App 和资产范围一致。

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C01 | media-buyer prepare/status,workspace-route | [media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288)、[client_workspace.py:health_check](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L624)、[client_workspace.py:prepare_business](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L1084)、[creative_research.py:run_research](../../codex-ads-live/src/codex_ads_live/creative_research.py#L297) | 原工作区、品牌/商品、原 case → 同一项目和计划指纹；冲突拒绝 | EV-E01，EV-E02，EV-F01。case 与 plan_sha256 接续，未重复建单。 |
| C02 | meta-access-review,workspace-bind,workspace-refresh,workspace-page-preview | [client_workspace.py:health_check](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L624)、[connection.py](../../codex-ads-live/src/codex_ads_live/connection.py)、[platform_access.py:review](../../codex-ads-live/src/codex_ads_live/platform_access.py#L29)、[connection.py:check_connection](../../codex-ads-live/src/codex_ads_live/connection.py#L295)、[connection.py:verified_metadata](../../codex-ads-live/src/codex_ads_live/connection.py#L273)、[native_credentials.py:NativeCredentialBroker](../../codex-ads-live/src/codex_ads_live/native_credentials.py#L75) | App、Business、账户、Page 编号和角色 → 具名读取、权限分离、时区；错误 ID/缺授权拒绝 | EV-E04，EV-E05，EV-E06，EV-F02。模拟回包验证身份及错误路径；真实授权留待验证。 |
| C03 | meta-access-review,meta-guidance-review,workspace-onboarding-report | [platform_access.py:review](../../codex-ads-live/src/codex_ads_live/platform_access.py#L29)、[meta_guidance.py:review](../../codex-ads-live/src/codex_ads_live/meta_guidance.py#L21) | 用途、应用模式、资产角色和当前已知访问层 → 权限条件和待核资格；旧教程冲突标记 | EV-E04，EV-E05，EV-E10，EV-F02。旧教程冲突另标，账户后台资格需实证。 |
| C04 | media-buyer prepare,tracking-audit | [operator_review.py:tracking_audit](../../codex-ads-live/src/codex_ads_live/operator_review.py#L327)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 落地页、UTM、同事件 Pixel/CAPI 和归因声明 → 缺 ID/重叠/未成对；测试与生产分列 | EV-E31，EV-F01。合成事件核对；不发送生产事件。 |
| C05 | media-buyer prepare,business-readiness,partner-brief-review | [operator_review.py:business_readiness](../../codex-ads-live/src/codex_ads_live/operator_review.py#L64)、[partner_brief.py:review](../../codex-ads-live/src/codex_ads_live/partner_brief.py#L32) | 商品、Offer、成本与落地页证据 → 保留冲突、缺失或停止项，按已知资料核算 | EV-R1-C05，EV-F01。事实相互矛盾时避免计算利润。 |
| C17 | multi-account-plan,multi-account-review,account-inventory-review | [multi_account_review.py](../../codex-ads-live/src/codex_ads_live/multi_account_review.py)、[multi_account_review.py:summarize](../../codex-ads-live/src/codex_ads_live/multi_account_review.py#L65) | 同工作区具名账户和各自归档 → 按币种/时区分组；错工作区拒绝 | EV-E27，EV-E28。多账户明确具名，未自动查第三方账户。 |
| C20 | tracking-audit,media-buyer prepare | [operator_review.py:tracking_audit](../../codex-ads-live/src/codex_ads_live/operator_review.py#L327) | 浏览器/服务端事件 ID 和环境 → 疑似重复、缺 ID、测试和生产区分 | EV-E31，EV-F01。生产流量不能用测试事件代替。 |
| C29 | install.py,preflight,workspace-backup,workspace-restore | [install.py](../../codex-ads-live/install.py)、[runtime.py](../../codex-ads-live/src/codex_ads_live/runtime.py)、[MEDIA_BUYER.md](../../codex-ads-live/references/MEDIA_BUYER.md)、[onboarding.py:preflight](../../codex-ads-live/src/codex_ads_live/onboarding.py#L42)、[onboarding.py:windows_preflight](../../codex-ads-live/src/codex_ads_live/onboarding.py#L31)、[client_workspace.py:restore](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L846)、[WINDOWS_CLIENT.md](../../codex-ads-live/references/WINDOWS_CLIENT.md)、[INSTALL_RUN.md](../../codex-ads-live/references/INSTALL_RUN.md) | 安装件、项目、升级预演、非秘密备份 → Mac 新装/升级/恢复；Win 原生及桌面待验 | EV-E01，EV-E02，EV-E03，EV-E42，EV-F04。180 项载荷与 138 命令；Windows 原生 NOT_RUN_NO_WINDOWS_HOST。 |

**现成输入输出示例**　用 meta-access.example.json 提交 ADS_REPORT 及未知访问层。正常本机结果 META_PREREQUISITES_REVIEW，动作未授权。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_windows_customer_compatibility.py。[已有测试文件](../../tests/test_windows_customer_compatibility.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

证明本机准备、权限检查结构和合成响应约束。App 创建、授权、真实账户读回和持续可用分别验收。

Meta v27.0；账户和 Page 各自授权。本地检查不配置花费；实际资格依账户、国家和应用用途。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　缺角色或令牌时保留本地权限预览；已授权报告可走本地复盘。
