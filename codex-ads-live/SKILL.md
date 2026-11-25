---
name: codex-ads-live
description: 在当前客户项目中接通具名 Meta 广告账户，复用其只读连接，准备电商广告、按批准范围接续首投，读取同一广告或具名公共主页的记录。
---

# Meta 广告与公共主页

第一次使用，先询问客户要接的具体广告账户。客户已有工作区时，先从原目录运行 `workspace-route`，沿用已确认的 App、账户和默认资产。只有客户明确切换资产、授权失效或核对结果不匹配时，才复查相关项目。可从[安装说明](references/INSTALL_RUN.md)和[账户接通流程](references/CUSTOMER_SOP.md)进入。

## 接入广告账户

1. 检查 macOS、Python 版本和项目位置，通过安装器核对随包文件。在客户项目中运行已安装的 `run.py`。运行 `python3 run.py --help` 可查看当前版本实际支持的命令。
2. 按账户接通流程准备已存在的 Business、App、广告账户和有权操作的用户。让客户本人登录、确认资产并在本机系统密码框录入凭据。只在档案中保存非秘密引用。
3. 使用 `workspace-connection-prepare` 生成具名计划。请客户检查目标和查询范围，再以 `workspace-refresh` 在本地交互终端读取。分别报告实际返回状态、目标 ID、时间和覆盖范围。权限不足时指出要核查的资产和权限。未取得真实回执时保留待接通状态。
4. 日常复用原工作区。客户要求“昨天的广告”时使用账户时区，确认日期、币种及报表层级。完整零行、空值、部分分页和明确的数字零分别报告。广告与主页使用独立资产和访问范围。

## 客户接下来想做什么

| 需求 | 处理 |
| --- | --- |
| 查账户、广告或广告报表 | 先用 `workspace-route` 定位资产；需要新数据时预览具名计划，由客户确认读取。 |
| 准备一条电商广告 | 收集落地页、商品、素材使用权、目的、预算币种及上限。在原工作区生成并检查草案。[首投操作](references/FIRST_LAUNCH_CUSTOMER.md) |
| 继续同一条广告 | 使用 `workspace-first-launch-handoff` 加载原计划、对象关系、已用批准与待办。未知结果沿原尝试核对。 |
| 打开自动建单或启用 | 只在客户已配置管理服务、独立写授权和逐项批准时操作。[管理员说明](references/FIRST_LAUNCH_LOCAL_AUTHORITY.md) |
| 读取公共主页或查看直播记录 | 先确认 Page ID 及对应权限，使用 `workspace-route` 进入该主页，按实际可读取的字段判断覆盖。[工作流](references/WORKFLOWS.md) |
| 保存品牌、来源、选题和素材评语 | 用 `workspace-creative-research` 读取现有资料或导入本次补充。将选定素材的 SHA-256、概念与变体传给 `workspace-first-launch-plan --select-creative`。有授权文稿时逐条引用观察，未看到画面时保留缺项。[品牌及创意操作](references/CREATIVE_AND_DELIVERY.md) |
| 导入公开广告观察 | 先用 `public-ad-query` 查范围，具备 Ads Library API 权限才执行。已有导出可直接使用 `workspace-public-ad-observations`，将公开观察及推断作为两类消息保留。 |
| 上传和复用图片或视频 | 以 `workspace-first-launch-media` 核对计划与素材摘要。上传后用 `recover` 核对原尝试。权威服务封存为 `NOT_SENT` 且有新批准时才用 `continue`。同一素材经新计划获准核对后用 `reuse` 并将就绪引用写入 Creative。[操作说明](references/CREATIVE_AND_DELIVERY.md) |
| 调整或复盘同一条广告 | 用 `workspace-first-launch-update` 预览具名暂停或日预算范围。用 `workspace-first-launch-delivery` 复盘原 Ad ID，按需补原创意和活动证据。[操作说明](references/CREATIVE_AND_DELIVERY.md) |
| 商品、创意、核算和复盘方法 | 用 `method --intent` 加上 `ads_plan`、`creative`、`analytics` 或 `positioning` 选择随包方法，按已提供的数据标出结论、假设和缺项。[随包方法](methods/ADAPTATION.md) |

本地草案、模拟练习、平台操作和具名读取都有独立结果。客户只批准接通账户时，到交付本次读取回执即停止。另行涉及花费的动作应有明确对象、可执行的金额限制、时间和客户批准。
