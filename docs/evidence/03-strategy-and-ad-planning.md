# 投放策略与广告计划

## 当前候选的客户路径

支持 NEW、同账户 APPEND、经核对的 REBUILD，同时支持先建暂停结构后补素材。ABO/CBO 的预算层与开始停止时间写入同一对象图。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C11 | Campaign结构/受众/版位/预算/测试 | `media-buyer prepare/plan → workspace-first-launch-progress` / `native-audience / catalog-review / ad-policy-preflight → media-buyer prepare` | A07 A11 |
| C12 | Campaign/AdSet/Creative/Ad创建 | `media-buyer prepare/plan → workspace-first-launch-progress` / `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `workspace-first-launch-activation / native-rules` | A07 A08 A09 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

把经营事实、备选策略和预算上限整理成可校对的广告计划，覆盖 Campaign、Ad Set、Creative、Ad 四类对象。

能力 ID C01、C04、C05、C06、C10、C11、C30。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「按当前 Offer、毛利、目标和样本对照 ABO 与 CBO，先预览计划。」

商品、Offer、落地页、市场、账户、主页、转化目标、追踪、可用历史和预算护栏。

```text
knowledge-search --topic strategy
```

## 流程和专业依据

核对业务与归因 → 比较策略与样本条件 → 生成并预填四类对象草案 → Codex 按已审核输入补齐字段 → 核查草案、预算和依赖 → 具名审批 → 条件满足后 PAUSED 创建及回读。

**官方事实或工具合同**　对象和字段按 Meta 参考。ABO/CBO 和 Broad/Interest 可比较适用条件。成本和盈利以客户已知数据计算。 [Marketing API](https://developers.facebook.com/docs/marketing-api/reference/v27.0/) · [整理观点及来源](../../codex-ads-live/references/operator-knowledge.json)

**从业者方法与客户业务规则**　ABO/CBO、Broad/Interest 条件化比较。可接受成本、归因口径和取样期由客户材料给出。

Campaign → Ad Set → Ad 构成父子链，Ad 引用 Creative。多素材批次共享 Campaign 和 Ad Set，每条素材各有对应的 Creative 和 Ad 草案。

计划命令先生成并预填本地草案。Codex 按已审核的商品、预算、受众和文案补齐剩余字段，再用 `check_drafts`（`workspace-first-launch-check`）检查。具名审批和受管条件满足后才进入 PAUSED 创建及回读。草案预览、平台创建、启用投递及效果分别核对。

ABO/CBO 可作策略比较；本候选首投写入限 Ad Set（ADSET）日预算，Campaign 层预算无当前首投写入路径。

[客户首投操作步骤](../../codex-ads-live/references/FIRST_LAUNCH_CUSTOMER.md)

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C01 | media-buyer prepare/status,workspace-route | [media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288)、[client_workspace.py:health_check](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L624)、[client_workspace.py:prepare_business](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L1084)、[creative_research.py:run_research](../../codex-ads-live/src/codex_ads_live/creative_research.py#L297) | 原工作区、品牌/商品、原 case → 同一项目和计划指纹；冲突拒绝 | EV-E01，EV-E02，EV-F01。case 与 plan_sha256 接续，未重复建单。 |
| C04 | media-buyer prepare,tracking-audit | [operator_review.py:tracking_audit](../../codex-ads-live/src/codex_ads_live/operator_review.py#L327)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 落地页、UTM、同事件 Pixel/CAPI 和归因声明 → 缺 ID/重叠/未成对；测试与生产分列 | EV-E31，EV-F01。合成事件核对；不发送生产事件。 |
| C05 | media-buyer prepare,business-readiness,partner-brief-review | [operator_review.py:business_readiness](../../codex-ads-live/src/codex_ads_live/operator_review.py#L64)、[partner_brief.py:review](../../codex-ads-live/src/codex_ads_live/partner_brief.py#L32) | 商品、Offer、成本与落地页证据 → 保留冲突、缺失或停止项，按已知资料核算 | EV-R1-C05，EV-F01。事实相互矛盾时避免计算利润。 |
| C06 | media-buyer prepare,knowledge-search,strategy-review | [operator_v1.py:knowledge_search](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L130)、[operator_review.py:strategy_review](../../codex-ads-live/src/codex_ads_live/operator_review.py#L129) | 目标、关键词和商业证据 → 候选策略及适用条件，来源/观点/假设保留 | EV-E09，EV-E10，EV-E11，EV-F01，EV-F06。492 条索引和 13 条整理观点；有分页证据。 |
| C10 | workspace-creative-research,media-buyer prepare,creative-compare | [creative_research.py:freeze_selection](../../codex-ads-live/src/codex_ads_live/creative_research.py#L266)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 品牌、商品、草案证据和已登记素材 → 复用或对照创意 Brief、角度和变体 | EV-E12，EV-E13，EV-F01。同一创意变体进入本地草案。 |
| C11 | media-buyer prepare/plan,strategy-review,workspace-first-launch-check | [first_launch_plan.py:scoped_draft](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L196)、[operator_review.py:strategy_review](../../codex-ads-live/src/codex_ads_live/operator_review.py#L129)、[first_launch_plan.py:stage_specs](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L25)、[first_launch_plan.py:_draft](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L80)、[first_launch_plan.py:check](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L228)、[meta_write_mapping.py:blank_draft](../../codex-ads-live/src/codex_ads_live/meta_write_mapping.py#L54)、[media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288) | 目标、受众、版位、预算与样本假设 → 预填的四类对象草案及依赖；Codex 补齐后检查，拒绝范围漂移 | EV-E09，EV-E19，EV-F01。预算/落地页变更受校验；草案不生成平台对象。 |
| C30 | knowledge-search,workflow-guide,partner-brief-review | [operator_v1.py:knowledge_search](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L130)、[partner_brief.py:review](../../codex-ads-live/src/codex_ads_live/partner_brief.py#L32)、[operator-knowledge.json](../../codex-ads-live/references/operator-knowledge.json)、[operator_v1.py:operator_route](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L174) | 官方资料、观点、案例和独立模块合同 → 保留来源、适用、撤回及验证状态 | EV-E09，EV-E10，EV-E11，EV-E41，EV-E42，EV-E43，EV-F05，EV-F06。本机复验 492 条索引与 14 个具名交接角色。 |

**现成输入输出示例**　策略主题返回多种观点及使用条件。合成试投案例在原 case 中生成四类对象草案，按已审核输入补齐后检查。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_v1_customer_workflows.py。[已有测试文件](../../tests/test_v1_customer_workflows.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

本地计划与预算限制可证。实际投放、受众可用性及效果需要独立回执。

Marketing API v27.0；账户资格分开检查。草案、实际提交、预算安排属于不同阶段。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　资料不足时停在保守本地预览，补指标或预算后重审。
