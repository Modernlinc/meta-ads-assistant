# macOS 管理员操作说明｜首投受控执行端 V2

**适用场景**为一台受管理 Mac、一个已建立的客户工作区和本次首投计划。客户项目继续使用正常 Skill。每台 Mac 由管理员分别核对服务部署、钥匙串访问和系统权限。真实权限、系统部署、平台创建与花费须分别取得对应批准。

## 1. 确定两个执行身份

| 身份 | 管理范围 |
| --- | --- |
| 客户项目用户 | 运行已安装的 `run.py`，使用自己的受托只读引用，准备计划、查看脱敏回执和独立回读。该用户不得读取专用写钥匙串或拥有不受约束的管理员权限。 |
| 独立管理员 | 控制受保护的解释器、服务源码、专用钥匙串及批准目录。对照客户的真实授权、目标账户、对象、预算、时窗与材料，在独立终端逐次批准。 |

客户已有可用的只读账户与报告入口可以继续使用。未部署新服务时仍可编写本地草案、查看原记录、按已有批准接续人工操作。涉及新服务的自动建单、原生预览、宿主回读和自动启用要等服务及相应授权就绪。部署面向 macOS 和管理员信任的 Python 3.12 或更高版本。

## 2. 管理员部署一次

1. 校验本交付包的 SHA-256 清单。从同一份已校验的安装 ZIP 解出 `codex-ads-live`，保留 `authority.py` 和完整的 `src`。由管理员在受保护的暂存目录完成校验和复制。客户端普通安装不能作为管理员运行的服务副本。
2. 以下为新目录的部署示例。在 **管理员终端**设置 `VERIFIED_SOURCE` 为上一步核过的解压目录；将 `ADMIN_PYTHON` 设为管理员控制的绝对解释器路径。示例不覆盖已有服务或旧版目录。新版本安装和替换已有管理员副本应按公司的发布流程执行。

```sh
set -eu
: "${VERIFIED_SOURCE:?请设置管理员已核验的源码目录}"
: "${ADMIN_PYTHON:?请设置管理员控制的 Python 绝对路径}"
ADMIN_COPY="/Library/ModernLinc/AdsFirstLaunchAuthorityV2"
STATE="/Library/Application Support/ModernLinc/CodexAdsLive/first-launch-v2"
test ! -e "$ADMIN_COPY"
sudo /usr/bin/install -d -o root -g wheel -m 0755 "$ADMIN_COPY"
sudo /usr/bin/install -o root -g wheel -m 0644 "$VERIFIED_SOURCE/authority.py" "$ADMIN_COPY/authority.py"
sudo /usr/bin/ditto "$VERIFIED_SOURCE/src" "$ADMIN_COPY/src"
sudo /usr/sbin/chown -R root:wheel "$ADMIN_COPY/src"
sudo /bin/chmod -R go-w "$ADMIN_COPY/src"
sudo /usr/bin/install -d -o root -g wheel -m 0755 "$STATE"
sudo /usr/bin/install -d -o root -g wheel -m 0700 "$STATE/private"
```

3. App 管理员核对同一 App、具名开发者身份、广告账户、Page、币种与角色。自检使用该 App 开发者的 USER 令牌，要求令牌和数据访问均有明确的未来到期时间。客户只读引用与专用写引用使用不同的 `conn_` 加 32 位小写十六进制编号。客户会话的通用密码项目只登记具有 `ads_read`、不含 `ads_management` 的只读令牌。管理员使用受管钥匙串工具，**只在** `/Library/Application Support/ModernLinc/CodexAdsLive/first-launch-v2/private/admin-write.keychain-db` 的通用密码项目中登记允许 `ads_management` 的写令牌。项目服务名为 `ModernLinc.CodexAdsLive.<对应的conn_编号>`，账户名为 `<APP_ID>.<PRINCIPAL_ID>`。钥匙串必须允许管理员执行服务在批准时非交互读取，private 目录须 root 专属（0700），钥匙串文件须 root 专属（0600），两者均不带扩展 ACL，钥匙串文件仅有一个硬链接。凭据和钥匙串口令经受管渠道设置，不进入终端参数、聊天、源码或工作区文件。该产品使用既有凭据，只支持无需 App Secret Proof 的已授权连接。
4. 迁移旧版 时，管理员先停止旧审批服务，复核旧的未决尝试、批准和真实平台状态。对普通用户过去可读的写令牌完成撤销或轮换，确认相同写能力不再保留于客户登录钥匙串、脚本及旧宿主，再启用 V2。原项目账本和证据应保留。新服务使用独立目录，不消费旧批准。旧尝试未知时先核对原对象，不能补发。

## 3. 绑定原计划并查看服务状态

客户用户从其**同一版正常安装目录**导出当前范围。`W` 为原工作区，以下占位符均替换为实际值。若计划位于自选目录，各客户端命令加相同的 `--plan-dir PLAN`。

```sh
python3 run.py workspace-first-launch-host --workspace W > SETUP_SCOPE.json
```

管理员从已获准的非秘密配置包核对 UID、App、账户、Page 和原计划。分别在管理员终端配置并运行固定服务。下列 `READ_REF` 和 `WRITE_REF` 是引用名。首次配置只写新 profile；已经绑定到另一计划时停止核对，不覆盖或复用其他客户的绑定。

```sh
sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" configure --from-packet SETUP_SCOPE.json --client-uid CLIENT_UID --principal-id PRINCIPAL_ID --read-reference READ_REF --write-reference WRITE_REF
sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" serve
```

服务前台运行。由客户用户另开终端运行以下命令。预期 `LOCAL_AUTHORITY_SCOPE_MATCHED` 仅说明当前计划找到匹配的审批端，凭据有效性和平台结果要在各自阶段核对。服务暂停只会阻止后续请求；已经在 Meta 启用的广告仍由独立的平台限额、结束时刻及授权停投控制。

```sh
python3 run.py workspace-first-launch-host --workspace W --status
```

## 4. 暂停建单、恢复和独立启用

- 管理员核对具体只读范围后，可在独立管理员终端执行 `sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" approve read --packet SETUP_SCOPE.json --evidence-ref EV-READ`。客户进程仅在该许可内读取。
- 每项暂停建单，从普通入口将 `workspace-first-launch-progress --workspace W --reserve` 的输出保存为 `RESERVED.json`。由管理员核对同一对象、准确表单、材料权利、资金范围及尚未发送的原尝试，在独立终端执行 `sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" approve create --packet RESERVED.json --evidence-ref EV-CREATE`。客户从正常入口请求 `workspace-first-launch-progress --workspace W --dispatch-approved STAGE_SHA256`。服务根据管理员已存的原表单、账户和请求摘要发一次限定请求。客户端收到脱敏对象编号或未知状态。
- 建单回读使用 `workspace-first-launch-progress --workspace W --recover-host STAGE_SHA256`。客户端收据丢失时，完整保存在新审批端的原回执可协助恢复原 ID；若实际请求已被消费但平台回执无法归属，保留未知结果，只查原对象和活动记录。此时不得重发。
- 在相应读许可内，用 `workspace-first-launch-preview --workspace W --fetch --output NEW_PREVIEW` 查看原 Ad。父级变更、完整影响目录、可执行 Campaign 总限额和 Ad Set 起止时间沿用 `FIRST_LAUNCH_CUSTOMER.md` 的独立授权与状态回读。在原 Ad 预览核对、真实支出控制及单 Ad 另批都具备后，按以下顺序操作。各条客户命令均在已安装目录运行；管理员命令在独立终端运行。

```sh
python3 run.py workspace-first-launch-activation --workspace W --review REVIEW --host-approval > ACTIVATION_SCOPE.json
sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" approve activate --packet ACTIVATION_SCOPE.json --evidence-ref EV-SPEND
python3 run.py workspace-first-launch-activation --workspace W --review REVIEW --execute-approved
python3 run.py workspace-first-launch-activation --workspace W --recover
```

启用执行端会自行核对原批准、具名账户及实际控制字段，只能使用已批准的单 Ad 请求。回读应固定同一个 Ad 并区分配置与平台有效状态。首次正投递另需同一广告在实际批准和控制窗内的正展示。拒绝、超时、服务中断或钥匙串不可用时，保留原尝试和回执并回查；恢复服务须使用同一受保护目录、审批记录和客户范围，禁止通过重建消费记录来恢复。


首次在当前设备使用前，管理员应核对部署、两个身份的有效权限、具名客户授权及同一动作的回执。服务版本和账户读取回执可在各自的设置步骤查看。

## 5. 受管素材、复盘证据和原对象调整

使用同一版本的客户安装和管理员服务。管理员从客户侧取得明确的计划包后，先核对账户、原计划、文件或对象、权限和批准期限。在下列命令中，用实际请求文件及凭据引用对应的证据编号替换示例名称。

- 上传一张图片或一个视频时，先用 `workspace-first-launch-media` 生成请求，核对实际本机原件与文件摘要。管理员执行 `sudo "$ADMIN_PYTHON" -I -B "$ADMIN_COPY/authority.py" approve media --packet media-packet.json --media-file "/客户原文件的绝对路径" --evidence-ref EV-MEDIA`。原文件由客户项目用户拥有。已批准字节保存在管理员专属目录中，按管理员的素材保留策略管理。该权限只发送该文件。
- 原媒体读回由管理员执行 `approve read --packet SETUP_SCOPE.json --read-purpose media --read-operation media-packet.json --evidence-ref EV-MEDIA-READ` 后，客户使用 `workspace-first-launch-media --mode recover --packet media-packet.json`。看到原视频仍在处理中时可稍后发起同一只读检查。
- 需要复盘的同一 Ad 创意和账户活动时，将 `workspace-first-launch-evidence` 的预览保存为 `evidence-preview.json`。管理员核对日期和对象后，使用 `approve read --packet SETUP_SCOPE.json --read-purpose delivery-evidence --read-operation evidence-preview.json --evidence-ref EV-REPORT-CONTEXT`。客户沿原日期生成回执。活动读取限当前账户和最多三页，输出仅保留指定广告及父级的记录。
- 暂停或调原广告组日预算时，先通过 `approve read --packet SETUP_SCOPE.json --read-purpose maintenance --evidence-ref EV-MAINTENANCE` 开通具名检查。客户用 `workspace-first-launch-update --mode observe` 生成原值、目标值和影响对象包。管理员核对后用 `approve update --packet update-packet.json --evidence-ref EV-UPDATE`。客户以原包执行一次 `--mode dispatch`，然后以 `--mode recover` 读回。恢复要求仍有 maintenance 读批准。受管服务用已登记的管理员角色对原对象单独发起 GET。确认收到请求时为 `ACKNOWLEDGED`，只有原节点回读一致才为 `READBACK_MATCHED`。
- 日预算提交前，系列、广告组与完整受影响 Ad 清单均须处暂停。父级范围与预期不同、源文件已变、预览包到期或原尝试未决时，先核对原范围。恢复读到的配置状态和平台实际投递指标需分别解释。
