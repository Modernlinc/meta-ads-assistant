# 经营证据与接续操作

在原客户项目的终端运行 `python3 .agents/skills/codex-ads-live/run.py 命令 --help`。以下文件是各入口需要的本机输入。金额、计量和日期先确认币种、精度和账户时区。配对入口对应的示例见本说明和[运营工作流](OPERATOR_V1.md)。

| 你想做的事 | 从这里开始 |
| --- | --- |
| 准备商品和优惠 | `business-readiness --input 经营资料.json`。给商品、优惠、落地页与成本各提供出处。冲突值和缺项会保留。 |
| 旧教程里找不到按钮 | `meta-guidance-review --input 旧教程.json`。填写教程日期、目标地区、账户资格和界面。 |
| 审阅预算和受众策略 | `strategy-review --input 策略.json`。可补 `prior_results`、`alternative_audience`、`placements` 和 `attribution_basis`。线索投放可填已核对的 `lead_value_minor`。报告分别展示 ABO/CBO、宽泛/兴趣、预算、放量与归因条件。 |
| 检查视频研究的证据 | `knowledge-search --topic all --notes --query "关键词" --limit 30`。下一页使用响应中的 `next_offset`。笔记和已整理结论有各自的来源等级。 |
| 比较两个创意 | `creative-compare --workspace 工作区 --account-id 100 --concept-id A --concept-id B --output 对照资料`。以已登记概念为准。 |
| 看投放问题在哪个环节 | `performance-diagnose --input 对照.json`。可按 `delivery`、`placement`、`tracking`、`sample`、`creative`、`offer`、`landing_page`、`funnel` 给 `stage_evidence`。每项填 `reference` 和 `state`，状态为 `OBSERVED_CLEAR`、`OBSERVED_ISSUE` 或 `HYPOTHESIS`。 |
| 对照 Pixel、CAPI、GTM / GA4 | 用 `tracking-audit` 把测试事件标为 `mode: TEST`。归因资料可填 `attribution.meta` 和 `attribution.ga4`，分别写事件、窗口、模型、日期口径、范围及计数。打开已保存的 Tag Assistant 或 DebugView 观察做复核。 |
| 在多个账户分别读报表 | `multi-account-plan --workspace 工作区 --account-id 100 --account-id 101 --last-complete-days 7`。逐个核对计划和权限。有回执时交给 `multi-account-review`，分组条件包括币种、时区、归因、报告日期和 API 版本。 |
| 筛选多个已有广告组 | 先用 `workspace-plan --task ads-inventory` 形成并按既有权限读取具名库存计划，再用 `account-inventory-review --workspace 工作区 --monitor 监控目录 --account-id 100 --kind adsets --id 201 --id 202` 查询原快照。缺失只表示该快照未观察到。 |
| 从已有报表找系列、组或广告 | 用 `account-insights-slice --workspace 工作区 --receipt 回执 --account-id 100 --kind adset --id 201`。沿档案中实际返回的广告日行汇总；Meta 原生父级去重指标仍须按具名查询检查。 |
| 暂停广告或改变日预算 | 按原计划使用 `workspace-first-launch-update`。核对对象、子对象范围、币种、时区、观察时间、调整前后，确认独立审批有效期。结果不确定时用原审批和尝试读取回执。 |
| 设置本机日报、周报或观察告警 | `commerce-watch` 可用 `fresh_for_seconds` 和 `spend_alert_minor`。锁由当前进程持有，停止请求、上次成功和本机告警可持久回读。Mac 休眠或关闭进程时没有持续采集。按原目录用 `--resume` 接续。 |

[Meta 测试事件参数](https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/v25.0/) · [Tag Assistant](https://support.google.com/tagmanager/answer/6107056) · [GA4 DebugView](https://support.google.com/analytics/answer/7201382)

## 经营资料和旧教程

`business-readiness` 输入使用 `business-readiness-v1`，填写 `account_id`、`product_id`、`market`、`currency`、`as_of`、`facts`。每条事实有 `id`（如 `EV_OFFER`）、`kind`、`value`、`observed_on`。类型为 `PRODUCT`、`OFFER`、`DESTINATION`、`LANDING_PAGE_STATUS`、`NET_ORDER_MINOR` 和 `VARIABLE_COST_MINOR`。落地页状态用 `READY` 或 `ISSUES`。拿到相互矛盾的价格或成本时，先对照原始凭据，保留独立事实 ID。如果已有商品目录记录，可补 `catalog_reference`（`catalog_id`、`product_id`、`destination`、`currency`、`availability`、`reference`），核对是否匹配本次商品和落地页。

`meta-guidance-review` 输入使用 `meta-guidance-review-v1`，填写 `task: AD_CREATION`、`surface: ADS_MANAGER_WEB`、`market: US` 或 `GB`、`qualification`、`as_of` 和 `historical`。在 `historical` 填旧教程的 `title`、`published_on`、`ui`、`instruction`。资格值为 `CONFIRMED`、`INELIGIBLE` 或 `UNKNOWN`。现行官方材料的地区、日期和适用条件会回到结果。账户中的具体按钮以当前实际界面为准。 [Meta 美国广告说明](https://secure.facebook.com/business/ads) · [Meta 英国广告说明](https://en-gb.facebook.com/business/ads)

## 视频本地转写

已有 SRT 直接用 `media-analyze --subtitles 字幕.srt`。选用本机 Whisper 时，先从项目模型清单选择许可允许的模型，在下载来源核对 SHA-256。使用 `--model 模型.bin --model-sha256 预期摘要 --language en --fallback-subtitles 人工字幕.srt`。英文专用权重使用英文，多语言素材选对应多语言模型。模型文件缺失、摘要错配或本地转写失败时，结果保留实际原因。有手工字幕时可继续做时间码和素材观察。 [Whisper 模型清单与许可](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md) · [官方模型摘要](https://huggingface.co/ggerganov/whisper.cpp/blob/main/ggml-tiny.en.bin)

## Shopify 订单核对

1. 先核对店铺的 myshopify.com 域名、Shop GID、店铺时区、币种与订单创建日期范围。近 60 日订单的基础权限为 `read_orders`，更早订单另需 `read_all_orders`。读取库存摘要需加入 `read_products` 和 `read_inventory`。
2. 用 `shopify-read-preview --input 店铺范围.json` 核对权限声明。已有去标识回包时，用 `shopify-read-review --input 回包记录.json`。采用 Admin GraphQL `2026-10` 读取合同。源文件顶层为 `shopify-read-review-v1`，填写 `store`、`shop_id`、`timezone`、`currency`、`since`、`until`、`checked_on`、`reported_scopes`、`inventory` 以及依次排列的 `exchanges`。每个回包有 `operation`、`after`、`status`、GraphQL `body`，子分页另带 `object_id`。操作名有 `Orders`、`OrderLines` 和 `RefundLines`。
3. 与 Meta 消耗对照时，附加 `--spend 消耗.csv --account-id 100 --spend-timezone America/New_York --minor-digits 2`。供给同币种、同日期口径的 CSV。订单成本取历史凭据。库存单位成本来自当前库存项，不能填进历史订单利润。退款的订单汇总和订单行小计分别显示。
4. 没有合格回包时，走 `commerce-reconcile` 三表流程。真实连接器 OAuth 授权由店铺管理员办理，授予与任务相符的只读权限。当前本机回包入口不接收 OAuth 口令。

[Shopify API 版本](https://shopify.dev/docs/api/usage/versioning) · [订单查询](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders) · [权限](https://shopify.dev/docs/api/usage/access-scopes)

## 公共主页汇总及复盘证据

具名 Page 已登记时，先查看 `workspace-live-plan`。需要监控历史直播或权限暂不可用时，使用 Page 所有者提供的聚合报告。整理为 `page-live-export-v1`，填写 `page_id`、带 Page 前缀的 `post_id`、`source: PAGE_OWNER_EXPORT`、`since`、`until` 和 `observations`。记录含 `metric`、`observed_at`、`state`、`value`。保留缺失与确认为 0 的区别。运行 `page-live-export-review` 时给出原工作区。该入口可读聚合记录，直播连续读取仍需实际直播标识和可用权限。

复盘后用 `workspace-evidence` 查看原账户现有记录。写本地记录时增加 `--input 证据修订.json`。内容采用 `workspace-evidence-revision-v1`，填写 `entry_id`、`version`、`action`（`RECORD` 或 `REVOKE`）、`expires_on`、`result_sha256`、`approval_reference`、`strategy_id`、`source_version`、`material_sha256`、`plan_sha256`、`ad_id`。指定实际的 `--plan-dir` 可核原广告和素材。期限和撤回影响本地复盘，实际批准仍以管理服务为准。

[Meta Page Live Videos](https://developers.facebook.com/docs/graph-api/reference/page/live_videos/) · [Meta Page Insights](https://developers.facebook.com/docs/graph-api/reference/insights/)

## 同一广告任务

`media-buyer prepare` 读取准备材料范例中的业务、策略、追踪、Page 和创意。本机可选 `partner_briefs` 数组和 `competitor` 对象。竞品对象需要 `observation` 路径、`page_id`、`archive_id`，可附 `media_report` 路径和 `angle`。素材以原工作区已审核的摘要、概念和变体为准。

`media-buyer review` 接受 `ad_id` 和 `windows`，每期含 `receipt`、`since`、`until`，可各自附 `evidence`。单期也可在表顶层附 `evidence`。可另附 `stage_evidence`、`shopify_review`。Shopify 对账附 `spend` 和 `minor_digits`，要求时区一致。`media-buyer optimize` 接受 `schema: media-buyer-proposal-v1`、`action`、`ad_id`、`review_sha256`、`evidence`、`counterevidence`、`stop_condition`。日预算调整另需 `budget_minor`，单位为账户币种的最小货币单位。

`shopify-read-plan` 接受准备度表的店铺、日期和已知权限，并附 `schema: shopify-read-plan-v1`、`shop_id`、`timezone`、`currency`、`inventory`。只生成查询计划。由客户已授权的导出留下去标识回包再审阅。

### 具名出价、受众和素材变更

先用 `workspace-first-launch-update --mode plan --ad-id ID --action ACTION --change-file change.json` 预览，再单独核对操作前原值、权限和影响范围。`media-buyer optimize` 可在同一广告复盘下传入对应的 `change` 数据。三种受控范围如下。

| ACTION | change.json | 前置条件 |
| --- | --- | --- |
| SET_ADSET_BID_AMOUNT | `{"strategy":"COST_CAP","amount_minor":1800}` | 使用现有同一受支持出价策略和广告组预算。复查账户币种、计费事件、上下级暂停及受影响的全部广告。 |
| SET_ADSET_TARGETING | `{"geo_locations":{"countries":["US"]},"age_min":21,"age_max":60}` | 原受众也属于关闭字段的地域年龄档，普通广告分类，全部相关对象暂停。 |
| SET_AD_CREATIVE | `"805"` | 使用同批次另一条已审阅且有读回的 Creative；原广告和父对象暂停，核同一账户和主页。 |

上面的字段只是填写格式。现有具名读回超出支持范围时，保留审查结果，沿 Ads Manager 具名人工操作。审批、执行及恢复使用原受管通道。 [Meta Ad Set](https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-campaign) · [Meta Ad](https://developers.facebook.com/docs/marketing-api/reference/ad-group/)

按同一复盘窗口可附 `commerce_csv`，填写 `orders`、`refunds`、`spend`、`store_id`、`timezone`，以及可选的 `refund_basis`。使用 Shopify 路径或 CSV 路径之一。漏斗导出可附 `funnel`，以 `media-buyer-funnel-v1` 声明同一账户、Ad、日期、币种、时区、归因和证据编号。用 `coverage` 和 `counts` 标出 LPV、ATC、IC、Purchase 及缺项。这组导出与广告请求来源分开。
