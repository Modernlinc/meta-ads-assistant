# 品牌、素材与广告复盘

在自己的 Codex 项目打开 `$codex-ads-live`。首次使用先完成[安装和账号接通](INSTALL_RUN.md)，再按[首投步骤](FIRST_LAUNCH_CUSTOMER.md)选择目标广告账户和 Page。下面的命令在客户项目中运行。

```sh
RUN=.agents/skills/codex-ads-live/run.py
WS=.codex-ads-live/client
python3 "$RUN" workspace-show --workspace "$WS"
```

已经使用过其他工作区目录时，请将 `WS` 改成那个目录。

## 保存品牌与选题

在项目内放一份获准使用的品牌简报和素材文件。简报建议包括产品和目标客户、真实可证实的卖点、常用措辞、禁止用语、主要市场和落地页。让 Codex 先读取本项目原档案，再把新增事实、来源和待验证假设整理成一份 `creative-research.json`。可用下面这句话开始。

> 使用 $codex-ads-live，读取当前客户的目标和品牌档案。我会给你品牌简报、获授权的素材与商品页。按卖点、钩子、转化点、真人感逐项列证据和改法。每项引用可定位的截图、口播、字幕或文稿。画面没有看到就标记未观察。把选题与素材 SHA-256 关联起来，先给我核对。

```sh
python3 "$RUN" workspace-creative-research --workspace "$WS" --input creative-research.json
python3 "$RUN" workspace-creative-research --workspace "$WS"
```

JSON 顶层用 `brand`、`sources`、`concepts`、`learning`、`reviews`。未更新品牌时填 `null`，未补的列表填 `[]`。

| 字段 | 填写方式 |
| --- | --- |
| `brand` | `positioning`、`voice`，写成简短品牌定位和语气。 |
| `sources` | 每项填 `id`、`kind`、`url`、`observed_at`（含时区）、`product`、`fact`。来源类型可为商品事实 `PRODUCT_FACT`、客户语言 `CUSTOMER_LANGUAGE`、自有报告 `OWN_PERFORMANCE` 或公开观察 `PUBLIC_OBSERVATION`。 |
| `concepts` | 每项填 `id`、`source_ids`、`hypothesis`、`variants` 和 `state`。变体填 `id` 与 `brief`。选题进入待用池填 `AVAILABLE`，确认已用后把原项状态改为 `USED` 保存。 |
| `learning` | 每项填 `id`、`source_id`、`scope`、`finding` 和 `state`（`WATCH`、`ACTIVE` 或 `RETIRED`）。 |
| `reviews` | 用 `asset_sha256` 和 `concept_id` 固定素材及选题。每条 `observations` 填 `id`、`asset_sha256`、`kind`、`evidence_ref`、`time_ms`。类型为 `VISUAL`、`SPOKEN`、`SUBTITLE` 或 `SCRIPT`。 `comments` 含 `selling_point`、`hook`、`conversion_point`、`human_feel`，每项填 `observation_id` 和 `comment` 或 `null`。另填 `weakest` 与 `suggestion`，缺项可用 `null`。 |

查询公开广告时，准备查询 JSON，例如 `{"query":"ceramic cup","countries":["US"],"status":"ACTIVE","date_min":null,"date_max":null}`。

```sh
python3 "$RUN" public-ad-query --input library-query.json
```

如具备 Meta Ads Library API 授权，可从交互终端追加 `--execute-read` 并保存返回的 JSON。也可让 Codex 按实际导出整理 `scope`、`records`、`coverage`。每条观察保留 `ad_archive_id`、`page_id`、`source_url`、`observed_at`、`body`、`detail` 和 `angle_hypothesis`。详情须保留相同两个 ID。然后导入同一客户工作区。

```sh
python3 "$RUN" workspace-public-ad-observations --workspace "$WS" --input library-output.json > library-receipt.json
python3 "$RUN" workspace-creative-research --workspace "$WS" --public-observations library-receipt.json --product "陶瓷杯"
```

公开观察提供可引用的文案和采样范围。订单、CPA、ROAS 请用自己的账户报告和订单资料。API 无权访问时，从获准使用的实际观察整理输入。[Meta Ads Library API](https://www.facebook.com/ads/library/api/)

## 选素材与准备首投

将概念、变体、素材摘要用在同一份首投计划。多个素材时可以重复该参数，最多六份。先准备计划和所有草案，再保存本机执行次序。

```sh
python3 "$RUN" workspace-first-launch-plan --workspace "$WS" --select-creative CONCEPT_ID VARIANT_ID ASSET_SHA256
python3 "$RUN" workspace-first-launch-check --workspace "$WS"
python3 "$RUN" workspace-first-launch-progress --workspace "$WS"
```

同一批次共用一个 Campaign 和 Ad Set，之后按每份素材创建 Creative 和暂停的 Ad。首次批次预留后，地区、预算、文案、素材引用或其他内容变化会要求重新审阅。逐项核对现有 ID 和状态，未决时使用原尝试只读恢复。预算由 Ad Set 草案填写。购买、线索事件和缺失的 Pixel 请按实际配置提供。

### 上传图片或视频

已有图片时，可按[首投步骤](FIRST_LAUNCH_CUSTOMER.md)使用本账户已核对的 `image_hash`。需要上传时，从原计划生成一个明确的文件请求。

```sh
python3 "$RUN" workspace-first-launch-media --workspace "$WS" --kind IMAGE --file "素材.jpg" > media-packet.json
```

视频将类型改为 `VIDEO`，文件用 MP4 或 MOV。图片支持 JPG、JPEG、PNG，大小最多 10 MiB。视频最多 100 MiB。管理员按[受管服务部署及批准说明](FIRST_LAUNCH_LOCAL_AUTHORITY.md)核对该请求和原文件。批准后执行一次，再沿原包核对。已有上传尝试时直接使用恢复模式。

```sh
python3 "$RUN" workspace-first-launch-media --workspace "$WS" --packet media-packet.json --mode dispatch
python3 "$RUN" workspace-first-launch-media --workspace "$WS" --packet media-packet.json --mode recover
```

只有 `READY` 的同账户回执才能用于受管建单。图片在 Creative 参数填回读的 `image_hash` 和 `media_scope_sha256`。视频填写 `video` 对象内的 `video_id` 和本账户已核对的 `thumbnail_hash`，再填视频的 `media_scope_sha256`，外层 `image_hash` 留空。两种格式均须填写 Page、落地页、主文案、标题及 CTA。缩略图可用本账号的既有图片。图片与视频文件的摘要放在对应草案的 `context.material_sha256`。上传返回 `PROCESSING` 时稍后读原 ID。

## 预览、调整与复盘

按[首投步骤](FIRST_LAUNCH_CUSTOMER.md)核对实际 Ad 的预览。当前批次的原首投启用和复盘入口指向最后一个完成的 Ad，其他条目保持原记录与暂停设置。启用仍由具名范围和实际预算控制单独批准。

`workspace-first-launch-update` 先预览原对象范围。支持暂停这条 Ad、暂停本批 Ad Set 或 Campaign，或为整链均处暂停时调整原广告组日预算。父级调整前需读到完整受影响 Ad 清单。日预算使用本账户币种的最小单位，例如 USD 15.00 填 `1500`。审批和原尝试读回的命令见[管理员说明](FIRST_LAUNCH_LOCAL_AUTHORITY.md)。

```sh
python3 "$RUN" workspace-first-launch-evidence --workspace "$WS" --since YYYY-MM-DD --until YYYY-MM-DD
python3 "$RUN" workspace-first-launch-delivery --workspace "$WS" --run-receipt 报表回执.json --since YYYY-MM-DD --until YYYY-MM-DD
```

在管理员授予本次读取范围后，给第一条加 `--execute-read`，可将回执保存并传给第二条的 `--evidence`。同一账户、日期、归因和 Ad ID 的报告可检查展示、花费、点击与已请求的转化。账户重点集合按已观察花费排序，包含跨越 90% 的那条广告。先看实际覆盖范围，再读与当前目标有关的建议。复盘建议供决策和下一次核对使用。
