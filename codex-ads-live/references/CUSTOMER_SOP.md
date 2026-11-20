# 账户接通流程

本流程的完成标志是**从已安装的 Skill 读取指定 Meta 广告账户，并核对账户 ID、币种、时区和状态**。首次只想确认能否连接，走 A；希望持续复用同一客户工作区，直接走 B。两者都只申请本次账户读取所需的授权。

## 1. 在 Meta 确认主体和资产

| 需要什么 | 在哪里核对；这次用什么值 |
|---|---|
| 广告账户 ID | 登录 Ads Manager，在账户选择器点开本次广告账户，核对账户名称及 ID。工具填纯数字，去掉接口前缀 `act_`。同一人可见多个账户时，以客户指定的那个为准。 |
| App ID | 打开 Meta for Developers 的「我的应用」，选择客户已有且归属明确、能使用 Marketing API 的 App，在应用面板读取 App ID。若还没有，应用负责人在「创建应用」按当前向导选与广告管理相符的用途，添加 Marketing API 能力并确认归属。只为核对一次已有账户时，无需再新建广告账户。 |
| App 角色与账户访问 | 本版首次自检使用该 App 开发者身份的 **User Access Token**。请同时核对本人在这个 App 的开发者身份和指定广告账户的读取权。若只有广告账户的操作权限，请由客户已有且获准的 App 开发者办理，或由应用负责人在应用面板的角色页核对并按公司流程分配角色。App ID、广告账户 ID、Page ID、Business ID 和人员 ID 是不同编号。 |
| App Secret Proof | 应用管理员在 App Settings → Advanced 的 Security 区域查看 Require App Secret。已开启或不确定时，保留安全设置；本版对应的自检停在本地预览，请实施方使用支持该校验的已批准路径。 |
| 权限与使用场景 | 首次账户读取申请 `ads_read`。客户使用自己的 App 操作自己的账户，按该 App 的权限级别办理。服务商用其 App 管理他人账户时，Meta 要求相关权限具备 Advanced Access；管理员在 App Review → Permissions and Features 核对。生产广告主还需按应用面板核对 Marketing API Access Tier。不要用员工临时开发者身份代替面向外部客户所需的审核。 |

[广告账户与人员任务](https://developers.facebook.com/docs/business-management-apis/business-asset-management/guides/ad-accounts) · [应用面板](https://developers.facebook.com/apps/) · [Marketing API 授权与访问级别](https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization) · [App Secret Proof](https://developers.facebook.com/docs/graph-api/guides/secure-requests/)

## 2. 取得这个 App 下本人的只读授权

1. 在 Meta 官方 Graph API Explorer 登录。右上选择**第 1 步核过的 App**。选择用户访问口令（User Access Token），在权限列表选择 `ads_read`，生成口令，在 Meta 弹窗核对登录者、App 和批准范围。下拉找不到正确 App 或权限不可申请时，先请管理员核对角色、使用场景及审核状态。
2. 在这个 App 和这枚 User Token 下，选择 GET。与本安装件核对时，API 版本选择 v27.0，查询 `/me?fields=id`。返回的 `id` 就是本流程的 `principal_id`。Graph Explorer 默认选择的其他 App、个人主页网址中的编号以及 Business ID 都不能替代它。Explorer 这次读用户 ID 是为了确定身份，后面还需由 Skill 读取实际广告账户。
3. 操作人保管令牌，只在自己控制的终端隐藏输入或本机钥匙串密码框中使用。Token、密码和 App Secret 不发给 Codex，不写入 JSON 或命令行，不在截图中显示。本版会核对 Meta 返回的令牌到期和数据访问到期时间。请使用有明确有效期的个人用户令牌；系统用户令牌和无期限令牌都不适用于此入口。Skill 的自检不会代办 OAuth、续期或扩大授权；如此前授权含更多权限，回执按 Meta 实际返回的范围记录。另行使用服务商登录授权流程时，由其完成对应的用户同意与审核。

[Graph API Explorer](https://developers.facebook.com/tools/explorer/) · [Meta Explorer 使用指南](https://developers.facebook.com/docs/graph-api/guides/explorer) · [Token 元数据](https://developers.facebook.com/docs/graph-api/reference/debug_token/)

## 3. 在已安装项目内选择一种接入方式

在自己的 Mac 终端执行，第一行替换成实际项目绝对路径。所有命令示例都以该项目为当前目录。下面使用的新目录应尚不存在；已有客户目录保留，补跑时为新回执另取名。没有终端经验时，打开已安装的项目后输入「使用 $codex-ads-live，帮我准备本机只读连接模板，指出要由我本人确认的字段。停在登录或读取钥匙串之前」，再由本人操作需要授权的部分。

```sh
cd "/你的项目绝对路径"
RUN="$PWD/.agents/skills/codex-ads-live/run.py"
W="$PWD/.codex-ads-live/client"
APP="填实际App数字ID"
PERSON="填该App下的本人数字ID"
ACCOUNT="填广告账户纯数字ID"
```

在允许向 Meta 做无凭据连通性检查时，可先执行 `python3 "$RUN" network-check`。出现 `META_HTTPS_REACHABLE` 说明此时可连接 Meta；账户验证还在后面。Mac 使用既有 HTTPS 系统代理时，可用 `python3 "$RUN" --macos-system-proxy network-check`；需要代理的后续网络命令同样把该选项放在子命令之前。

### A．本人临时输入，完成一次只读连接

```sh
python3 "$RUN" connection-prepare --output "$PWD/账户连接计划"
```

用文本编辑器打开生成的 `账户连接计划/intent.json`，保留字段名和格式，仅按本次实际情况填写。完整示意如下。三个数字字符串必须替换；只有本人已核对相应事实时才将两项确认设为 true。

```json
{
  "schema": "connection-intent-v1",
  "app_id": "替换为App数字ID",
  "principal_id": "替换为本App下的人员数字ID",
  "account_id": "替换为广告账户纯数字ID",
  "credential_source": "SESSION_INPUT",
  "credential_reference": null,
  "app_developer_confirmed": true,
  "asset_owner_confirmed": true,
  "appsecret_proof_requirement": "NOT_REQUIRED_CONFIRMED"
}
```

最后一项仅适用于管理员已确认该 App 不要求 App Secret Proof。保持未知填 `UNKNOWN`，已要求填 `REQUIRED`。先预览实际 App、人员、账户和将发送的两项只读请求。

```sh
python3 "$RUN" connection-preview "$PWD/账户连接计划/intent.json"
```

看到 `READY_FOR_LOCAL_SCOPE_CONFIRMATION`、三个编号一致且已批准本次读取后，**本人在交互终端**运行下面的第一条命令。按终端显示键入完整确认句 `READ 账户数字ID WITH APP App数字ID`，在隐藏输入提示中粘贴 User Token。结束后用第二条命令从同一目录核对回执。

```sh
python3 "$RUN" connection-check "$PWD/账户连接计划/intent.json" --output "$PWD/账户只读回执"
python3 "$RUN" connection-review --run-dir "$PWD/账户只读回执"
```

这条路线可以到此结束。临时输入仅用于这次会话。以后想复用工作区时按 B 保存资产和系统引用。

### B．以后沿用同一个项目：工作区与 macOS 钥匙串

1. **生成资产准备表。** 运行下列命令，用文本编辑器打开生成的 `客户准备/preparation.json`。在 `assets.app.id`、`assets.business_portfolio.id`、`assets.ad_account.id` 分别填实际数字字符串。各自的 `ownership_evidence_ref` 填指向本人真实归属或授权记录的内部编号，例如格式 `EV_ACCOUNT_OWNER`，对应凭据留在客户项目。未知的资产保留空值。还要用于广告草案时，再填写 `assets.facebook_page` 的 ID、同类证据编号及 `page_identity_type = FACEBOOK_PAGE`。按已知事实填写 `account_context.currency`、`account_context.timezone`。本次只读广告将 `requested_work.ads_read` 设为 true。保留模板的模式、授权和真实执行开关原值。

```sh
python3 "$RUN" prepare --output "$PWD/客户准备"
```

2. **建立或复用工作区。** 首次运行下面的初始化；若该工作区已经存在，需要补这份表时，只执行 `workspace-onboard --workspace "$W" --preparation "$PWD/客户准备/preparation.json"`（前面同样是 `python3 "$RUN"`）。已绑定的客户直接进入当前任务，不重复初始化。不存在已核实的 Business Portfolio 时，可先用 A 验证一个账户，等负责人确认工作区绑定所需的真实归属再走 B。

```sh
python3 "$RUN" workspace-init --workspace "$W" --preparation "$PWD/客户准备/preparation.json"
python3 "$RUN" workspace-default --workspace "$W" --kind ACCOUNT --id "$ACCOUNT"
python3 "$RUN" workspace-show --workspace "$W"
```

3. **在本机建只读凭据引用。** 已有与该 App、人员及用途一致的有效登记时，用第二条查看已有登记；首次才执行第一条。命令生成随机的 `conn_…`、`service_or_target` 和 `account_label`。此时只建了引用，口令另由本人录入。

```sh
python3 "$RUN" credential-prepare --app-id "$APP" --principal-id "$PERSON" --platform macos --output "$W/credential-record"
python3 "$RUN" credential-status --record-dir "$W/credential-record"
```

4. **系统录入。** 本人用 Spotlight 打开“钥匙串访问”（Keychain Access），选自己的登录钥匙串，使用新建密码项目功能，建立通用密码项目（Generic Password）。项目名称／服务精确复制 `service_or_target`，账户精确复制 `account_label`，密码栏由本人粘贴这枚 User Token。保留系统访问控制；确认读取弹窗中的应用与本次操作一致时，可选择仅允许本次。不同 macOS 版本的菜单名称可能不同，先核条目种类与两个标签。[Apple 钥匙串访问说明](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26)

5. **预览、绑定、读取。** 下列确认参数仅在两项角色／归属已经核实、该 App 已确认无需 Proof 时使用。第一条生成 `intent.json`、`PREVIEW.json` 等文件。先看预览是否为 `READY_FOR_LOCAL_SCOPE_CONFIRMATION` 且三项编号均对，再执行后续命令。实际读取时仍需本人本次确认；系统引用绑定可以长期保留，实际 Token 有自己的期限。

```sh
python3 "$RUN" workspace-connection-prepare --workspace "$W" --id "$ACCOUNT" --app-developer-confirmed --asset-owner-confirmed --appsecret-proof NOT_REQUIRED_CONFIRMED --output "$PWD/工作区连接计划"
python3 "$RUN" workspace-bind --workspace "$W" --intent "$PWD/工作区连接计划/intent.json"
python3 "$RUN" workspace-refresh --workspace "$W" --id "$ACCOUNT" --output "$PWD/工作区只读回执"
python3 "$RUN" connection-review --run-dir "$PWD/工作区只读回执"
```

6. **下次直接恢复。** 回到相同项目，重新设置本页开头的 `RUN` 和 `W` 后执行 `python3 "$RUN" workspace-route --workspace "$W" --task ads-analysis`。它给出已选资产及下一步，凭据过期、角色改变或换了目标时按本次情况修正。只读账户授权、Page 读取和真实写入各自办理；独立写批准以及用于建单和启用的凭据由管理员单独配置。

## 4. 看回执，决定到哪里为止

打开所选结果目录的 `RESULT.json` 和 `RECEIPT.json`。确认 `status = NAMED_ACCOUNT_READ_PASSED`、`real_account_read_verified = true`、返回的账户 ID 与目标一致；核对返回的币种、时区、账户状态及观察时间。诊断顺序是先读 Token 的 App、人员、权限和有效期，通过后再读指定账户的基础字段。合法的非活跃账户也可能可读，若状态受限，后续投递资格由账户管理员处理。[Meta 广告账户字段](https://developers.facebook.com/docs/marketing-api/reference/ad-account/)

| 看到什么 | 可以怎么继续 |
|---|---|
| 预览显示阻断、Proof 必需或未知 | 按阻断项核对角色、资产、App 安全策略；没有匹配实现就停在预览。 |
| 人员或 App 不符、广告读取权限缺失、Token 已过期／期限无法核实 | 回 Meta 核对同一 App、操作者和原授权范围。权限或令牌需变更时由本人决定。使用新输出目录保存下一次获准读取。 |
| 本机引用找不到或访问被拒 | 检查当前 macOS 用户、登录钥匙串、通用密码种类、服务名和账户标签；再确认本机登记仍有效。 |
| 账户 ID 不符、无资产访问、API 限流、网络中断 | 暂停当前步骤，保留该次回执及错误码；由负责人核对 ID 或任务权限，依平台提示等待或修复网络。再次只读前确认原范围。 |
| 只有 Explorer 成功、模板生成或网络可达 | 在已安装项目内完成 A 或 B 的指定账户读取，保留实际返回。 |
| 账户已可读，接下来要投放 | 先回到[使用说明](INSTALL_RUN.md)准备当前电商广告，再单独授权真实动作。正展示、实际花费及成交收入分别以各自的原始记录确认。 |

账号或经营数据请保存在本机；公开教学素材仅使用另行获准的脱敏内容。
