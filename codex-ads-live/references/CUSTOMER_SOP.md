# 首次接入 Meta 与已有工作区接续

完成安装后可直接说「用现有报表解释 CPM 为什么变高」「核对订单退款与广告费」。助手自行处理工作方法；只有使用真实平台数据时才需要相应身份和只读授权。[客户使用说明](../methods/ADAPTATION.md)

在已安装项目的 Codex 聊天中说「使用 $codex-ads-live，检查这个项目的 Meta 接入状态。从当前步骤继续，登录、授权和实际读取由我确认。」先使用原项目和工作区。终端示例以 macOS 为主。Windows 有 [PowerShell 与凭据管理器步骤](WINDOWS_CLIENT.md)，实机历史状态为 `NOT_RUN_NO_WINDOWS_HOST`，本轮验收已获用户豁免（`PASS_BY_USER_WAIVER`）。

```sh
cd "/你的项目绝对路径"
RUN="$PWD/.agents/skills/codex-ads-live/run.py"
W="$PWD/.codex-ads-live/client"
python3 "$RUN" workspace-connect --workspace "$W"
```

已有工作区路径和例子不同时，沿用原路径。只有返回 `INITIALIZE_WORKSPACE` 时用 `workspace-connect --workspace "$W" --start`，再读取 `CHOOSE_ROUTE`。命令中的 APP、BUSINESS、PERSON、ACCOUNT 等示例变量需要换成当事人核过的实际编号。广告账户填纯数字。输出目录用新名称。

## 1. 按客户当前状态选路线

先看已有工作区是否能复用；首次按表从上往下选符合本次工作的第一条。一个任务选一条路线。

| 你的状态 | 选择与本人操作 | 回到 Skill 后的第一步 |
| --- | --- | --- |
| 工作区已有接入，最近换项目或重开了聊天 | 沿原路线和具名资产继续。项目负责人确认当前任务，也核对到期或权限变化。 | `workspace-connect --workspace "$W"`。按回执的原路线和步骤继续。 |
| 已有工作区，Token 过期、角色撤销或读取失败 | 沿原路线恢复。由本人通过原 App 或原宿主重新授权；企业管理员核对系统用户和资产。 | 先读取旧状态，再按第 5 节处理。 |
| 官方宿主已授权，而且本次只需在该宿主查账户或生成本地草案 | 选 `host-connector`。本人检查原宿主授权并允许它列出账户。 | `workspace-connect --workspace "$W" --route host-connector`。 |
| 企业明确要求以系统用户定期读取具名广告数据 | 选 `system-user`。业务管理员分配资产和 Employee 系统用户，App 开发者办理独立检查身份。 | `workspace-connect --workspace "$W" --route system-user`。 |
| 有自有 App 和本 App 下的 User Token，尚未选账户 | 选 `user-graph`。开发者确认 Token 的身份、有限有效期和 `ads_read` 及具名账户权限。 | 登记 App 和业务资产组合，再检查原凭据引用。 |
| 有自有 App，暂无可用 Token | 选 `user-graph`。由该 App 的开发者用官方入口取得本人 User Token。 | 登记已有 App。按 `ENROLL_USER_REFERENCE` 继续。 |
| 没有 App 或 Token，当前宿主没有可用的官方授权，且确实要接通原生读取 | 选 `user-graph`。由客户管理员建立或取得企业自有 App，添加与广告管理相符的 Marketing API 用例。 | `workspace-connect --workspace "$W" --route user-graph`。按 `PREPARE_APP` 继续。 |

仅准备品牌、素材或策略时可先做本地任务。已在可用宿主读取的客户可以继续原宿主连接器。

## 2. 自有 App 与个人用户（user-graph）

1. **确定业务身份。** 去 Meta for Developers 的「我的应用」选该企业拥有的 App。App 负责人可在需要时用「创建应用」及广告管理用例开启 Marketing API。去业务设置确认该 App 和具名广告账户的资产归属，并在应用的角色设定与广告账户分配页面核对操作者分别有相应权限。记录 App ID 与业务资产组合 ID。App 角色、资产角色和账户编号各有用途。[Meta 应用面板](https://developers.facebook.com/apps/) · [Marketing API 授权](https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization)

   ```sh
   python3 "$RUN" workspace-connect --workspace "$W" --route user-graph --app-id "$APP" --business-id "$BUSINESS"
   ```

   预期 `ENROLL_USER_REFERENCE`。已登记客户按原工作区返回的最新步骤走。

2. **本人取得 User Token。** 在 Graph API Explorer 右上确认选择的是第 1 步的 App，选择用户访问口令及 `ads_read`，在 Meta 自己的窗口同意。用官方调试工具核 App、用户、权限和有限有效期。需要 `principal_id` 时，在相同 App 和身份下查 GET `/me?fields=id`，使用返回的纯数字。App 缺失时由负责人检查 App 角色；权限不可用时去 App Review → Permissions and Features 查看本任务的访问要求，不在别的 App 上生成 Token。[Graph API Explorer](https://developers.facebook.com/tools/explorer/) · [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/)

3. **登记非秘密引用，由本人录入系统凭据。** 已有匹配登记时执行 `credential-status --record-dir "$W/credential-record"`。首次执行下列命令。Spotlight 打开“钥匙串访问”，选自己的登录钥匙串，新增通用密码项目。对照返回的 `service_or_target` 和 `account_label` 填服务/名称与账户。本人在系统密码输入框内输入口令。Windows 按 [凭据管理器说明](WINDOWS_CLIENT.md)。回到 `workspace-connect` 预期 `DISCOVER_ACCOUNTS`，只表示登记可用。[Apple 钥匙串访问](https://support.apple.com/guide/keychain-access/welcome/mac)

   ```sh
   python3 "$RUN" credential-prepare --app-id "$APP" --principal-id "$PERSON" --platform macos --output "$W/credential-record"
   ```

4. **发现并选择具名账户。** 让 App 管理员先核对 App 安全设置。只有本人确认该 App 无需 App Secret Proof，且批准本次读取时，才在本人终端执行发现命令并照提示确认。对每个已观察账户检查名称、ID、币种与分页覆盖。知道目标账户时只选同一账户，填写对应归属记录的引用（例为 `EV_ACCOUNT_OWNER`）。`CHECK_NAMED_ACCOUNT` 是选定后的下一步。若缺页可在获准范围内重试，只有已看到的 ID 可选。Proof 必须启用或尚不确定时按原安全设置保留预览，由负责人选择已支持的另一条授权路线。[Meta 安全请求](https://developers.facebook.com/docs/graph-api/guides/secure-requests/)

   ```sh
   python3 "$RUN" workspace-connect --workspace "$W" --discover --proof-not-required
   python3 "$RUN" workspace-connect --workspace "$W" --select-account "$ACCOUNT" --ownership-evidence EV_ACCOUNT_OWNER
   ```

5. **预览并完成具名读取。** 只有在开发者身份、归属和 Proof 条件都已核实后使用下面的确认参数。预览时核 App、人员、账户，且返回 `READY_FOR_LOCAL_SCOPE_CONFIRMATION`。绑定后由本人在终端完成这一次具名范围确认。核对 `RESULT.json` 和 `RECEIPT.json` 的 `NAMED_ACCOUNT_READ_PASSED`、`real_account_read_verified`、账户 ID、币种、时区和回执时间。完成后直接在同一工作区准备业务资料。不同权限入口不要混用，Page 与写广告分别办理。

   ```sh
   python3 "$RUN" workspace-connection-prepare --workspace "$W" --id "$ACCOUNT" --app-developer-confirmed --asset-owner-confirmed --appsecret-proof NOT_REQUIRED_CONFIRMED --output "$PWD/工作区连接计划"
   python3 "$RUN" workspace-bind --workspace "$W" --intent "$PWD/工作区连接计划/intent.json"
   python3 "$RUN" workspace-refresh --workspace "$W" --id "$ACCOUNT" --output "$PWD/工作区只读回执"
   python3 "$RUN" connection-review --run-dir "$PWD/工作区只读回执"
   ```

## 3. 已配置的 Meta 官方宿主连接器（host-connector）

1. 工作区选择 `host-connector`；看到 `CONNECT_IN_OFFICIAL_HOST` 后在支持该连接器的原宿主办理。本人与 Meta 核对已连接账户、所授权限和有效期。在该宿主发起「列出我有权读取的广告账户」，明确记录实际覆盖、时间与来源。[官方连接器配置](https://www.facebook.com/business/help/1456422242197840) · [Meta Ads MCP 指南](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-mcp-server/ads-mcp-server-get-started)
2. 让助手从宿主实际返回结果写入 `meta-host-account-list-v1`。顶层字段为 `schema`、`source`、`observed_at`、`accounts`、`coverage`（`COMPLETE` 或 `PARTIAL`）和 `pages_read`。`source` 包含 `provider: META_OFFICIAL_ADS_MCP`、实际 `host` 和具名 `ref`（如 `EV_HOST_LIST`）。账户每条包括 `account_id`、`currency`、`account_status` 和可选名称。保留未知为空。回执不放凭据。通过 `--host-receipt` 导入后，如果有结果会返回 `SELECT_ACCOUNT`。
3. 用 `--select-account` 和归属引用选择已看到的同一账户。`HOST_ACCOUNT_READY` 表示本地保存了宿主回执，后续真实查询在原宿主做。请在原宿主核同一账户 ID、币种、时区和实际报表，保留原宿主的回执。若宿主权限失效，重新在该宿主取得回执再导入；没有可用连接器时，返回路线表判断是否符合自有 App 路线。

   ```sh
   python3 "$RUN" workspace-connect --workspace "$W" --route host-connector
   python3 "$RUN" workspace-connect --workspace "$W" --host-receipt "$PWD/host-accounts.json"
   python3 "$RUN" workspace-connect --workspace "$W" --select-account "$ACCOUNT" --ownership-evidence EV_ACCOUNT_OWNER
   ```

## 4. 企业独立报表（system-user）

1. 业务管理员在 Business Settings → Users → System Users 选择或新增 Employee 系统用户，并分配具名广告账户和 App 所需访问。为系统用户生成有限期、仅 `ads_read` 的 Token。请同一 App 的开发者另备检查用 User Token，在 Meta 核对该身份有权确认原应用及组合的员工系统用户。两个身份的编号与口令分别处理。[Meta 系统用户授权](https://developers.facebook.com/docs/business-management-apis/system-users/guides/permissions) · [安装应用与生成令牌](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/)
2. 登记 App、业务组合与两个不同人员，看到 `SYSTEM_USER_CONFIGURATION` 时完成下列两项非秘密登记，由本人按第 2 节的钥匙串方法分别录入两个凭据。返回 `DISCOVER_SYSTEM_ACCOUNTS` 后，经批准执行 `--discover --proof-not-required`，只使用对应 App 已核实的 Proof 条件。选择已观察到的同一账户。若返回身份、员工或许可错误，到原业务组合核对分配。
3. `SYSTEM_USER_READ_READY` 是可继续检查的状态。批准实际读取后运行 `native-inventory get account`，核 `NATIVE_INVENTORY_OBSERVED`、`COMPLETE` 和 `NATIVE-INVENTORY.json` 中的账号、币种、时区。报表从 `native-insights` 设明确日期。若续期、换身份或失去权限，重新核对两条登记和业务资产，回到发现、选择与具名验证。

   ```sh
   python3 "$RUN" workspace-connect --workspace "$W" --route system-user --app-id "$APP" --business-id "$BUSINESS"
   python3 "$RUN" credential-prepare --app-id "$APP" --principal-id "$SYSTEM_ID" --platform macos --output "$W/system-credential-record"
   python3 "$RUN" credential-prepare --app-id "$APP" --principal-id "$INSPECTOR_ID" --platform macos --output "$W/app-inspector-record"
   python3 "$RUN" workspace-connect --workspace "$W" --discover --proof-not-required
   python3 "$RUN" workspace-connect --workspace "$W" --select-account "$ACCOUNT" --ownership-evidence EV_ACCOUNT_OWNER
   python3 "$RUN" native-inventory get account --workspace "$W" --account-id "$ACCOUNT" --output "$PWD/系统用户账户回执" --execute-read
   ```

## 5. 续接、权限和错误

| 看到什么 | 接续操作 |
| --- | --- |
| `REUSE_WORKSPACE` | 保留原项目和计划，用 `workspace-route --workspace "$W" --task ads-analysis` 核目标。表明本地可接续；当前权限以新回执为准。 |
| `RECONNECT`、`AUTH_REQUIRED`、`PERMISSION_REQUIRED` | 根据原路线核 App 与角色。用同一人、同一 App 重新授权时，可由本人在系统界面更新同一项凭据，回到 `workspace-refresh` 或原生读取；有新的登记目录时，从凭据登记、预览和绑定开始使用相应的 `--credential-record-dir`。涉及更换 App/业务的历史工作区按管理员确认的新范围办理。 |
| `NO_OBSERVED_ACCOUNTS` 或 `PARTIAL` | 先核广告账户分配，保留已观察项。确需补读时按当前角色重试原发现命令。 |
| `REVIEW_APP_PROOF`、终端确认取消或输入中断 | 保留已完成的本地登记。核真实安全设置，回到同一个步骤，重新确认原范围。未同意时没有具名读取回执。 |
| Token、人物 ID 或资产不一致；网络中断或限流 | 回到具体出错步骤，按平台回执处理。保持原账号，只在本人同意后用新的输出目录重跑受影响的检查。 |

**Meta 会分别管理这些事项**

- **App 与 Marketing API 产品**负责确定客户使用哪个应用和提供哪些广告能力。已有符合本次用途的 App 可复用。
- **App Review**核所请求权限或功能；**Business Verification**核业务主体。按控制台对应要求提交，广告素材本身还有独立审核。
- **Standard / Advanced Access** 是具体权限和功能的访问级别；**Marketing API Access Tier** 另分 Limited / Full，影响调用限制等。按应用和账户用途查各项，不把一个层级当作另一个层级的证明。
- **`ads_read`**用于所批准的只读广告任务。**`ads_management`**用于另行批准的管理用途。**Page** 按具体对象和读取类型核相应角色、权限和回执。一个广告账户口令、Page 口令、系统用户口令及宿主登录各有自己的范围。

[权限与功能访问级别](https://developers.facebook.com/documentation/development/graph-api/overview/access-levels) · [App Review](https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/app-review) · [公司验证](https://developers.facebook.com/documentation/development/release/business-verification) · [Marketing API Access Tier](https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization)

Token 留在本人控制的官方窗口、系统凭据界面或安装件提示的隐藏输入中。Skill 保存非秘密引用并按本地记录指引客户。App 创建、批准、同意授权与续期由有权的人在对应入口完成。

## 6. 补充：仅验证一个已知账号

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

这条路径需先核对自有 App、人物和具名账户。临时输入仅用于这次会话。需要日常接续时回到自有 App 路线，在同一项目保存具名账户与非秘密引用。
