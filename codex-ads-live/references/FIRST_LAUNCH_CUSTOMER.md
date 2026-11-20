# 客户安装后接续第一条 Meta 广告

使用原工作区准备首投计划。具名读取用客户的本机只读凭据；实际创建、预览与启用各按本次明确批准的范围执行。使用自动建单的管理员请先完成[受控执行端设置](FIRST_LAUNCH_LOCAL_AUTHORITY.md)。

## 安装及日常回来

1. 从安装件运行 `install.py --project PROJECT --preview` 核目录，再按 [安装说明](INSTALL_RUN.md)在客户项目安装。从已安装目录运行下文的 `python3 run.py …`，在 Codex 中打开客户的原项目。
2. 客户管理员按 [首次接入](CUSTOMER_SOP.md)确认 Business Portfolio、App、广告账户和公共主页归属，选本次默认资产。对已有文档的角色和授权复查缺失项。客户本人办理 Meta 登录、权限申请和审核。已有同范围只读连接可复用其本机安全引用及具名观察。
3. 客户首次用 `workspace-first-launch-plan --workspace W` 建计划，再用 `workspace-first-launch-check --workspace W` 补全四份草案、目标、事件、素材、日预算和时区。W 是原工作区。默认 PLAN 为该工作区的 `first-launch-plan`。使用自选计划目录时，后续命令都带 `--plan-dir PLAN`。
4. 用 `workspace-first-launch-handoff --workspace W` 看下一步。再次使用时打开原项目，运行同一命令。它继续核对保存在原 PLAN 的观察文件与启用审阅。已持久保存的四步对象和复盘决定仍由原进度账本维护。需要保留某个时点的交接，使用 `--output NEW` 存入全新目录。

## 核同账户首图

优先选广告账户已有并获准使用的商品图片。只有本地商品文件时，由素材负责人确认要用的具体文件和权利，客户明确批准上传范围后，实施人在 [Meta 广告管理工具](https://adsmanager.facebook.com/)选本次广告账户，从广告编辑器的素材选择区进入图片管理并上传单张图片。上传前检查界面身份及账户，保留原草案和投递开关的状态。界面出现要同时发布广告的操作时，先退出该发布流程，改由客户已获准的账户素材管理通道处理。

在同一账户的素材库核对上传结果。需要取得或验证图片哈希时，客户在 [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/)选批准的 App 和账户连接，以获准的只读权限执行 `GET /act_<ACCOUNT>/adimages?fields=hash,name`。已有哈希时用 `hashes` 参数限定，按返回的名称和图片预览确认本次材料。出现多条同名、权限不足或归属未证实时，先查原素材证据。用本机商品文件的 SHA-256 填创意草案的 `context.material_sha256`，用该账户返回的 `hash` 填 `parameters.image_hash`。 [Meta 账户图片接口](https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/adimages)

刷新交接，复制 `image_observation_template` 填证据编号与带偏移的观察时间，存为 `PLAN/image-observation.json`。工具核对账户、计划、Page、本地材料摘要和平台引用均匹配。证据截图保留在客户项目，观察 JSON 只填模板列出的安全字段和 `EV-…` 引用。令牌留在 Meta 或已授权的本机安全引用。

## 暂停创建和预览

1. 查看 `workspace-first-launch-progress --workspace W`，保存当次 `creation_request` 和 `expected_object`。每次先核具名对象及四步参数，分开确认当次创建范围，再用 `--reserve` 留下原动作摘要。在返回的 `stages` 核对该次预留的 `request_sha256`。由客户授权的实施人在 Meta 选择同一账户，依次处理 Campaign、Ad Set、Creative 和 Ad。Campaign、Ad Set 和 Ad 的配置状态须为暂停。Creative 是绑定本次 Page、图片与链接的素材对象。
2. 实施人先核发布摘要。普通后台遇到多层一起发布、不能独立创建 Creative 或无法保证暂停状态时，保留原草案，交由客户已授权、能逐对象创建且能独立回读的 Marketing API 实施通道。实施通道按原 `creation_request` 的 path、表单和摘要逐对象处理，认证由通道提供。缺该通道时停在对应步骤。保留本次账户、计划、草案和证据引用，等同一范围接通后续接。
3. 每次核对 Meta 返回的原 ID、配置状态和父子关系。需要核对对象字段时，在同一只读授权下以该对象 ID 查询 Meta，按进度回执的 `expected_object` 和官方字段核对。填写进度返回的 `readback_template` 并保存为 JSON，用 `workspace-first-launch-progress --workspace W --readback OBSERVATION` 登记。出现超时、草稿或未知，先回原账户和当次摘要查询原对象。遇到草稿时保留 `DRAFT`，发布需要对应范围的批准。
4. 对四步回读关联到的原 Ad，在 Ads Manager 的广告级选中该 ID，查看 Facebook 移动动态成品预览。以本次界面截图的脱敏凭证编号填 `preview_observation_template`。仅取得 Creative 预览时，继续补同一 Ad 的成品预览再进入启用审阅。 [Meta 广告对象](https://developers.facebook.com/docs/graph-api/reference/adgroup/) · [Meta 预览格式](https://developers.facebook.com/docs/marketing-api/reference/generatepreviews/)

## 核父级和启用同一个 Ad

准备 `workspace-first-launch-activation-review --workspace W --preview-observation PREVIEW --start-at START --end-at END --maximum-spend-minor CAP --output PLAN/activation-review`。替换各占位符。时间带偏移，结束时刻排除，总额用账户币种的最小单位。另存审阅时，交接用 `--review FILE` 指向本轮实际的 `activation-review.json`。

1. 按 `parent_before_template` 在 Ads Manager 中用 ID 找到该 Campaign、Ad Set 与 Ad，在账户盘点或后台展开完整系列，核对所有子广告及配置状态。原 Ad 必须仍暂停。记录启用父级后可能受到影响的 Ad 集合。只有影响范围已证实仅限本条时才继续。资料有缺页、父子关系冲突或还有其他广告可能受影响时，保留现态。将观察存为 `PLAN/before-parents.json`，重开接续命令看 `parent_approval_scope`。
2. 如需打开父级，先由客户逐项批准本次明确列出的 Campaign 和 Ad Set 转换、牵涉广告、预算与停止时刻。实施人保持原 Ad 暂停，仅在各自层级切换具名对象。每次先回读该 ID 的配置与影响范围。两个父级原已启用时，保存现有状态和读取证据即可。超时先查原对象当前状态，保留原操作范围。
3. 在任何单 Ad 启用前，核对实际可执行的支出限制和停止时刻已生效。在 `parent_after_template` 填实际控制值、`control_evidence_ref`，以及需要时的独立 `parent_approval_ref`。将观察存为 `PLAN/after-parents.json`。控制额度必须覆盖已审阅日预算，且落在批准总额内。单凭日预算、观察日期或人工提醒无法证明有可执行的总额和停止限制。若当前 Meta 配置无法满足，保持 Ad 暂停，请预算负责人复核一套可实施的方案并重新审阅。
4. 由客户另行批准原 Ad 的启用、额度及时窗。只在批准窗口内，实施人重新核对该 Ad 仍暂停、两级父对象及支出控制，准备 `ad_observation_template`。点击前把原尝试时刻、单 Ad 批准的证据编号和 `UNKNOWN` 状态存为 `PLAN/ad-observation.json`。点击前，先运行 `python3 run.py workspace-first-launch-handoff --workspace W --ad-observation PLAN/ad-observation.json --reserve-manual`。仅本人已核实从未执行、该窗的批准仍有效且此次返回 `manual_registration = FIRST_PREACTION_RESERVATION` 时，才在广告层切换该 Ad 一次。重复预留、导入旧记录或结果未知时，直接查询原 Ad 的状态，保留原尝试。完成观察后再次运行不带 `--reserve-manual` 的同一交接命令登记结果，按原 ID 回读配置与实际投递状态。核对结果后存同一尝试的更新，保留初始观察。发现未知结果或账户变化时回读原 Ad 和窗口，保持原尝试编号和时间。

以上记录用于接续。客户的实际批准、操作与证据由相应人员在 Meta 和客户项目里保管。已走可信宿主的原尝试继续沿其原恢复路径。普通安装的本地接续命令始终只处理观察和审阅。

## 同 ID 投递与下次复盘

在本次具名授权下使用 [工作区广告分析](INSTALL_RUN.md#下次打开项目)的 `ads-analysis` 路线保存同账户运行归档。用 `workspace-first-launch-delivery --workspace W --run-receipt RUN_RECEIPT --since DAY --until DAY --activation-review REVIEW` 复盘原 Ad。运行回执使用该次监测运行 ID 目录内的 `RECEIPT.json`。日期按账户时区和原查询填。下次打开原工作区仍沿该计划、Ad 和批准记录继续。

配置状态为 ACTIVE 只说明开关。记录首投实际投递需有同 Ad 在批准窗口内的正展示。归档日桶须完整落在有证据的实际批准及控制窗内，且须在该次尝试之后。跨出短窗口时保留待核，有权限时再用 Meta 后台能对应同 ID 与时段的报表证据。支出需有实际金额和币种。购买与营收按各自证据另列。
