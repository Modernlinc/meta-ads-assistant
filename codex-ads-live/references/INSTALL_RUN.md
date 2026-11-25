# 使用说明

第一次使用先完成安装和账户读取。准备广告时，向 Codex 提供商品图、落地页、投放地区、购买目标、账户币种、预算上限、开始和停止时间。Codex 会沿原工作区核对还缺什么。

## 安装到自己的项目

1. 在 Mac 上打开 Codex，选择准备用于这个客户的现有项目文件夹。若是新客户，可先在 Finder 建立专用项目文件夹再打开。记住它的绝对路径。
2. 将本套文件中的 `codex-ads-live.zip` 解压。打开终端，在解压后**能同时看到 `install.py`、`run.py` 和 `SKILL.md` 的目录**执行下面的命令。先将示例路径替换为实际路径，保留引号。
3. 先核对安装预览列出的项目和目标位置。确认无误再执行安装和读回。若显示同名安装冲突，保留项目原件，让 Codex 核对既有安装回执和升级计划。

```sh
PROJECT="/你的项目绝对路径"
python3 --version
python3 run.py preflight
python3 install.py --project "$PROJECT" --preview
```

Python 需为 3.12 或更新版。查看预检输出中的 `system`、`checks.operating_system` 和 `checks.package_integrity`。本指南在 Mac 本机运行，预期显示 `Darwin`、`IN_SCOPE`、`PASS`。若显示 `Linux / OUTSIDE_V1_SUPPORTED_OS`，说明当前执行位置在 Linux 环境。请到 Mac 本机终端打开同一客户项目后再继续，无需重新登记 Meta 账户。预览显示 `INSTALL_PLAN_READY` 后执行：

```sh
python3 install.py --project "$PROJECT"
python3 install.py --project "$PROJECT" --install-status
cd "$PROJECT"
python3 .agents/skills/codex-ads-live/run.py --version
```

看到 `PROJECT_INSTALL_VERIFIED`，记下最后一条显示的版本。打开相同项目，在 Codex 输入「使用 $codex-ads-live，只检查本项目的安装位置，告诉我加载的是哪个 SKILL.md」。路径应指向项目内的 `.agents/skills/codex-ads-live/SKILL.md`。找不到时，先核对项目路径，再重新打开该项目。[Python macOS 下载](https://www.python.org/downloads/macos/) · [Codex Skill](https://developers.openai.com/codex/skills/)

已有 V1.6R1 项目时，先在独立目录安装 V1.6R2。日常配置可按 `python3 install.py --help` 的 `--upgrade-from` 预览分阶段接续。该步骤只接续可移植配置。若需恢复原媒体尝试，请用 R2 的 `run.py` 明确指定原 `--workspace`、原 `--plan-dir` 和原媒体请求包。受管端也须按[管理员更新步骤](FIRST_LAUNCH_LOCAL_AUTHORITY.md)切换到 R2，并使用原状态目录。

## 查到自己的广告账户

打开[账户接通流程](CUSTOMER_SOP.md)。想快速验证自有账户，可用本人终端临时输入；希望日后在同一个工作区持续使用，可以直接选钥匙串方式。两条路径择一即可。账户读通的标志是本次指定账户返回 `NAMED_ACCOUNT_READ_PASSED`，同时 ID、币种、时区与 Meta 里的目标一致。

## 准备和核对第一条电商广告

打开已安装的项目后输入：

> 使用 $codex-ads-live，在上次的客户工作区，用这个已获授权的商品图和落地页，按网站购买准备首投。先显示广告账户和 Page 的尾号、购买事件、市场、文案、币种、预算与停止时间。我想先看草案。缺少图片引用、Pixel 或资格时逐项告诉我，等我确认再安排远端操作。

账户和 Page 已映射时，Codex 可用 `workspace-first-launch-plan` 生成 Campaign（广告系列）、Ad Set（广告组）、Creative（创意）和 Ad（广告）草案，并用 `workspace-first-launch-check` 列出所缺字段。首次只读连接若采用临时输入，先按《账户接通流程》的“以后沿用同一个项目”建立工作区；只核过账户的客户，还需补本次 Page 和素材的使用范围。

| 现在想完成的事 | 客户看到的结果 |
|---|---|
| 准备草案 | 在本地核对原图、购买目标和落地页。新图需要远端上传时先确认上传范围。在 Meta 图片库使用现有图时，核对它属于本次广告账户。 |
| 创建暂停对象 | 连接服务具备相应资格、负责人逐项批准后，按依赖建单并读回原编号。系列、广告组和广告都核对暂停状态。只保存本地草案时，仍可修改计划。 |
| 查看广告 | 在已配置的 macOS 宿主上，用同一新 Ad ID 取得 Facebook 移动动态预览，核对 Page、实际文案、落地页和版位。只有 Creative 预览时，先补看该 Ad 的成品。 |
| 准备花费 | 实施人员核查系列和广告组状态及影响范围。在模板或后台填写数字后，还要确认平台确有可执行的支出上限和停止时间。启用父级与这一条 Ad 分别批准。 |
| 查看结果 | 按同一账户、同一 Ad ID、账户时区及批准窗口取数。正展示证明这一窗口实际投递，花费另看币种及金额，订单另看可核对的成交证据。 |

自动写入路径需管理员完成该项目的独立审批与连接配置，并按计划核实资产和权限。尚未配置时，可继续做本地草案；项目已有具名获批的人工投放流程时，实施人可以在 Meta 做对应步骤，将原对象、预览及回读关联回原计划。后台会联动其他广告、无法保证暂停或此次批准范围不清时，保留草案等负责人核对。首图、父级和启用的可选人工步骤，管理员可在已安装项目内打开 `.agents/skills/codex-ads-live/references/FIRST_LAUNCH_CUSTOMER.md`。读取账户成功仅完成上一步的只读连接。

## 下次打开项目

回到上次的客户项目，输入「使用 $codex-ads-live，继续上次的广告，用原工作区和原 Ad。先告诉我已有决定、数据区间、上次读取时间和下一次复核日期。」默认首投保存在工作区的 `first-launch-plan`；此前手动选择过计划目录时，指定原路径。日常只处理当前任务的目标、权限变化和到期项，无需每次重新盘点全公司资产。

报告为零、没有返回数据、权限失效是三种不同结果。若整日数据超出批准的小时窗口，只能报告该日汇总，待补能归属到窗口的记录。发送中断或返回结果不明时，恢复原计划先查原动作及对象，避免重复建单。
