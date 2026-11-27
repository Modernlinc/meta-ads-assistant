# 账户诊断、经营核算与持续复盘

## 当前候选的客户路径

支持四层原生 Insights、原异步任务恢复、具名对象和活动查询、店铺固定版本只读、同窗工作簿与正展示后的原素材学习。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C16 | 广告报表与分层读取 | `native-insights / native-inventory` | A12 |
| C17 | 多账户隔离与聚合 | `workspace-connect → workspace-refresh / native-inventory` / `native-insights / native-inventory` / `commerce-reconcile / media-buyer report` | A02 A12 A16 |
| C18 | 版位和投递诊断 | `native-insights / native-inventory` / `native-insights → media-buyer review/optimize → workspace-first-launch-update` | A12 A13 |
| C19 | 漏斗/样本/素材/Offer/LP表现诊断 | `native-insights → media-buyer review/optimize → workspace-first-launch-update` / `commerce-reconcile / media-buyer report` | A13 A15 |
| C23 | 本机Watchdog/调度/告警/日报周报 | `workspace-first-launch-activation / native-rules` / `commerce-reconcile / media-buyer report` | A11 A16 |
| C24 | Shopify GraphQL只读连接器 | `shopify-bind → shopify-read` | A14 |
| C25 | 订单/退款/成本/库存/净收入/贡献利润核算 | `shopify-bind → shopify-read` / `commerce-reconcile / media-buyer report` | A14 A15 |
| C26 | Facebook公共主页Live V1数据 | `workspace-method-record / page-live-contract-check / page-live-export-review` | A17 |
| C27 | 客户级报表/报告导出 | `native-insights → media-buyer review/optimize → workspace-first-launch-update` / `commerce-reconcile / media-buyer report` | A13 A16 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

把同一 Ad 的投递观察、经营数据、诊断和建议保持在可比范围。

能力 ID C01、C04、C16、C17、C18、C19、C20、C21、C22、C23、C24、C25、C26、C27、C28、C29、C30。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「按同一广告、时区与归因复盘两期，列出建议、反证和停止条件。」

同一 Ad、账户、时区、日期、币种、指标定义和归档；核算另需授权的订单、退款、成本和消耗。

```text
media-buyer review --workspace <工作区> --case <原Case> --input <时期归档JSON>
```

## 流程和专业依据

核对具名清单与归档覆盖 → 按来源核对本地广告日粒度切片、账户版位或同一 Ad 复盘 → 检查样本及漏斗 → 经营核算 → 保存提案或本机续跑。

**官方事实或工具合同**　官方 Insights 和 Shopify scope 定义数据来源。比较日期、币种、归因和覆盖。缺成本列未知；解释区分证据与反证。 [Insights](https://developers.facebook.com/docs/marketing-api/insights/) · [Shopify scopes](https://shopify.dev/docs/api/usage/access-scopes)

**从业者方法与客户业务规则**　对齐币种、日期、归因和样本，再看漏斗和反证。缺真实成本保留缺项。

`account-insights-slice` 在本地筛选和聚合已归档的广告日粒度数据，结果依据标为 `OBSERVED_AD_DAY_ROWS`。这类切片的口径区别于平台原生父级去重 Insights；缺失的 reach/frequency 不据此推算。

`account-inventory-review` 核对已捕获的具名对象、父子关系和覆盖。账户版位读取用独立的版位报告（`workspace-plan --task ads-placements`）；同一 Ad 复盘使用具名广告和可比观察窗。分别保留来源、日期、币种、时区及覆盖。

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C01 | media-buyer prepare/status,workspace-route | [media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288)、[client_workspace.py:health_check](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L624)、[client_workspace.py:prepare_business](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L1084)、[creative_research.py:run_research](../../codex-ads-live/src/codex_ads_live/creative_research.py#L297) | 原工作区、品牌/商品、原 case → 同一项目和计划指纹；冲突拒绝 | EV-E01，EV-E02，EV-F01。case 与 plan_sha256 接续，未重复建单。 |
| C04 | media-buyer prepare,tracking-audit | [operator_review.py:tracking_audit](../../codex-ads-live/src/codex_ads_live/operator_review.py#L327)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 落地页、UTM、同事件 Pixel/CAPI 和归因声明 → 缺 ID/重叠/未成对；测试与生产分列 | EV-E31，EV-F01。合成事件核对；不发送生产事件。 |
| C16 | workspace-plan,monitor-report,account-insights-slice | [read_adapter.py](../../codex-ads-live/src/codex_ads_live/read_adapter.py)、[analysis.py](../../codex-ads-live/src/codex_ads_live/analysis.py)、[read_archive.py](../../codex-ads-live/src/codex_ads_live/read_archive.py)、[multi_account_review.py:insights_slice](../../codex-ads-live/src/codex_ads_live/multi_account_review.py#L140)、[multi_account_review.py:review_inventory](../../codex-ads-live/src/codex_ads_live/multi_account_review.py#L104)、[analysis.py:aggregate_ads](../../codex-ads-live/src/codex_ads_live/analysis.py#L35) | 具名账户、日期、分页或已存归档 → 具名对象清单与已归档 ad-day 数据的本地切片和汇总，保留币种、时区、日期和覆盖 | EV-E18，EV-E27，EV-E29。本机假 HTTP 归档有效；空值保留。 |
| C17 | multi-account-plan,multi-account-review,account-inventory-review | [multi_account_review.py](../../codex-ads-live/src/codex_ads_live/multi_account_review.py)、[multi_account_review.py:summarize](../../codex-ads-live/src/codex_ads_live/multi_account_review.py#L65) | 同工作区具名账户和各自归档 → 按币种/时区分组；错工作区拒绝 | EV-E27，EV-E28。多账户明确具名，未自动查第三方账户。 |
| C18 | workspace-plan,workspace-first-launch-delivery,media-buyer review | [placement_review.py](../../codex-ads-live/src/codex_ads_live/placement_review.py)、[first_launch_delivery.py:delivery_review](../../codex-ads-live/src/codex_ads_live/first_launch_delivery.py#L24)、[placement_review.py:placement_delivery](../../codex-ads-live/src/codex_ads_live/placement_review.py#L9) | 广告、版位/投递状态、报告时段与来源 → 实际已存字段、覆盖及投递差异 | EV-E29，EV-E30，EV-E32，EV-F01。仅按同广告和可用窗口诊断。 |
| C19 | media-buyer review,performance-diagnose,commerce-reconcile | [media_buyer.py:_period_review](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L126)、[operator_review.py:performance_diagnose](../../codex-ads-live/src/codex_ads_live/operator_review.py#L248) | 可比漏斗、样本、落地页和 Offer 资料 → 比较 CPM、CTR、落地页等，缺项保留 | EV-E29，EV-E30，EV-E32，EV-E37，EV-F01。两期同 Ad 复盘及归因冲突拒绝有合成证据。 |
| C20 | tracking-audit,media-buyer prepare | [operator_review.py:tracking_audit](../../codex-ads-live/src/codex_ads_live/operator_review.py#L327) | 浏览器/服务端事件 ID 和环境 → 疑似重复、缺 ID、测试和生产区分 | EV-E31，EV-F01。生产流量不能用测试事件代替。 |
| C21 | media-buyer optimize,workspace-first-launch-update | [media_buyer.py:_proposal](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L249)、[first_launch_update.py:run](../../codex-ads-live/src/codex_ads_live/first_launch_update.py#L236) | 已审同 Ad 复盘、证据、反证、停止条件 → 具名待审批提案；另行启用受管或人工执行 | EV-E22，EV-E23，EV-E32，EV-F01，EV-F03。目标/范围进入提案，未形成实际平台变更。 |
| C22 | multi-account-plan,media-buyer status,workspace-first-launch-progress | [multi_account_review.py](../../codex-ads-live/src/codex_ads_live/multi_account_review.py)、[first_launch_progress.py:progress](../../codex-ads-live/src/codex_ads_live/first_launch_progress.py#L498)、[journal.py](../../codex-ads-live/src/codex_ads_live/journal.py) | 工作区、原任务、原尝试及对象摘要 → 锁、逐项状态、幂等范围和恢复 | EV-E26，EV-E27，EV-E28，EV-E34，EV-F01。合成未知响应先读回原对象。 |
| C23 | commerce-watch,commerce-watch-stop,commerce-watch-report,monitor-report | [commerce_watch.py](../../codex-ads-live/src/codex_ads_live/commerce_watch.py)、[monitor.py](../../codex-ads-live/src/codex_ads_live/monitor.py)、[commerce_watch.py:run](../../codex-ads-live/src/codex_ads_live/commerce_watch.py#L142)、[commerce_watch.py:stop](../../codex-ads-live/src/codex_ads_live/commerce_watch.py#L100) | 本机对账源、限时周期和告警 → 停止/续用、最近成功、过期及日报周报 | EV-E08，EV-E35，EV-E36。本机模拟停止和续跑；云端常驻留在 V2。 |
| C24 | shopify-read-preview,shopify-read-plan,shopify-read-review,commerce-reconcile | [shopify_graphql.py:request_plan](../../codex-ads-live/src/codex_ads_live/shopify_graphql.py#L356)、[shopify_graphql.py:Collector](../../codex-ads-live/src/codex_ads_live/shopify_graphql.py#L109)、[shopify_graphql.py:review](../../codex-ads-live/src/codex_ads_live/shopify_graphql.py#L376) | 店铺、已授权去标识回包、日期与 scopes → GraphQL 查询计划、分页、重试与 CSV 退路 | EV-E38，EV-E39，EV-F02。本地合同通过；未执行 Shopify OAuth 或实时商店查询。 |
| C25 | media-buyer review,shopify-read-review,commerce-reconcile | [commerce_csv.py:reconcile](../../codex-ads-live/src/codex_ads_live/commerce_csv.py#L100)、[shopify_graphql.py:reconcile_spend](../../codex-ads-live/src/codex_ads_live/shopify_graphql.py#L406) | 同币种同范围订单、退款、成本与广告消耗 → 去重、净收、MER、贡献利润和成本缺项 | EV-E37，EV-E38，EV-E39，EV-F01。用合成订单核对，未知库存/成本不填零。 |
| C26 | workspace-live-preview,page-live-contract-review,page-live-export-review | [live_contract.py](../../codex-ads-live/src/codex_ads_live/live_contract.py)、[page_export.py:review](../../codex-ads-live/src/codex_ads_live/page_export.py#L9)、[live_contract.py:relationship](../../codex-ads-live/src/codex_ads_live/live_contract.py#L9) | 具名 Page/Live 或已经授权的主办方汇总 → 核具名数据/评论或核导出；无音视频采集 | EV-E06，EV-E40，EV-R1-C26。本地导出合同有测试，连续真实 Page Live 未执行。 |
| C27 | workspace-first-launch-delivery,media-buyer review,monitor-report | [first_launch_delivery.py:delivery_review](../../codex-ads-live/src/codex_ads_live/first_launch_delivery.py#L24)、[workspace_diagnostics.py](../../codex-ads-live/src/codex_ads_live/workspace_diagnostics.py)、[monitor_tools.py](../../codex-ads-live/src/codex_ads_live/monitor_tools.py) | 原广告归档或前台任务及期限 → 本地报告保留范围、覆盖和缺项 | EV-E32，EV-E37，EV-F01。两期同广告的已核数据随报告引用。 |
| C28 | media-buyer status,workspace-first-launch-handoff,workspace-evidence | [media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288)、[workspace_evidence.py:revise](../../codex-ads-live/src/codex_ads_live/workspace_evidence.py#L18) | 同工作区、广告、原回执、期限和版本 → 原任务稳定接续、修订和撤回 | EV-E34，EV-R1-C28，EV-F01。同一范围可看回，原知识/客户记录独立。 |
| C29 | install.py,preflight,workspace-backup,workspace-restore | [install.py](../../codex-ads-live/install.py)、[runtime.py](../../codex-ads-live/src/codex_ads_live/runtime.py)、[MEDIA_BUYER.md](../../codex-ads-live/references/MEDIA_BUYER.md)、[onboarding.py:preflight](../../codex-ads-live/src/codex_ads_live/onboarding.py#L42)、[onboarding.py:windows_preflight](../../codex-ads-live/src/codex_ads_live/onboarding.py#L31)、[client_workspace.py:restore](../../codex-ads-live/src/codex_ads_live/client_workspace.py#L846)、[WINDOWS_CLIENT.md](../../codex-ads-live/references/WINDOWS_CLIENT.md)、[INSTALL_RUN.md](../../codex-ads-live/references/INSTALL_RUN.md) | 安装件、项目、升级预演、非秘密备份 → Mac 新装/升级/恢复；Win 原生及桌面待验 | EV-E01，EV-E02，EV-E03，EV-E42，EV-F04。180 项载荷与 138 命令；Windows 原生 NOT_RUN_NO_WINDOWS_HOST。 |
| C30 | knowledge-search,workflow-guide,partner-brief-review | [operator_v1.py:knowledge_search](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L130)、[partner_brief.py:review](../../codex-ads-live/src/codex_ads_live/partner_brief.py#L32)、[operator-knowledge.json](../../codex-ads-live/references/operator-knowledge.json)、[operator_v1.py:operator_route](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L174) | 官方资料、观点、案例和独立模块合同 → 保留来源、适用、撤回及验证状态 | EV-E09，EV-E10，EV-E11，EV-E41，EV-E42，EV-E43，EV-F05，EV-F06。本机复验 492 条索引与 14 个具名交接角色。 |

**现成输入输出示例**　同一 Ad 的两期归档复盘回执记录 windows 为 2。分层切片另用已归档的 ad-day 数据和 OBSERVED_AD_DAY_ROWS；账户版位、经营数据各保留来源。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_v1_r1_workflows.py。[已有测试文件](../../tests/test_v1_r1_workflows.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

能证明本地账务、归档比较、隔离及轮次报告；真实性、持续性和效果分别验收。

Meta v27.0、Shopify Admin GraphQL 2026-10。账号和日期受实际权限限制，本机轮次依赖进程。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　用获准 CSV 或具名回包补缺；Page Live 可用主办方导出；预算/素材变更必须另批。
