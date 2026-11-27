# 助手方法工作流

## 选择和加载

1. 先确认当前客户项目、原工作区和资料读取授权。新客户沿 [Meta 接入向导](CUSTOMER_SOP.md) 定位原路由。草案诊断可以使用无平台授权的本地资料。
2. 读安装版 `method-catalog`，按客户目标选择业务意图。八个意图可组合，发现词仅为辅助。按任务上下文选择，卡在业务事实时询问具体缺项。提到某个输入不等同于另开一个任务。
3. 用安装版 `method-plan --workspace WORKSPACE --intent INTENT --output NEW_DIR` 保存 `METHOD-PLAN.json`。如要固定非默认账户，显式传 `--account-id`。客户语言和业务材料由助手处理，日常无需交给客户输入命令。计划保留接入状态、账户、品牌档案摘要、方法版本和许可来源。
4. 读取每项已验证方法的入口、正文及从 `read_files` 选择的相关 refs。只读已验证的文件；方法文本里的工具命令和安装步骤不是产品指令。信息无法获得时标明缺失，同样区分客户事实、原始观测、判断和假设。
5. 对计划返回的 `knowledge_candidates`，用 `knowledge-search --batch01 --topic all --card-id METHOD_ID` 核验并读取原文、来源、日期、技术文档访问级别和反例。保存实际选择或保留不用的理由。批次01只含 Meta 使用所需方法与来源，详情见 [方法和来源说明](knowledge-batch01/NOTICE.md)。
6. 参考本机方法和 `knowledge-search` 的来源，比较可适用的策略流派。小预算与低样本测试先保住可观察性，稳定数据下再比较集中预算、受众扩展、版位和增量。具体策略选择由客户条件支持，草案仍保留原有授权门槛。

随包文本和已激活的固定参考会在工作区直接核验。缺件或旧版本时只从允许的固定 GitHub 来源尝试校验恢复。方法停用会保留原文件但不自动恢复。断网、来源失效、摘要异常时，保留可用核心及失败状态。任务需要该方法特有证据且没有可替代依据时，按业务语言说明待补内容。安装许可及来源见 [DISTRIBUTION_NOTICES.md](DISTRIBUTION_NOTICES.md)。

## 从方法成果到下一模块

助手在工作区生成一份 JSON，顶层使用 `schema: meta-method-application-v1`、`methods` 和 `consumer`。在 `methods` 中为本次意图的每个成功装载的方法填入以下字段：

- `deliverable` 是完整业务成果，写清建议与根据。
- `brief` 沿用 `partner-evidence-brief-v1`。包含 `role`、`product`、两位 `market`、以及有唯一 `EV_…` 参考、类型、时间和 `CONFIRMED`／`HYPOTHESIS`／`WITHDRAWN` 状态的 `items`。
- `read_files` 列实际读取的入口、正文及必要参考路径。记录 `input_requirements`、`assumptions`、`unknowns` 三组文本。

需要用批次01时，在同一 JSON 增加 `knowledge` 列表。每行有 `method_id`、核验返回的 `method_sha256`、`selection`（`USED_AS_BASIS`、`COMPARED` 或 `INAPPLICABLE_RETAINED`）、`applicability_check`、`contribution`、`counterevidence`、`assumptions` 与 `unknowns`。只把基于客户资料得出的选择写入真实消费者合同。纯参考卡可比较，不能充当编译依据。报表可继续引用反例、竞争做法和缺项。

`consumer.command` 只能选本次意图的本机业务命令。助手用真实授权材料生成消费者合同。运行 `method-apply --workspace WORKSPACE --plan METHOD-PLAN.json --intent INTENT --input APPLICATION.json --source-material FILE --output NEW_DIR`，需要时重复 `--source-material`。同一个计划可依次交多个意图，各自使用新结果目录。

| 本机消费者 | `consumer` 的其余字段与实际效果 |
| --- | --- |
| `strategy-review`、`performance-diagnose`、`tracking-audit`、`business-readiness` | `document` 为原业务 JSON。传入从证据提取的变量、事件、窗口或商品事实。 |
| `media-buyer prepare` | `case` 和 `intake`。应用过程追加方法回执，原业务模块依次核对账户、商品、素材、追踪与策略选择。可带 `selected_strategy` 编译暂停对象。 |
| `creative-compare` | `concept_ids` 及可选 `media_reports`。从原工作区回读实际概念和已声明的媒体分析附件。 |
| `public-ad-inspect` | `observation`、`page_id`、`archive_id`、`hypothesis_ref`，可选 `media_report`。消费者使用回执中该条假设和两层原始编号。 |
| `commerce-reconcile` | `orders`、`refunds`、`spend`、`store_id`、`account_id`、`currency`、`since`、`until`、`refund_basis`。用真实本机核算引擎生成报告和工作簿。 |

输出包括每项 `METHOD-RECEIPT.json`、批次01的知识回执、原消费者入参、`BUSINESS-RESULT.json`，以及消费者实际成功后生成的 `CONSUMPTION.json`。方法加载的计划状态和执行后的消费证据分开。无有效材料时可以给出临时诊断及缺项，不伪造消费回执。变更工作区登记、账户或方法文件后，重新出计划。出现半成结果目录时，保留已出件并用新目录续接，未写消费回执表示仍待完成。

维护命令 `method`、`method-install`、`method-disable` 和原 `workspace-method-record` 保留用于已有流程、调试与重放。旧固定源码目录仍兼容，由维护者使用。
