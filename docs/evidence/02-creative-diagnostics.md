# 广告素材诊断

## 当前候选的客户路径

可比较实际抽帧、分镜和文案并保存同版选择。已启用的实际效果按广告回读。合成样例查看了两张图，使用同一素材摘要进入草案。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C08 | 广告素材图片/视频分析 | `media-analyze → creative-compare → media-buyer prepare/learn` | A05 |
| C09 | 素材审阅与版本管理 | `media-analyze → creative-compare → media-buyer prepare/learn` | A05 A13 |
| C10 | 创意Brief/文案/广告变量 | `method → workspace-method-record → media-buyer prepare` / `media-analyze → creative-compare → media-buyer prepare/learn` | A03 A05 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

为创意审阅保留可核对的帧、音轨、字幕、摘要和评语。

能力 ID C08、C09、C10。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「用这份素材和自带 SRT 检查卖点、钩子、转化点、真人感，列出有时码的观察。」

获准使用的本机图片或视频；FFmpeg 与 ffprobe；有对应 SRT 或受支持的本地语音模型。

```text
media-analyze --file <授权素材> --subtitles <已有SRT> --output <新目录>
```

## 流程和专业依据

文件检查 → 探测媒体并提取证据 → 对齐字幕 → Codex 或审阅者填写观察 → 校验引用并绑定素材摘要。

**官方事实或工具合同**　帧、音轨及字幕由本机工具提取，观察由看过证据的 Codex 或审阅者提交。 [FFmpeg 文档](https://ffmpeg.org/documentation.html) · [Whisper 模型](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md)

**从业者方法与客户业务规则**　先看具体帧、听提取的音轨，并核对字幕。提交结构化观察时用对应的 FRAME 或 SUBTITLE 定位，按行业和目标说明判断。

视频默认在 0、约 1/3、约 2/3 时长处抽样，时间去重后最多三个。脚本输出帧、可提取的音轨、字幕时码、素材摘要和审阅页。Codex 或审阅者查看实际证据后，填写卖点、钩子、转化点、真人感四维观察，再通过 `--observations` 绑定到同一素材。结构化评语当前引用 `FRAME` 或 `SUBTITLE`，真人感须引用 `FRAME`。未提交观察的维度显示 `UNOBSERVED`。脚本校验素材摘要和证据引用，评语语义由审阅者判断。当前无 Meta 官方评分或广告效果评分。

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C08 | media-analyze | [media_evidence.py:analyze](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L194)、[media_evidence.py:read_srt](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L90)、[media_evidence.py:probe](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L57) | FFmpeg 可读本机素材、可选 SRT 或已核模型 → 抽样帧、音轨、字幕时码和素材摘要；缺项显式。视频默认在 0、约 1/3、约 2/3 时长抽样 | EV-E12，EV-E14，EV-E15。Mac 真实抽帧、抽音、SRT；Whisper 未验。 |
| C09 | workspace-creative-research,creative-compare,media-analyze,media-buyer prepare | [creative_research.py:freeze_selection](../../codex-ads-live/src/codex_ads_live/creative_research.py#L266)、[media_evidence.py:analyze](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L194)、[media_evidence.py:load_observations](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L124)、[media_evidence.py:evaluate_observations](../../codex-ads-live/src/codex_ads_live/media_evidence.py#L136) | 原概念、变体、素材摘要及 Codex 或审阅者提供的观察 → 把输入观察与同一素材及 FRAME 或 SUBTITLE 绑定；保留版本和 UNOBSERVED，真人感须引用 FRAME | EV-E12，EV-E13，EV-E15，EV-F01。可回查摘要与报告。脚本校验摘要和证据引用，评语语义由审阅者判断。 |
| C10 | workspace-creative-research,media-buyer prepare,creative-compare | [creative_research.py:freeze_selection](../../codex-ads-live/src/codex_ads_live/creative_research.py#L266)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 品牌、商品、草案证据和已登记素材 → 复用或对照创意 Brief、角度和变体 | EV-E12，EV-E13，EV-F01。同一创意变体进入本地草案。 |

**现成输入输出示例**　2 秒合成素材和现成 SRT 的归档示例输出 0、666、1333 ms 三个抽样帧，transcript_state 为 USER_SUPPLIED_SRT，四维均为 UNOBSERVED。该示例证明本机提取、字幕对齐及文件绑定。可展示具名审阅者的实际观察，或标明使用合成观察示例。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_v1_customer_workflows.py。[已有测试文件](../../tests/test_v1_customer_workflows.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

帧、音轨和字幕时码可回查。四维中未提供观察的项目为 UNOBSERVED；广告表现和 Whisper 转写需各自的证据。

素材权利须具名。自动转写额外依赖合适的 whisper-cli 和可信模型 SHA-256。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　可提供获准使用的现成 SRT。缺音轨、字幕或观察时，保留各自的状态。
