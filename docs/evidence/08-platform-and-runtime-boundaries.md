# 平台、运行及验收边界

证据以候选 0.1.0.dev5+v1win、提交 525bee82b2e1abcc8da8b01b4834085f1798ebc0 为准。2026-11-27 只读查询的公开 GitHub main 为 9b3370d7799a84c9db61441e20ed92f29e58217a。候选代码及本次索引在交付的本地分支内可打开。当时的公开仓库以历史 V1.6R2 为准。

| 范围 | 工程、测试及现场状态 | 可用客户路径 |
| --- | --- | --- |
| macOS 本机安装、升级、恢复 | 实际隔离环境运行；本机 CLI 与合成广告工作流有回执。Codex 桌面自然语言本候选未独立验收。 | 按 [Mac 安装](../../codex-ads-live/references/INSTALL_RUN.md)。EV-W01、EV-E01、EV-E02、EV-F01。 |
| Windows 原生安装和 Codex | Windows 10/11 预检、PowerShell、Credential Manager 边界有代码与说明，Win32 调用用 mock 测试；Windows 原生安装、系统实际凭据和 Codex UI 为 NOT_RUN_NO_WINDOWS_HOST。 | [Windows 客户说明](../../codex-ads-live/references/WINDOWS_CLIENT.md)。EV-W02、EV-W03。 |
| Meta 具名只读与受管写入 | Graph/Marketing API 合同 v27.0。真实账户需要本人或业务管理员批准和回读。当前真实读取、写入、启用、投放和消费未复验。 | 本机预览与本地工作区；Mac 可用独立管理服务走具名批准。Windows 通过已获批的 Ads Manager 人工执行和回读。 |
| Ads Library | 合成地区、Ad/Page 双 ID、缺字段及游标测试；真实查询与覆盖取决于官方资格和地区。 | 客户已有且可公开的观察，按双 ID 导入。 |
| Shopify | 固定 Admin GraphQL 2026-10 的只读计划、去标识回包、分页、退款与 CSV 合同。真实 OAuth 与实际商店 API 请求未执行。 | 由商家授权的回包或 CSV 输入。 |
| Facebook Page Live | 具名 Live 对象、评论和授权导出合同。公开 Page live_videos 列举边不支持读。连续真实 Page/Live 待具名对象和权限。 | 具名数据或主办方导出。V1 采集范围只含数据和评论。 |
| FFmpeg/SRT 与 Whisper | Mac 本机 FFmpeg 抽帧、抽音和现成 SRT 实测。先前模型下载超时；没有本候选自动转写回执。 | 自备带时间码字幕；另用已核本机模型取得转写结果。 |
| 看守与运行时 | 本机配置、租约、重试、停止和报告有本机或合成证据。 | 运行时保持进程，睡眠或中止后核查报告时效，按原范围续跑。 |
| V2 | 三项预留，分别为实时 Live 音视频、多 Page/多直播间与云常驻、SaaS 工作台。 | 独立路线与验收。 |

独立核验须把 **实现、已测、本机演示、真实平台已验、条件未满足** 分开。用[证据登记](evidence-register.csv)直接定位固定归档与校验值。状态字段中的 NOT_RUN 不表示代码缺失。支付、授权、部署或公开发布各依自己的决定和批准。

[Meta v27.0](https://developers.facebook.com/docs/marketing-api/reference/v27.0/) · [Meta Page Live](https://developers.facebook.com/docs/graph-api/reference/page/live_videos/) · [Shopify scopes](https://shopify.dev/docs/api/usage/access-scopes) · [Whisper 模型](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md) · [Windows 客户系统支持](https://learn.microsoft.com/en-us/windows/release-health/supported-versions-windows-client)

## 本次候选的平台边界

macOS 的解压、全量摘要与 CLI 原入口已验。Windows 本轮仅有 UTF-8 BOM 输入和 Win32 模拟回归。实机、Codex 桌面和凭据管理器的原生验收状态为 `NOT_RUN_NO_WINDOWS_HOST`。实际连接器登录及受管写入仍按具名资产办理。

本轮 Windows 验收经用户豁免（`PASS_BY_USER_WAIVER`），实际状态仍为 `NOT_RUN_NO_WINDOWS_HOST`。macOS 项目 CLI 安装实际复核。桌面选择与调用用[最短验收步骤](../../codex-ads-live/references/MACOS_CODEX_ACCEPTANCE.md)复核（`HUMAN_GATE`）。
