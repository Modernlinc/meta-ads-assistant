# 在客户项目接入或恢复 Meta

先打开 [客户首次接入与续接向导](CUSTOMER_SOP.md)。向导列出路线选择、客户本人在官方页面的步骤、接通标志和失败恢复。这一页用于核对当前命令返回的状态。

从安装项目内调用 `run.py workspace-connect --workspace WORKSPACE`。已有工作区始终用原路径。新项目在 `INITIALIZE_WORKSPACE` 时加 `--start`，返回 `CHOOSE_ROUTE` 再选路线。

| Step | 下一步 |
| --- | --- |
| `PREPARE_APP` | 自有 App 或系统用户路线核对原 App、业务组合和资产归属，使用 `--app-id`、`--business-id` 登记。 |
| `ENROLL_USER_REFERENCE`、`DISCOVER_ACCOUNTS` | 用同 App 的个人用户和 `ads_read`，登记本机引用；本人核 Proof 与范围后用 `--discover --proof-not-required`。 |
| `CONNECT_IN_OFFICIAL_HOST`、`HOST_ACCOUNT_READY` | 在原宿主授权和查询。仅用 `--host-receipt` 导入当前具名账户列表；选择后能继续准备同账号草案。 |
| `SYSTEM_USER_CONFIGURATION`、`DISCOVER_SYSTEM_ACCOUNTS`、`SYSTEM_USER_READ_READY` | 系统用户和开发者检查口令分别登记。在已核 Proof 条件时发现、选择，最后用 `native-inventory get account` 做具名读取。 |
| `SELECT_ACCOUNT`、`CHECK_NAMED_ACCOUNT` | 只选择当前路线已观察到的 ID，并提供归属引用。自有 App 路线继续 `workspace-connection-prepare → workspace-bind → workspace-refresh`。 |
| `NO_OBSERVED_ACCOUNTS`、`REVIEW_APP_PROOF`、`RECONNECT` | 按向导核资产归属、安全设置和原身份，续跑当前步骤。 |
| `REUSE_WORKSPACE` | 核对账号和最近观察，继续原任务。 |

输出里的身份和权限各自用于对应路线。原 `workspace-connect --help` 可查每个参数。
