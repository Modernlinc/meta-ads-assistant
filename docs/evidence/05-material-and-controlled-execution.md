# 素材管理与受控广告执行

## 当前候选的客户路径

受管路径继续要求独立逐项批准。具名存量广告可先从本地快照接续，核对同 Page、创意和受影响范围后进入调整流程。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C13 | 多素材批次预览/批准/执行 | `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `workspace-first-launch-activation / native-rules` | A09 A10 |
| C14 | 启停/预算/受众/素材修改审查 | `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `workspace-first-launch-activation / native-rules` / `native-insights → media-buyer review/optimize → workspace-first-launch-update` | A09 A13 |
| C15 | 媒体上传与视频就绪/复用 | `native-audience / catalog-review / ad-policy-preflight → media-buyer prepare` / `native-inventory → media-buyer plan 或 workspace-first-launch-update` | A05 A10 A11 |
| C21 | 优化提案与受控执行 | `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `native-insights → media-buyer review/optimize → workspace-first-launch-update` | A10 A13 |
| C22 | 多账户/批量任务账本与恢复 | `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `native-insights / native-inventory` | A02 A10 A12 |
| C28 | 任务历史/恢复/知识回流 | `media-analyze → creative-compare → media-buyer prepare/learn` / `native-inventory → media-buyer plan 或 workspace-first-launch-update` / `native-insights → media-buyer review/optimize → workspace-first-launch-update` | A05 A10 A13 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

把素材版本、计划和独立审批关联，未知结果回到原尝试核对。

能力 ID C09、C11、C12、C13、C14、C15、C21、C22、C28。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「沿原计划检查每条 Ad 的素材和批准范围，先预览，未知状态先读原尝试。」

素材权利/摘要、具名对象、冻结计划、支出控制和批准。受管执行需管理员配置及有效授权。

```text
workspace-first-launch-check --workspace <工作区> --plan-dir <原计划>
```

## 流程和专业依据

选素材 → 核权利和摘要 → 具名批准上传 → 核可用性 → PAUSED 建单 → 逐项回读 → 另行批准启用或修改。

**官方事实或工具合同**　按四类对象核对父子关系、素材引用和具名批准。分别记录配置状态和实际投递；发送结果未知时保留原尝试和对象范围。 [Marketing API v27.0](https://developers.facebook.com/docs/marketing-api/reference/v27.0/) · [独立受管流程](../../codex-ads-live/references/FIRST_LAUNCH_LOCAL_AUTHORITY.md)

**从业者方法与客户业务规则**　预览、审批、创建、读取和启用按具名范围分别判断。留存当前方案与停止条件。

Campaign → Ad Set → Ad 构成父子链，Ad 引用 Creative。多素材批次共享 Campaign 和 Ad Set，每条素材各有对应的 Creative 和 Ad 草案。

计划命令先生成并预填本地草案。Codex 按已审核的商品、预算、受众和文案补齐剩余字段，再用 `check_drafts`（`workspace-first-launch-check`）检查。具名审批和受管条件满足后才进入 PAUSED 创建及回读。草案预览、平台创建、启用投递及效果分别核对。

ABO/CBO 可作策略比较；本候选首投写入限 Ad Set（ADSET）日预算，Campaign 层预算无当前首投写入路径。

[客户首投操作步骤](../../codex-ads-live/references/FIRST_LAUNCH_CUSTOMER.md)

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C09 | workspace-creative-research,creative-compare,media-analyze,media-buyer prepare | [creative_research.py:freeze_selection](../../codex-ads-live/src/codex_ads_live/creative_research.py#L266)、[media_evidence.py:analyze](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L194)、[media_evidence.py:load_observations](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L124)、[media_evidence.py:evaluate_observations](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L136) | 原概念、变体、素材摘要及 Codex 或审阅者提供的观察 → 把输入观察与同一素材及 FRAME 或 SUBTITLE 绑定；保留版本和 UNOBSERVED，真人感须引用 FRAME | EV-E12，EV-E13，EV-E15，EV-F01。可回查摘要与报告。脚本校验摘要和证据引用，评语语义由审阅者判断。 |
| C11 | media-buyer prepare/plan,strategy-review,workspace-first-launch-check | [first_launch_plan.py:scoped_draft](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L196)、[operator_review.py:strategy_review](../../codex-ads-live/src/codex_ads_live/operator_review.py#L129)、[first_launch_plan.py:stage_specs](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L25)、[first_launch_plan.py:_draft](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L80)、[first_launch_plan.py:check](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L228)、[meta_write_mapping.py:blank_draft](../../codex-ads-live/src/codex_ads_live/meta_write_mapping.py#L54)、[media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288) | 目标、受众、版位、预算与样本假设 → 预填的四类对象草案及依赖；Codex 补齐后检查，拒绝范围漂移 | EV-E09，EV-E19，EV-F01。预算/落地页变更受校验；草案不生成平台对象。 |
| C12 | media-buyer plan/status,workspace-first-launch-progress | [first_launch_plan.py:prepare](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L124)、[first_launch_progress.py:progress](../../codex-ads-live/src/codex_ads_live/first_launch_progress.py#L498)、[first_launch_managed_write.py](../../codex-ads-live/src/codex_ads_live/first_launch_managed_write.py)、[first_launch_managed_write.py:execute_approved](../../codex-ads-live/src/codex_ads_live/first_launch_managed_write.py#L291)、[first_launch_local_host.py:LocalFirstLaunchHost](../../codex-ads-live/src/codex_ads_live/first_launch_local_host.py#L59)、[first_launch_plan.py:stage_specs](../../codex-ads-live/src/codex_ads_live/first_launch_plan.py#L25)、[meta_write_mapping.py:blank_draft](../../codex-ads-live/src/codex_ads_live/meta_write_mapping.py#L54) | 已补齐并检查的四类对象草案、具名审批和受管执行条件 → 核对 Campaign → Ad Set → Ad 父子链及 Ad 引用 Creative 的回执 | EV-E19，EV-E20，EV-E26，EV-F01。合成验证 PAUSED 建单合同，真实对象未建。 |
| C13 | media-buyer status,workspace-first-launch-preview,workspace-first-launch-activation | [first_launch_progress.py:select_ad](../../codex-ads-live/src/codex_ads_live/first_launch_progress.py#L478)、[first_launch_preview.py:preview](../../codex-ads-live/src/codex_ads_live/first_launch_preview.py#L108) | 原批次、各 Ad ID、媒体版本和批准 → 批次逐项、审批/回读/恢复状态 | EV-E21，EV-E26，EV-E33，EV-E34，EV-F01。三份素材批次及未知项有合成证据。 |
| C14 | workspace-first-launch-update,media-buyer optimize | [first_launch_update.py:run](../../codex-ads-live/src/codex_ads_live/first_launch_update.py#L236)、[first_launch_managed_write.py:recover_update](../../codex-ads-live/src/codex_ads_live/first_launch_managed_write.py#L234) | 受影响对象、原读回、变动范围和审批 → 暂停/预算/有限地域年龄、出价额和同批素材预览 | EV-E22，EV-E23，EV-F03。合成验证变更请求与拒绝；复杂策略人工。 |
| C15 | workspace-first-launch-media | [first_launch_media.py](../../codex-ads-live/src/codex_ads_live/first_launch_media.py)、[first_launch_media_ledger.py](../../codex-ads-live/src/codex_ads_live/first_launch_media_ledger.py)、[first_launch_media.py:run](../../codex-ads-live/src/codex_ads_live/first_launch_media.py#L302)、[first_launch_media_ledger.py:bind_reuse](../../codex-ads-live/src/codex_ads_live/first_launch_media_ledger.py#L239) | 素材、摘要、计划及单次批准 → 媒体就绪、原尝试状态与复用范围 | EV-E24，EV-E25。合成验证图片/视频上传合同和就绪。 |
| C21 | media-buyer optimize,workspace-first-launch-update | [media_buyer.py:_proposal](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L249)、[first_launch_update.py:run](../../codex-ads-live/src/codex_ads_live/first_launch_update.py#L236) | 已审同 Ad 复盘、证据、反证、停止条件 → 具名待审批提案；另行启用受管或人工执行 | EV-E22，EV-E23，EV-E32，EV-F01，EV-F03。目标/范围进入提案，未形成实际平台变更。 |
| C22 | multi-account-plan,media-buyer status,workspace-first-launch-progress | [multi_account_review.py](../../codex-ads-live/src/codex_ads_live/multi_account_review.py)、[first_launch_progress.py:progress](../../codex-ads-live/src/codex_ads_live/first_launch_progress.py#L498)、[journal.py](../../codex-ads-live/src/codex_ads_live/journal.py) | 工作区、原任务、原尝试及对象摘要 → 锁、逐项状态、幂等范围和恢复 | EV-E26，EV-E27，EV-E28，EV-E34，EV-F01。合成未知响应先读回原对象。 |
| C28 | media-buyer status,workspace-first-launch-handoff,workspace-evidence | [media_buyer.py:run_case](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L288)、[workspace_evidence.py:revise](../../codex-ads-live/src/codex_ads_live/workspace_evidence.py#L18) | 同工作区、广告、原回执、期限和版本 → 原任务稳定接续、修订和撤回 | EV-E34，EV-R1-C28，EV-F01。同一范围可看回，原知识/客户记录独立。 |

**现成输入输出示例**　三组已登记合成素材打入原批次，回执保留逐条预览和未确认的原尝试。已核三组模拟写入且 real_graph_requests 为 0。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_v1_operator_boundaries.py。[已有测试文件](../../tests/test_v1_operator_boundaries.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

合成回执能验证参数、部分失败和恢复合同；真实对象状态、启用和收费需要独立平台证据。

受管写入需要配置好的 macOS。Windows 只保留获批人工 Ads Manager 的执行和回读；支出另批。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　缺批准或宿主时停在本地草案，已授权的人工作业记原 ID 并导回。
