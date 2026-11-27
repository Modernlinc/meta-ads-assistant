# 跨境电商广告助手

## 1. 安装到客户项目

Windows 提供[安装、升级和凭据手册](WINDOWS_CLIENT.md)，原生宿主待验收（`NOT_RUN_NO_WINDOWS_HOST`）。

下面是已验证的 macOS 安装路径。安装指南要求 Python 3.12 或以上。在 Codex 里打开专用的本地客户项目。解压安装 ZIP，将终端切换到解压后的 `codex-ads-live` 目录，再执行以下命令。把示例路径改成你自己的项目路径。

```bash
python3 install.py --project "/你的客户项目" --preview
python3 install.py --project "/你的客户项目"
python3 install.py --project "/你的客户项目" --install-status
```

出现 `PROJECT_INSTALL_VERIFIED` 后，从项目根目录运行 `python3 .agents/skills/codex-ads-live/run.py preflight`。如果已打开该项目聊天，重新打开项目或新建聊天，在输入框选择 `$codex-ads-live`，再说“为这个项目准备一项 Meta 广告试投，使用我现有的工作区”。Skill 会读取项目内的业务资料，并给出范围、缺项和下一步。已有安装先看[升级、验证与退出](INSTALL_RUN.md)。迁移只复制可移植配置时，旧报告、授权和运行中的计划需按各自原路径管理。

下文用 `RUN` 表示 `python3 .agents/skills/codex-ads-live/run.py`，`WORKSPACE` 表示 `workspace-init` 或已有项目返回的工作区目录，`spring-test` 是本次测试的短名称。也可以在 Codex 中用自然语言发起这些步骤，由助手调用同一个 Runner。

## 2. 接通自己的 Meta 资产

从原项目输入「使用 $codex-ads-live，检查这个项目的 Meta 接入状态」。先用 `workspace-connect`，按[客户首次接入与续接向导](CUSTOMER_SOP.md)选择：自有 App 和个人用户、已配置的官方宿主连接器，或企业系统用户。

自有 App 路线完成具名账户检查。官方连接器的查询和真实回执留在该宿主，导入清单可用于本地草案。企业系统用户使用单独的报表路线。已有客户按原路线续接。每次登录、授权和实际读取由本人确认。开户、角色、访问级别、App Review、业务验证及独立审批各按向导办理。[接入状态速查](META_CONNECT.md)

广告预算与平台服务费用分别确认。客户在 Ads Manager 核币种、金额上限、日期和账单，再决定是否审批投放。

## 3. 提供品牌、商品和素材

1. 在项目内建一个资料目录。整理品牌语气、目标市场、商品与变体、真实卖点、价格和优惠、配送与退换、典型购买疑问、许可范围和原始落地页。文档注明来源、观察日期和可用于广告的限制。订单及客服证据用聚合值或去标识记录。
2. 向 Codex 提供实际资料目录或商品网址，请它从获准读取的材料整理品牌事实、原始出处和待补项，再登记测试总预算、保护日预算、目标、币种及样本。工作区可持续保存品牌、假设和素材版本。对于选品、客服、商品页等补充任务，助手按需加载[随包或固定版本方法](../methods/ADAPTATION.md)，提交该项成果给同一广告任务。以后只复核变化的事实及受影响的计划。
3. 使用 `media-analyze` 审阅有使用权的原文件。看媒体 SHA-256、关键帧、音轨与字幕时码。转写需要本机工具及已核验的模型。没有模型时提供 SRT 或记录语音缺项。用 `creative-compare` 对照方案，将媒体报告一并交给命令。打开生成的 `comparison.html` 查看关键帧、分镜和文案。保存所选方案的 JSON，在准备投放时让 Codex 把它加入 `creative_choices`。创意、评审或来源变化后，需要重新保存选择。已有公开广告观察时用 `public-ad-inspect` 核对原 Page、广告资料库编号和字段覆盖。
4. 在 Events Manager 与已授权的 Tag Assistant 证据中核对数据集、事件名称、买家路径、域名和 UTM。对同一浏览器与服务器事件比对事件名称和 ID。测试样本单列。 [Meta Pixel 与服务器事件去重](https://developers.facebook.com/documentation/ads-commerce/conversions-api/deduplicate-pixel-and-server-events) · [Ads Library 查询能力](https://developers.facebook.com/docs/graph-api/reference/ads_archive/)

如用 `tracking-prepare` 生成过配置，请把 `TRACKING-CONFIGURATION.json` 的路径交给 Codex 加到准备材料的 `tracking_configuration`。工作区按同一数据集、目标链接和主事件核对测试与生产观察。自定义事件需要确认其真实业务含义及平台内对应配置。

## 4. 用同一任务准备和执行试投

复制[准备材料范例](../fixtures/media-buyer-intake.example.json)到项目并换成自己的具名资料。让 Codex 根据已审核资料填写，核对报告里的币种、预算、事件、缺项和证据。每项素材必须已有同一账户工作区的创意审阅，带概念、变体和 SHA-256。

```text
RUN media-buyer prepare --workspace WORKSPACE --case spring-test --input intake.json
RUN media-buyer plan --workspace WORKSPACE --case spring-test
RUN media-buyer status --workspace WORKSPACE --case spring-test
```

出现缺项时，根据 `intake.blockers` 补材料。在生成计划前可重新提交同一个任务。生成后要改落地页、数据集或测试范围，请先保留原任务，重新准备新任务。返回的 `plan_dir` 和 `customer_commands` 始终指向同一份首投计划。查看 `check_drafts` 和 `continue`，校对各草案缺项。预算需要同时符合现有工作区保护条件。受限目标或广告类型按实际平台资格核对。

通过原首投入口用 `PAUSED` 逐项建单，按 Campaign、AdSet、每份 Creative 和对应 Ad 核对父对象与回读。批准一次只对应当前范围。广告回执未知时按原尝试编号先读取恢复。待取得具体 Ad ID 后按条预览，再单独审批启用。管理员受管服务适用于 macOS。未配置受管服务时，原任务保留人工接续和回读。Windows 指南也列出这条路径，原生验收待完成。详见[首投与人工接续](FIRST_LAUNCH_CUSTOMER.md)和[管理服务配置](FIRST_LAUNCH_LOCAL_AUTHORITY.md)。

## 5. 复盘同一广告

用 `workspace-plan` 预览具名广告账户的日期、币种和时区，完成经授权的报表读取并保留归档。准备一或两个互不重叠的已结束日期窗口。指定实际 Ad ID 和实际归档位置。两期比较还需窗口长度、归因、事件和覆盖一致。

```json
{
  "schema": "media-buyer-review-v1",
  "ad_id": "804",
  "windows": [
    {"receipt": "/你的项目/已授权归档/RECEIPT.json", "since": "2026-11-20", "until": "2026-11-20"}
  ]
}
```

```text
RUN media-buyer review --workspace WORKSPACE --case spring-test --input review.json
RUN media-buyer optimize --workspace WORKSPACE --case spring-test --input proposal.json
```

复盘从原计划读回对象关系和实际报表。空报表、零、缺覆盖、归因证据不足和样本不足会分别显示。暂停或日预算调整以同一复盘的 `review_sha256`、Ad ID、证据、反证和停止条件生成待审计划。有限的地域和年龄变更、现有受支持出价策略的出价额、同批次已审阅素材也可以进入具名受控修改。超出这些范围先在 Ads Manager 人工审查。复盘时若素材有过替换，分别选择修改前后的完整观察期并保留修改回执。需要保留结论时用 `workspace-evidence` 记录来源版本、撤回与有效期。 [操作输入与回执](OPERATOR_CONTRACTS.md)

## 6. 店铺和直播数据

Shopify 使用 Admin GraphQL 2026-10。用 `shopify-read-preview` 判断日期和声明权限，`shopify-read-plan` 核对固定查询，用 `shopify-read-review` 读取商家已授权、去标识的回包。默认订单范围依 Shopify 的最近 60 天条件，超期需要对应授权的 `read_all_orders`。可以直接用 `commerce-reconcile` 导入订单、退款和广告消耗 CSV。产品成本或履约成本缺失时保留空值。按日期和时区核对后，可以把 Shopify 回包路径及消耗表一并附到广告复盘。 [Shopify API 版本](https://shopify.dev/docs/api/usage/versioning) · [Shopify 访问范围](https://shopify.dev/docs/api/usage/access-scopes) · [Shopify Orders](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders)

使用 `commerce-watch` 定期复核本机报告，设置最大循环和运行时间。电脑休眠、关机或进程停止期间，先查看状态和最新完成周期再恢复。Facebook 公共主页以具名 Page、已知 Live ID 或主办方汇总为依据，可通过 `page-live-export-review` 核对导出。真实连续读回按当次权限和现成直播对象验证。[运营与故障处理](OPERATOR_V1.md)

### 从平台原生报表到同一工作簿

先预览 `RUN native-insights --workspace WORKSPACE --level account --since 2026-11-01 --until 2026-11-07 --daily --output native-report`。检查账户、日期和授权范围后加 `--execute-read`，在本机交互终端确认。可选 `campaign`、`adset` 或 `ad` 并加 `--id`。覆盖人数与频次使用该对象、该窗口的原始指标。逐日报表中的人数和频次按日观察。

报表量大时加 `--report-mode async --job-state native-progress`。提交后若仍在运行，原命令加 `--resume`，保留原路径和范围。创建结果未知时保留记录，由管理员核对原调用。暂停等待期间可以退出，下次仍在同一项目接续。 [Meta 异步报表](https://developers.facebook.com/docs/marketing-api/insights/best-practices/v2.8)

如需实际读 Shopify，在工作区用 `shopify-bind` 登记店铺自己的 `myshopify.com` 域名、Shop ID、时区和币种，查看命令生成的本机凭据服务与账户标签。由管理员完成自己的应用授权，并在本机对应系统凭据管理器登记只读口令。用 `shopify-read` 选择交易日期、输出文件夹和具名授权。部分分页后，沿原输出目录和日期加 `--resume`。将 `SHOPIFY-SNAPSHOT.json` 与同一日期、币种、时区的账户逐日 `NATIVE-INSIGHTS.json` 分别作为 `shopify_snapshot` 和 `native_spend_snapshot` 交给 `media-buyer review`。现金退款和商品明细退款各有独立口径。已成功的退款交易只覆盖本次所选订单批次。缺成本时贡献保持未知。完成复盘后用 `RUN media-buyer report --workspace WORKSPACE --case spring-test --output report` 生成工作簿和文字报告。 [Shopify 权限](https://shopify.dev/docs/api/usage/access-scopes) · [退款及交易](https://shopify.dev/docs/api/admin-graphql/latest/objects/Refund)

### 已有对象续建和规则

目标明确时，选用现有 Campaign 新建暂停的 Ad Set 和 Ad，或在已有 Ad Set 下新建暂停的 Ad。复用父级的编号与账户归属需要核验。工作区会保留每条 Ad 的对应素材、组与报表入口。已有父组启用另有实际支出和投递边界。原生创建在未满足启用保护时保持暂停。

在受管 macOS 上续建的广告，如需预览启用范围，用原 Ad 的具名预览证据和实际父级配置。为启用审阅加 `--parent-budget ADSET DAILY 金额最小货币单位`，或填实际 `CAMPAIGN` 和 `LIFETIME` 策略。受管服务会再次核对两级归属、活动状态、总额限制、窗口和预算金额，独立审批只覆盖该条广告。

素材还未选定时，可以先完成原账户的暂停结构。请让 Codex 在已选策略中使用 `sequence: STRUCTURE_FIRST`，每组暂不选素材。原任务回传 `FIRST_LAUNCH_STRUCTURE_RECORDED` 后，提供新素材和原计划目录。用新任务指定 `existing_structure` 和原结构中已回读的 Campaign、Ad Set，从 `APPEND` 补建 Creative 与 Ad。组预算、原账户和原对象继续按其现有批准范围处理。

若已配置新版 macOS 管理服务，可用 `native-rules list`、`get`、`history` 预览具名广告账户查询，授权后读取规则及有界历史。规则创建、改名、重配停用规则条件、停用和移除沿 `native-rules` 生成具名审批包，由管理员分别批准。新规则保持停用，后续如需启用，由资产管理人核所选实体与长期影响。本机轮询不会创建 Meta 平台规则。[Meta SDK 规则字段与操作](https://github.com/facebook/facebook-python-business-sdk/blob/b08e5c1d36a2adad3c4ede6578654c8eaca5f225/facebook_business/adobjects/adrule.py)

可只要求 `native-insights --include-object-context` 补读所选广告与广告组的实际关联、目标和转化信号。对自定义转化应查事件定义，并和报表窗口、账户归因设置比较。复盘中的重点诊断队列会保留账户和日期，先输出待确认查询。

查看存量对象时，先在同一工作区用 `native-inventory list adset --parent-kind campaign --parent-id CAMPAIGN` 预览，也可用 `get ad --id AD` 核单条。加 `--execute-read --output 新目录` 后在本机确认。如果结果保留了下一页，使用原参数与 `--resume-from 原目录/NATIVE-INVENTORY.json`，选一个新输出目录。查看活动时指定 UTC 日期范围。 `list activity --parent-kind adset --parent-id ADSET` 会读取该组活动，账户活动可直接在账户层读取。 [广告对象](https://developers.facebook.com/docs/marketing-api/reference/ad-group) · [广告组活动](https://developers.facebook.com/docs/graph-api/reference/ad-campaign/activities/)

需要检查较早订单在本期到账的退款时，店铺有相应权限且客户批准扩展范围后，加 `--include-cross-window-refunds`。本机确认语句会单列这部分。结果以扫描覆盖和订单交易顺序为准。选了 `--inventory` 时，本机确认也会单列库存与现行成本。现行单价仅供记录和讨论，历史订单成本仍须使用对应时点的库存或账务凭证。

若要加入实际会话漏斗，提供同店、同日期和时区的 Shopify Analytics 或 GA4 导出。请 Codex 按导出定义整理日期、会话数、发起结账的会话数、完成购买的会话数，并把原文件摘要和范围交给 `store_funnel_export`。这份报表和订单、Meta 归因分别列示。缺日时保留部分覆盖，等待补齐后再算整段比率。

轮播可选择同一账户已存在的 2 至 5 个图像。逐卡核对内容权利、站内目的地和顺序，受管执行会重新读取图库。URL 标签应和落地页现有参数去重。启用核对会读预算所属层以及实设的每日或生命周期金额。生命周期预算可能突破保护日限时保留暂停。已有父级的续建可先准备、回读和预览，再单独核对既有控制配置。

若当前账户的目标和广告组具备传统动态创意资格，可在已选策略的对应组增加 `dynamic_media`，提供账户内 2 至 5 张已核权属图像及备用正文、标题。每个此类组编译一条动态素材广告，使用同一落地页，并读回广告组模式及素材摘要。Meta 不支持该模式的目标、账户或版位时，先处理实际资格提示，另行决定是否改用普通创意。此流程使用手选素材。 [Meta 动态素材字段](https://developers.facebook.com/docs/marketing-api/reference/ad-asset-feed-spec/)

已有具名受众可先用 `native-audience lookup` 核对归属和可投递状态。需从现有种子生成相似受众时，用 `seed-review` 查观察到的规模与资格，再用 `create-lookalike` 预览该国比例及范围。经过独立批准后才能提交。收到 ID 后继续读回可投递状态，再把该 ID 带到目标广告组。目录可以用 `catalog-review` 核对商品集、导出价格、落地页及事件内容 ID。需要动态目录广告时按本任务提示补齐平台目录流程。遇到拒审，`ad-policy-preflight` 根据客户提供的具名证据生成待审改稿与核对步骤。

### 复用存量广告和复盘后的素材结论

已有广告可先用 `native-inventory get ad --id AD` 保存具名回执。用于具名调整时，给 `workspace-first-launch-update` 指定原工作区、`--ad-id AD`、`--existing-page PAGE` 和 `--existing-ad-snapshot NATIVE-INVENTORY.json`。影响整组或整系列时，再用 `native-inventory list ad` 保存同一父级的完整清单，作为 `--affected-ads-snapshot`。先预览拟调整对象，再由受管服务读回同一广告的 Page、创意、对象关系和影响列表，最后分别确认写入。用 `SET_ADSET_END_TIME` 可在全链暂停时缩短已有停止时间，传入 UTC 时间。延长或变更开始时间时，需要在 Ads Manager 单独核对预算和排期。现有广告的替换创意使用具名人工复核路径。

对由本任务创建且已复盘的广告，可以说「把本次确实使用的素材记录回原品牌资料」。工作区仅在具名复盘含完整日期、有展示且附有同一创意回读时，才通过 `media-buyer learn` 把原概念记为已使用，保留其他候选。复盘涉及后续替换素材、历史证据已变更或回读缺失时，先重新核对，避免把不同版本的效果混在一起。
