# 策略、素材和运营复盘

打开已经安装 Skill 的客户项目。下面的命令从项目目录执行。把资料文件名换成实际路径。所有示例均使用虚构资料。

```sh
RUN=".agents/skills/codex-ads-live/run.py"
python3 "$RUN" --version
python3 "$RUN" knowledge-search --topic strategy
```

## 1. 导入品牌资料并选定试验

在客户项目里整理品牌定位、商品事实、获准使用的图片和视频、地区、币种、优惠、购买前问题、毛利和履约成本。每条事实写清来源、适用的商品及观察日期。用 `workspace-creative-research --help` 查看导入入口。已有原工作区时，先加载现有品牌和证据再增补。其他模块可将本地佐证整理为 `partner-evidence-brief-v1`，通过 `partner-brief-review --input brief.json` 核对，然后按每条 `next_entry` 交给对应工作流。

```sh
python3 "$RUN" strategy-review --input strategy.json
python3 "$RUN" tracking-audit --input tracking.json
```

策略 JSON 的 `schema` 是 `operator-strategy-v1`。填入 `account_id`、`currency`、`product`、`offer`、`audience`、`destination`、`proof_ref`、`goal`、`test_variable`、`advantage_plus` 和 `commercial`。`proof_ref` 以 `EV_` 开头。以购买为目标时，`goal` 用 `PURCHASE`。首次可用 `CREATIVE_ANGLE` 作变量，尚未核实 Advantage+ 资格时填 `UNKNOWN`。

`commercial` 填 `net_order_minor`（每单净收入）、`variable_cost_minor`（可变成本）、`target_cpa_minor`、`test_budget_minor`、`minimum_results` 和 `days`。金额采用币种最小单位，可填 `null` 标出缺失。用结果中的样本预算、停止条件和来源限制确定本次广告试验。

跟踪 JSON 的 `schema` 是 `operator-tracking-v1`，填写 `dataset_id`、`destination` 及 `events`。每个事件包含 `channel`（`PIXEL` 或 `CAPI`）、`name`、`event_id`。使用经一致映射的 `EV_` 事件别名，缺失填 `null`。该报告可检查同键组合及重复 UTM。Meta 实际归因和去重结果由平台核实。

## 2. 核对素材和公开广告

```sh
python3 "$RUN" media-analyze --file "商品广告.mp4" --subtitles "字幕.srt" --output "素材复核-01"
python3 "$RUN" public-ad-query --input public-scope.json --page-id 400 --archive-id 500
python3 "$RUN" public-ad-inspect --input public-evidence.json --page-id 400 --archive-id 500
```

素材报告依赖本机的 FFmpeg 和 FFprobe。支持本地 PNG、JPG、WebP 及常见视频，单件最多 100 MiB，视频最长 3 分钟。`--subtitles` 传已有 SRT。若本机已有 `whisper-cli` 和获准使用的模型文件，可以改用 `--model`。打开输出目录的 `review.html`，复核卖点、钩子、转化点和真人感，应以实际画面、音轨或字幕为证据。打开同目录的 `media-evidence.json` 可查素材摘要和帧时码。

要进入现有概念，先建立概念 ID。用新的输出目录再次运行，`--observations` 传入包含 `media_sha256` 和 `observations` 的 JSON。四项各可填 `kind`（`FRAME` 或 `SUBTITLE`）、从 0 开始的 `index` 和 `observation`。加 `--concept-id` 导出 `workspace-review.json`，再通过 `workspace-creative-research --input` 导入。首投计划选择该概念、变体和同一素材摘要。实际素材使用权仍需另行核对。

公开广告查询命令默认只预览。输入包含 `query`、两个字母的 `countries` 数组、`status`（`ACTIVE`、`INACTIVE` 或 `ALL`）、`date_min` 和 `date_max`，日期可为 `null`。具备独立 Ads Library API 访问权限后，才可用 `--execute` 并在终端确认。本机保存平台返回的观察，之后用双编号核对，可选 `--media-report` 和 `--angle-hypothesis`。素材来源及其与目标广告的关系仍需查原件，对表现的推断需注明依据。

## 3. 核对建单、预览、启用与复盘

先按[首投操作](FIRST_LAUNCH_CUSTOMER.md)打开同一广告计划。多条广告创建后，从 `workspace-first-launch-progress` 核对每条对象。给 `workspace-first-launch-preview`、`workspace-first-launch-activation`、`workspace-first-launch-evidence` 和 `workspace-first-launch-delivery` 加 `--ad-id`，固定本次要看的那条。每次启用和预算变更需重新看到目标、窗口和批准。对象结果不明时，继续查询原尝试。

表现下滑时用 `performance-diagnose --input compare.json`。输入的 `schema` 是 `operator-diagnosis-v1`，包含 `before`、`after`、`minimum_results`。两份报表需有等长且互不重叠的 `since` 至 `until`，以及同样的账户 `account_id`、`currency`、`timezone`、`attribution`、`scope_id`、报表时间口径 `report_time`、主要事件 `result_event`。填入 `impressions`、`spend_minor`、`clicks`、`landing_views`、`conversions`、`conversion_value_minor`、`coverage` 和 `delivery`。缺失指标用 `null`，有完整读取时填 `COMPLETE`。

`multi-account-review --workspace "工作区" --receipt "账户A回执" --receipt "账户B回执"` 逐个读取原工作区的已核验报表。不同账户的币种、时区、窗口和证据单列。

## 4. 导入店铺数据

把导出的数据整理成 UTF-8 CSV。只保留这些列，表头需一致。

| 表 | 表头 |
| --- | --- |
| 订单行 | `store_id,order_id,line_id,date,currency,gross_minor,discount_minor,product_cost_minor,fulfillment_minor,payment_minor` |
| 退款 | `store_id,refund_id,order_id,line_id,date,currency,refund_minor` |
| 消耗 | `account_id,date,currency,spend_minor` |

日期使用 `YYYY-MM-DD`。先核对店铺和广告账户的日期口径。金额以最小货币单位填写，缺成本可留空，明确无消耗的日期写 0。退款只能关联已提供的订单行，订单级退款需先分配且只导入一次。

```sh
python3 "$RUN" commerce-reconcile --orders orders.csv --refunds refunds.csv --spend spend.csv \
  --store-id shop-sample --account-id 100 --currency USD --since 2026-11-01 --until 2026-11-07
```

默认按退款实际发生日核算，`--refund-basis order` 按原订单日期分析。输出显示净销售、退款、可核实的成本、广告消耗、贡献利润和 MER。缺成本或消耗日时保留缺项。

`shopify-read-preview --input shopify.json` 可以核对窗口和店铺自报的只读权限。输入 `schema` 为 `shopify-order-readiness-v1`，填写 `store`（店铺的 myshopify.com 域名）、`since`、`until`、`checked_on` 和 `reported_scopes`。当前对账使用 CSV。[Shopify 权限](https://shopify.dev/docs/api/usage/access-scopes)

## 5. 复核本机日报、周报和公共主页

将 `commerce-watch` 的任务文件设为 `shop-commerce-watch-v1`，填 `orders`、`refunds`、`spend`（同目录下的文件名）、`store_id`、`account_id`、`currency`、`refund_basis`。固定窗口填 `since` 和 `until`。滚动报告填 `days`（1 或 7）及 `timezone`（如 `Asia/Shanghai`），统计到当地昨天。目录应存放在项目中。用 `commerce-watch --input watch.json --state-dir watch-state` 运行一个周期。以同一状态目录运行 `commerce-watch-report` 查看最近结果，或运行 `commerce-watch-stop` 请求停止。重新开始之前核查任务与数据，再显式加 `--resume`。Mac 休眠时没有新的报告。

公共主页访问按[工作流](WORKFLOWS.md)使用指定 Page 和可获得的获准字段。没有直播对象及相应权限时，将该次直播记录标为待验证。可使用随包合成练习熟悉输出结构。

用 `operator-route --platform qianchuan` 查看其他平台的范围。巨量千川、Google Ads、TikTok Ads 和 LinkedIn Ads 使用各自账户、接口及权限。它们的投放工具须在各平台单独接入。
