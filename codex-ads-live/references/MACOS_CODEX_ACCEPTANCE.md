# 在 macOS Codex 桌面核对客户 Skill

使用客户安装 ZIP，按 [macOS 安装](INSTALL_RUN.md) 解压到专用新项目。安装回读应为 `PROJECT_INSTALL_VERIFIED`。项目内应存在 `.agents/skills/codex-ads-live/SKILL.md`，其中的 frontmatter 含 `name: codex-ads-live` 与 `description`。

1. 在 macOS 的 ChatGPT/Codex 桌面打开这个项目的**实际本地目录**，新建聊天。项目内安装通常会自动刷新。若选项未出现，先确认目录，再重开项目或重启 Codex。
2. 输入 `$codex-ads-live`，**从列表选中 Skill**。如果有重名项，选项目来源。发送「只运行这个项目已安装的广告 Skill 的 preflight。告诉我 SKILL.md 的路径、系统、包完整性和实际返回的版本。保持本机只读。」
3. 验收时留一张包含所选 Skill 和回执的截图，或保存可核对的文字。结果应指向此项目的 `.agents/skills/codex-ads-live/SKILL.md`，runner 为同目录的 `run.py`，系统为 `Darwin`，`checks.package_integrity` 为 `PASS`，版本与安装 ZIP 一致。桌面成功要求看到选择与实际执行两项。

如果仍找不到，请用已安装路径执行 `run.py preflight` 核对当前项目。终端通过只记录 CLI 可用；桌面选择与调用保留为 `HUMAN_GATE`，待按上述步骤实测。

[OpenAI Skill 结构、项目发现和选择方式](https://developers.openai.com/codex/skills)
