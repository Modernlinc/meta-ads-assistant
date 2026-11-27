# Windows 安装、凭据和验收步骤

本候选提供 PowerShell 和凭据管理器操作步骤。Windows 实机安装、Codex 桌面加载及凭据管理器原生读写待验证（`NOT_RUN_NO_WINDOWS_HOST`）。本轮实机验收已获用户豁免（`PASS_BY_USER_WAIVER`）。现有证据覆盖 Win32 模拟和含 UTF-8 BOM 的输入。

## 1. 检查 Windows、Python 和项目位置

1. 从开始菜单打开 **运行**，输入 `winver`。优先使用仍受支持的 Windows 11，例如 25H2。Windows 10 常规 22H2 服务已于 2025 年 10 月 14 日结束。Windows 10 请由设备管理员核对实际版本、是否仍处于 LTSC 支持期或已参加且仍受覆盖的 ESU；也要核对当前 ChatGPT 桌面应用能在该设备安装。Skill 预检识别客户端系统和 build，分别报告版本与支持期待核项。[Microsoft Windows 客户端支持清单](https://learn.microsoft.com/en-us/windows/release-health/supported-versions-windows-client)
2. 按 Python 官网选择本机系统架构对应的 Python 3.12 或更新版本。安装完成后关闭并重开 PowerShell。输入 `py -3 -V`。如果设备只有已安装的 `python.exe`，用 `python -V` 检查，之后把示例的 `py -3` 全部改为 `python`。出现商店提示、命令不存在或版本低于 3.12 时先处理 Python。[Python for Windows](https://docs.python.org/using/windows.html)
3. 使用普通用户权限在本机可写的 NTFS 文件夹建立客户项目。项目路径可有空格或中文。优先选短路径；下文用 `C:\客户项目\品牌 A` 举例。同步盘、公司网络共享和目录联接请改用客户本机的真实目录。将安装 ZIP 解压到另一个目录。
4. 从开始菜单打开 **PowerShell 7**。系统自带的 **Windows PowerShell 5.1** 也可运行本页的基本 Python 命令。运行 `$PSVersionTable.PSVersion` 可查看版本。

## 2. 预览并安装

将下面两行路径改成自己的实际绝对路径，保留单引号。安装目录内应同时看见 `install.py`、`run.py`、`SKILL.md`。

```powershell
$Project = 'C:\客户项目\品牌 A'
Set-Location -LiteralPath 'C:\已解压\codex-ads-live'
py -3 -V
py -3 -X utf8 -I -B .\run.py preflight
py -3 -X utf8 -I -B .\install.py --project "$Project" --preview
```

查看 `system = Windows`、`checks.operating_system = IN_SCOPE`、`checks.package_integrity = PASS`，并核对 `windows.family` 与 build。上述字段记录预检和包完整性。Windows 原生验收还需要在实际设备完成以下步骤并留存回执。检查预览的目标目录。看到 `INSTALL_PLAN_READY` 后继续。

```powershell
py -3 -X utf8 -I -B .\install.py --project "$Project"
py -3 -X utf8 -I -B .\install.py --project "$Project" --install-status
Set-Location -LiteralPath "$Project"
$SkillRun = Join-Path $Project '.agents\skills\codex-ads-live\run.py'
py -3 -X utf8 -I -B "$SkillRun" --version
```

安装回读应显示 `PROJECT_INSTALL_VERIFIED` 和预期版本。打开这个项目的原路径，在 ChatGPT 桌面应用的 Codex 模式中确认 Agent 环境是 **Windows native**。在一个新聊天中输入 `$codex-ads-live`，选择匹配项，然后输入「检查本项目安装，告诉我加载的 SKILL.md 路径。用公开知识比较 ABO 和 CBO，只输出本地结果。」选择字段必须指向 `.agents\skills\codex-ads-live\SKILL.md`。找不到时，先确认当前项目根目录、读回安装回执，再重开项目。还可从 PowerShell 直接运行同一入口。

```powershell
py -3 -X utf8 -I -B "$SkillRun" knowledge-search --topic strategy
```

当前步骤使用 Windows 原生 Python 和 PowerShell。若预检显示 Linux，返回 Windows native 并打开本机真实目录。Codex 的终端和 Agent 环境是两项设置。[Windows 桌面文档](https://learn.chatgpt.com/docs/windows/windows-app) · [Skill 的项目目录](https://learn.chatgpt.com/docs/build-skills)

数据模板用记事本或 VS Code 以 UTF-8 保存。PowerShell 7 保存本地非秘密 JSON 时可用 `Set-Content -Encoding utf8NoBOM`。Windows PowerShell 5.1 的 `>`/ `Out-File` 通常产生 UTF-16LE，产品输入使用 UTF-8。从 Python 回执直接交接原文件可保留完整摘要。[PowerShell 编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.6)

## 3. 本人安全输入或登记只读凭据

仍在刚安装的项目内。以下示例使用新输出目录。授权前先按[账户接通流程](CUSTOMER_SOP.md)完成 Meta 官方授权及 App、人员、账户核对。

```powershell
$W = Join-Path $Project '.codex-ads-live\client'
$IntentDir = Join-Path $Project '账户连接计划'
py -3 -X utf8 -I -B "$SkillRun" connection-prepare --output "$IntentDir"
```

打开生成的 `intent.json`，照账户 SOP 填非秘密的编号和确认项。临时方式填写 `SESSION_INPUT`，并保留 `credential_reference: null`。预览显示原 App、本人和账户均一致，且本人批准本次只读之后，在普通的 Windows Terminal 或 PowerShell 窗口完成实际读取。

```powershell
$Readback = Join-Path $Project '账户只读回执'
py -3 -X utf8 -I -B "$SkillRun" connection-preview "$IntentDir\intent.json"
py -3 -X utf8 -I -B "$SkillRun" connection-check "$IntentDir\intent.json" --output "$Readback"
py -3 -X utf8 -I -B "$SkillRun" connection-review --run-dir "$Readback"
```

先按窗口打印的范围确认句键入并回车。看到随后出现的隐藏口令提示后，再由本人输入 Token 并回车。隐藏输入时空白属于正常现象。如果出现输入不可用或要求回显，换到独立的 PowerShell 窗口重试。检查 `NAMED_ACCOUNT_READ_PASSED` 和具名账户编号。Token 留在本人控制的输入框，避免写入命令参数、脚本、转录、历史、聊天或截图。

长期复用时，先生成准备表。打开生成的 `preparation.json`，按[资产准备表说明](CUSTOMER_SOP.md#b以后沿用同一个项目工作区与-macos-钥匙串)填写业务资产组合、App、广告账户及已有 Page 的非秘密编号和佐证。把示例数字换成真实编号。

```powershell
$Account = '填广告账户纯数字 ID'
$Preparation = Join-Path $Project '客户准备'
py -3 -X utf8 -I -B "$SkillRun" prepare --output "$Preparation"
```

填好表后，首次才初始化。已有工作区用 `workspace-show` 核对原档案。要补准备表，可用 `workspace-onboard --workspace "$W" --preparation "$Preparation\preparation.json"`。

```powershell
py -3 -X utf8 -I -B "$SkillRun" workspace-init --workspace "$W" --preparation "$Preparation\preparation.json"
py -3 -X utf8 -I -B "$SkillRun" workspace-default --workspace "$W" --kind ACCOUNT --id "$Account"
py -3 -X utf8 -I -B "$SkillRun" workspace-show --workspace "$W"
```

登记时把示例数字分别换成实际 App 和本人在这个 App 下的身份。已登记时直接核对原登记。

```powershell
$App = '填 App 数字 ID'
$Person = '填本 App 下的人员数字 ID'
$Account = '填广告账户纯数字 ID'
$Record = Join-Path $W 'credential-record'
py -3 -X utf8 -I -B "$SkillRun" credential-prepare --app-id "$App" --principal-id "$Person" --platform windows --output "$Record"
py -3 -X utf8 -I -B "$SkillRun" credential-status --record-dir "$Record"
```

从开始菜单搜索 **凭据管理器**，进入 **Windows 凭据 → 添加普通凭据**。地址填返回的 `service_or_target`，用户名填 `account_label`，密码由本人在这个系统窗口输入只读 User Token。电脑由组织限制保存普通凭据时，使用上述临时交互方式。使用同一个 Windows 登录用户打开终端。只读引用的本地登记不含 Token；实际读回会核对 Meta 返回的权限、身份、令牌到期和数据访问到期。[Microsoft 凭据管理器](https://support.microsoft.com/en-us/windows/security/credential-manager-in-windows)

分别做预览、绑定和具名读回。下列确认选项仅适用于已核实两项身份和归属，且官方应用配置确认不要求 App Secret Proof 的情况。

```powershell
$Connection = Join-Path $Project '工作区连接计划'
$Observed = Join-Path $Project '工作区只读回执'
py -3 -X utf8 -I -B "$SkillRun" workspace-connection-prepare --workspace "$W" --id "$Account" --app-developer-confirmed --asset-owner-confirmed --appsecret-proof NOT_REQUIRED_CONFIRMED --output "$Connection"
py -3 -X utf8 -I -B "$SkillRun" workspace-bind --workspace "$W" --intent "$Connection\intent.json"
py -3 -X utf8 -I -B "$SkillRun" workspace-refresh --workspace "$W" --id "$Account" --output "$Observed"
py -3 -X utf8 -I -B "$SkillRun" connection-review --run-dir "$Observed"
```

过期后由本人在 Meta 核对并决定新授权。也可在系统窗口修改同标签的已有只读凭据，再在原项目重查。退出时先运行 `workspace-removal-plan --workspace "$W"` 看范围。本人可以用 `credential-disable --record-dir "$Record"` 停用当条本机引用，随后在凭据管理器按地址核对并移除那一项。需要撤销 Meta 授权时，在 Meta 官方授权页面办理。三个动作的回执各自保留。

## 4. 升级、备份、回退

从新版解压目录执行。先创建独立、空白的新版项目。保持旧项目、旧安装与原计划目录。预览返回 `PROJECT_UPGRADE_PREVIEW_READY`，核对两个路径和输出的 `plan_sha256`。

```powershell
$Old = 'C:\客户项目\品牌 A'
$Next = 'C:\客户项目\品牌 A 新候选'
New-Item -ItemType Directory -Path "$Next"
py -3 -X utf8 -I -B .\install.py --project "$Next" --upgrade-from "$Old" --preview
$PlanHash = '填上一条返回的 plan_sha256'
py -3 -X utf8 -I -B .\install.py --project "$Next" --upgrade-from "$Old" --upgrade-plan-sha256 "$PlanHash"
py -3 -X utf8 -I -B .\install.py --project "$Next" --upgrade-status
```

确认 `PROJECT_UPGRADE_STAGED_VERIFIED` 后，在 Codex 打开新版项目，核对它所读取的本地配置。旧项目使用自定义工作区时，预览与执行均附 `--previous-workspace` 和该路径。分阶段升级只转移可移植资料；系统凭据、连接授权、旧计划和报告依原位置留存。回退时重新打开保留的旧项目及旧工作区。

在原项目中导出非秘密配置。把备份目标和恢复工作区改成尚不存在的真实路径。先检查返回的 `restore_plan_sha256`。

```powershell
$Backup = 'C:\客户项目\品牌 A 独立备份'
$RestoredWorkspace = 'C:\客户项目\品牌 A 恢复档案'
py -3 -X utf8 -I -B "$SkillRun" workspace-backup --workspace "$W" --output "$Backup"
py -3 -X utf8 -I -B "$SkillRun" workspace-restore --workspace "$RestoredWorkspace" --backup-file "$Backup\config.json" --preview
$RestoreHash = '填上一条返回的 restore_plan_sha256'
py -3 -X utf8 -I -B "$SkillRun" workspace-restore --workspace "$RestoredWorkspace" --backup-file "$Backup\config.json" --restore-plan-sha256 "$RestoreHash"
```

看到 `backup_receipt_status = MATCHED`，再运行 `workspace-show` 查看恢复出的账户与配置。继续原试投时将原工作区和原计划一起交给 `workspace-first-launch-progress`，避免将旧计划挂到恢复后的不同凭据状态上。

## 5. 故障和平台边界

| 现象 | 如何处理 |
| --- | --- |
| 哈希失败、安装文件被隔离 | 先保留报错和原项目。对照客户包清单重新解压到短路径，由 IT 核对隔离项或写权限。 |
| 路径过长、安装冲突或文件在使用 | 先确认当前目录是正常本地 NTFS 路径。将新版安装到另一个较短的可写目录，结束自己在该目录的旧操作后再尝试。 |
| 本机凭据找不到或读取拒绝 | 核对是否为 Windows 凭据下的普通凭据、同一 Windows 用户及两个准确标签。组织不允许时改为本人隐蔽输入。 |
| Windows 访问 Meta 出现超时或 TLS 错误 | 在允许的网络中可执行 `network-check`，它只向固定 Meta HTTPS 地址发无凭据连通性测试。Windows 使用直连且核对 TLS。公司代理、PAC 或 TLS 代理需要 IT 处理。也可使用已授权的 Meta 报表导出。 |
| 需要照片、视频帧或字幕 | 在 Windows 上准备可运行的 ffmpeg 和 ffprobe，再用 `media-analyze`。有权使用的 SRT 可作为字幕输入。自动转写还要可运行的 whisper-cli 与经 SHA-256 校验的对应模型。 |
| 要在 Windows 实际建单或启用 | Windows 指南保留本地准备和人工回执路径，原生验收待完成。管理员受管执行服务适用于配置好的 macOS。获准时可在 Ads Manager 人工办理，再保留原编号与观察记录。 |
| 要看店铺或公共主页直播 | Shopify 先核真实店铺与所需权限，可用获授权的去标识回包或 CSV。Facebook Page Live 使用具名对象或主办方导出，连续读取另看实际权限和对象。 |

[素材与经营操作](MEDIA_BUYER.md) · [首投与人工接续](FIRST_LAUNCH_CUSTOMER.md) · [Windows 凭据读取接口](https://learn.microsoft.com/windows/win32/api/wincred/nf-wincred-credreadw) · [Whisper 模型](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md)
