# 横向底座能力

## 当前候选的客户路径

从干净 ZIP 安装到新客户项目并核对外置方法锁。原生实机、第三方平台权限和发布按各自记录保留。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C29 | 客户安装/升级/故障/退出手册 | `install.py → run.py preflight / workspace-connect / method` | A18 |
| C30 | 来源/策略知识治理与Evals | `knowledge-search` / `method → workspace-method-record → media-buyer prepare` / `install.py → run.py preflight / workspace-connect / method` | A03 A17 A18 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

这些能力贯穿六类工作。原台账中的 ID 可在[逐项矩阵](capability-matrix.csv)追到实际入口、函数、评测及边界。合作伙伴合同或其他渠道在自己的授权范围独立运行。

| 底座 | 对应 V1 | 客户入口及实现 | 可复核证据 | 运行边界 |
| --- | --- | --- | --- | --- |
| 客户工作区、品牌商品与跨会话接续 | C01、C05、C28 | media-buyer prepare / status、workspace-creative-research、workspace-evidence。原 [case 管理](../../codex-ads-live/src/codex_ads_live/media_buyer.py) 将同一计划摘要与历史记录关联，[证据变动](../../codex-ads-live/src/codex_ads_live/workspace_evidence.py) 写在原工作区。 | EV-F01 同一计划；EV-E02 升级保留旧档；EV-R1-C28 撤回。 | 本机可移植配置与原运行、权限和报告分别处理。 |
| 专业知识、策略来源、官方核验 | C03、C06、C30 | knowledge-search、strategy-review、meta-guidance-review；[策略索引](../../codex-ads-live/references/operator-knowledge.json) 和 [准备审核](../../codex-ads-live/src/codex_ads_live/operator_review.py)。 | EV-E09 至 EV-E11；EV-F06 的分组及分页。 | 平台事实、从业者观点、历史教程和客户证据分列；无效果承诺。 |
| 权限审批、唯一受管写入、账本与恢复 | C12、C13、C14、C15、C21、C22、C28 | workspace-first-launch-progress / activation / update；[受管执行](../../codex-ads-live/src/codex_ads_live/first_launch_managed_write.py)、[逐项进度](../../codex-ads-live/src/codex_ads_live/first_launch_progress.py)、[本机账本](../../codex-ads-live/src/codex_ads_live/journal.py)。 | EV-E19、EV-E20、EV-E21、EV-E22、EV-E26、EV-E33、EV-E34、EV-F03。 | PAUSED、启用及投递分别核对；未知状态先回读，批准范围已过期时停在预览。 |
| Shopify、CSV 与经营核算 | C24、C25、C27 | shopify-read-plan / review、commerce-reconcile、media-buyer review；[去标识回包](../../codex-ads-live/src/codex_ads_live/shopify_graphql.py)和[核算](../../codex-ads-live/src/codex_ads_live/commerce_csv.py)。 | EV-E37、EV-E38、EV-E39、EV-F02。 | 商家真实 OAuth 未执行。实际店铺权限、缺成本、退款身份与日期范围需核。 |
| 多账户路由与币种、时区隔离 | C16、C17、C22 | multi-account-plan / review、account-insights-slice；[同工作区范围](../../codex-ads-live/src/codex_ads_live/multi_account_review.py)。 | EV-E27、EV-E28、EV-R1-C17。同一工作区分别审阅，错误身份拒绝。 | 按币种和时区分别分组。`account-insights-slice` 在本地筛选和聚合已归档的广告日粒度数据，结果依据标为 `OBSERVED_AD_DAY_ROWS`。这类切片的口径区别于平台原生父级去重 Insights；缺失的 reach/frequency 不据此推算。 |
| 本机看守、停止续用、告警、日周报 | C23、C27、C28 | commerce-watch、commerce-watch-stop、commerce-watch-report；[本机看守](../../codex-ads-live/src/codex_ads_live/commerce_watch.py)。 | EV-E08、EV-E35、EV-E36；原最近成功时间和本机停止结果。 | 前台进程、时窗及本机文件可用时运行。电脑关机、休眠或进程退出会影响本机采集。 |
| 安装预检、升级、备份、恢复与凭据 | C02、C29 | [安装器](../../codex-ads-live/install.py)、preflight、workspace-backup、workspace-restore、credential-status；[原生凭据只读](../../codex-ads-live/src/codex_ads_live/native_credentials.py)。 | EV-E01、EV-E02、EV-E03、EV-W01、EV-W02、EV-W03。 | Mac 实测、Windows 模拟及 Windows 原生待核为三个独立结论。备份不迁移凭据。 |
| 合作伙伴 Brief、独立 Skill 和渠道适配 | C05、C06、C10、C30 | partner-brief-review、operator-route；[角色合同](../../codex-ads-live/src/codex_ads_live/partner_brief.py)依据具名输入核字段与撤回。 | EV-F05 有 14 个角色合成合同；EV-E43 分离千川/Google Ads 路线。 | 四项随包方法与其余 14 份合同保持原边界。仅处理提供的证据；相应渠道的真实连接与执行由独立产品负责。 |

`account-inventory-review` 核对已捕获的具名对象、父子关系和覆盖。账户版位读取用独立的版位报告（`workspace-plan --task ads-placements`）；同一 Ad 复盘使用具名广告和可比观察窗。分别保留来源、日期、币种、时区及覆盖。

[证据登记](evidence-register.csv) · [本地输出摘录](evidence-receipts.json) · [平台边界](08-platform-and-runtime-boundaries.md)
