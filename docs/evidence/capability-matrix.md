# V1 完整能力矩阵

六项一级分类覆盖当前 30 个 V1 二级能力。每项有唯一主归属，可以关联其他分类。R1 历史编号另列。

| 一级分类 | 名称 | 主归属 |
| --- | --- | ---: |
| G01 | [Meta 账户接入与业务准备](01-meta-account-and-business.md) | 6 |
| G02 | [广告素材诊断](02-creative-diagnostics.md) | 3 |
| G03 | [投放策略与广告计划](03-strategy-and-ad-planning.md) | 3 |
| G04 | [竞品广告研究](04-competitor-ad-research.md) | 1 |
| G05 | [素材管理与受控广告执行](05-material-and-controlled-execution.md) | 5 |
| G06 | [账户诊断、经营核算与持续复盘](06-account-diagnosis-and-business-review.md) | 12 |

[逐项 CSV](capability-matrix.csv)含入口、源码、合同、实现/验证状态、原评测和 Windows 状态。参见[证据](evidence-register.csv)及[平台限制](08-platform-and-runtime-boundaries.md)。

| ID | 二级能力 | 主类/关联类 | 实现状态 | 客户入口 | 评测 |
| --- | --- | --- | --- | --- | --- |
| C01 | 客户工作区、品牌与商品档案 | G01 / G03 / G06 | INTEGRATED | media-buyer prepare/status,workspace-route | EV-E01，EV-E02，EV-F01 |
| C02 | Meta App/Business Portfolio/Ad Account/Page接入 | G01 | INTEGRATED | meta-access-review,workspace-bind,workspace-refresh,workspace-page-preview | EV-E04，EV-E05，EV-E06，EV-F02 |
| C03 | 权限/资产/账户准备度诊断 | G01 | INTEGRATED | meta-access-review,meta-guidance-review,workspace-onboarding-report | EV-E04，EV-E05，EV-E10，EV-F02 |
| C04 | Pixel/CAPI/UTM/事件/归因追踪审计 | G01 / G03 / G06 | INTEGRATED | media-buyer prepare,tracking-audit | EV-E31，EV-F01 |
| C05 | 商品/Offer/单位经济/落地页准备度 | G01 / G03 | INTEGRATED | media-buyer prepare,business-readiness,partner-brief-review | EV-R1-C05，EV-F01 |
| C06 | 客户洞察/VOC/多流派策略 | G03 / G04 | INTEGRATED | media-buyer prepare,knowledge-search,strategy-review | EV-E09，EV-E10，EV-E11，EV-F01，EV-F06 |
| C07 | 公开竞品广告研究 | G04 | PLATFORM_ADAPTED | public-ad-query,public-ad-inspect,media-buyer prepare | EV-E16，EV-E17，EV-E18，EV-F01 |
| C08 | 广告素材图片/视频分析 | G02 | LOCAL_CONTRACT_COMPLETE_EXTERNAL_DEFERRED | media-analyze | EV-E12，EV-E14，EV-E15 |
| C09 | 素材审阅与版本管理 | G02 / G05 | REIMPLEMENTED | workspace-creative-research,creative-compare,media-analyze,media-buyer prepare | EV-E12，EV-E13，EV-E15，EV-F01 |
| C10 | 创意Brief/文案/广告变量 | G02 / G03 / G04 | INTEGRATED | workspace-creative-research,media-buyer prepare,creative-compare | EV-E12，EV-E13，EV-F01 |
| C11 | Campaign结构/受众/版位/预算/测试 | G03 / G05 | INTEGRATED | media-buyer prepare/plan,strategy-review,workspace-first-launch-check | EV-E09，EV-E19，EV-F01 |
| C12 | Campaign/AdSet/Creative/Ad创建 | G05 | INTEGRATED | media-buyer plan/status,workspace-first-launch-progress | EV-E19，EV-E20，EV-E26，EV-F01 |
| C13 | 多素材批次预览/批准/执行 | G05 | PLATFORM_ADAPTED | media-buyer status,workspace-first-launch-preview,workspace-first-launch-activation | EV-E21，EV-E26，EV-E33，EV-E34，EV-F01 |
| C14 | 启停/预算/受众/素材修改审查 | G05 | PLATFORM_ADAPTED | workspace-first-launch-update,media-buyer optimize | EV-E22，EV-E23，EV-F03 |
| C15 | 媒体上传与视频就绪/复用 | G05 | INTEGRATED | workspace-first-launch-media | EV-E24，EV-E25 |
| C16 | 广告报表与分层读取 | G06 | INTEGRATED | workspace-plan,monitor-report,account-insights-slice | EV-E18，EV-E27，EV-E29 |
| C17 | 多账户隔离与聚合 | G06 / G01 | INTEGRATED | multi-account-plan,multi-account-review,account-inventory-review | EV-E27，EV-E28 |
| C18 | 版位和投递诊断 | G06 | INTEGRATED | workspace-plan,workspace-first-launch-delivery,media-buyer review | EV-E29，EV-E30，EV-E32，EV-F01 |
| C19 | 漏斗/样本/素材/Offer/LP表现诊断 | G06 | INTEGRATED | media-buyer review,performance-diagnose,commerce-reconcile | EV-E29，EV-E30，EV-E32，EV-E37，EV-F01 |
| C20 | Pixel/CAPI/GTM事件诊断 | G06 / G01 | INTEGRATED | tracking-audit,media-buyer prepare | EV-E31，EV-F01 |
| C21 | 优化提案与受控执行 | G06 / G05 | INTEGRATED | media-buyer optimize,workspace-first-launch-update | EV-E22，EV-E23，EV-E32，EV-F01，EV-F03 |
| C22 | 多账户/批量任务账本与恢复 | G05 / G06 | INTEGRATED | multi-account-plan,media-buyer status,workspace-first-launch-progress | EV-E26，EV-E27，EV-E28，EV-E34，EV-F01 |
| C23 | 本机Watchdog/调度/告警/日报周报 | G06 | INTEGRATED | commerce-watch,commerce-watch-stop,commerce-watch-report,monitor-report | EV-E08，EV-E35，EV-E36 |
| C24 | Shopify GraphQL只读连接器 | G06 | LOCAL_CONTRACT_COMPLETE_EXTERNAL_DEFERRED | shopify-read-preview,shopify-read-plan,shopify-read-review,commerce-reconcile | EV-E38，EV-E39，EV-F02 |
| C25 | 订单/退款/成本/库存/净收入/贡献利润核算 | G06 | INTEGRATED | media-buyer review,shopify-read-review,commerce-reconcile | EV-E37，EV-E38，EV-E39，EV-F01 |
| C26 | Facebook公共主页Live V1数据 | G06 | LOCAL_CONTRACT_COMPLETE_EXTERNAL_DEFERRED | workspace-live-preview,page-live-contract-review,page-live-export-review | EV-E06，EV-E40，EV-R1-C26 |
| C27 | 客户级报表/报告导出 | G06 | INTEGRATED | workspace-first-launch-delivery,media-buyer review,monitor-report | EV-E32，EV-E37，EV-F01 |
| C28 | 任务历史/恢复/知识回流 | G06 / G05 | INTEGRATED | media-buyer status,workspace-first-launch-handoff,workspace-evidence | EV-E34，EV-R1-C28，EV-F01 |
| C29 | 客户安装/升级/故障/退出手册 | G01 / G06 | INTEGRATED | install.py,preflight,workspace-backup,workspace-restore | EV-E01，EV-E02，EV-E03，EV-E42，EV-F04 |
| C30 | 来源/策略知识治理与Evals | G03 / G04 / G06 | INTEGRATED | knowledge-search,workflow-guide,partner-brief-review | EV-E09，EV-E10，EV-E11，EV-E41，EV-E42，EV-E43，EV-F05，EV-F06 |

## 读表时需确认的口径

- C08、C09。抽样最多三个时间点；四维观察由 Codex 或审阅者提供，当前引用 FRAME 或 SUBTITLE，真人感用 FRAME；未观察为 UNOBSERVED。
- C11、C12。Campaign → Ad Set → Ad 构成父子链，Ad 引用 Creative。多素材批次共享 Campaign 和 Ad Set，每条素材各有对应的 Creative 和 Ad 草案。计划命令先生成并预填本地草案。Codex 按已审核的商品、预算、受众和文案补齐剩余字段，再用 `check_drafts`（`workspace-first-launch-check`）检查。具名审批和受管条件满足后才进入 PAUSED 创建及回读。草案预览、平台创建、启用投递及效果分别核对。ABO/CBO 可作策略比较；本候选首投写入限 Ad Set（ADSET）日预算，Campaign 层预算无当前首投写入路径。
- C16。`account-insights-slice` 在本地筛选和聚合已归档的广告日粒度数据，结果依据标为 `OBSERVED_AD_DAY_ROWS`。这类切片的口径区别于平台原生父级去重 Insights；缺失的 reach/frequency 不据此推算。

## 本次全链路工作单

[逐项 CSV](capability-matrix.csv)的末列对应本次 A01–A18。保留历史评测 ID，可通过本次回传中的来源和场景清单复查。
