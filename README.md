# 摩登领客 · Meta 广告助手

V1 客户预览版已完成本地验收。macOS 已验证项目安装和命令行运行，Codex 桌面首次使用可按手册确认 Skill。Windows 提供 PowerShell 安装方式，原生实机尚未验证。

使用自己的品牌、商品和市场资料准备 Meta 广告，读取获授权账户的数据，并沿同一广告对象复盘。接入信息保存在客户工作区，日常可回到原项目接续。

## 快速开始

1. [下载 V1 客户预览版](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.0.0-preview/modernlinc-meta-ads-customer-v1.0.0-preview.zip)，解压后打开 `CUSTOMER_START_HERE.md`。
2. 按[macOS 安装步骤](codex-ads-live/references/INSTALL_RUN.md)操作。Windows 安装路径见[待实机验收的 PowerShell 指南](codex-ads-live/references/WINDOWS_CLIENT.md)。两份指南均要求 Python 3.12 或更新版本。
3. 在该项目选择 `$codex-ads-live`，使用[首次接入与续接向导](codex-ads-live/references/CUSTOMER_SOP.md)。按原项目或当前需求采用自有 App、官方宿主或企业系统用户，读取范围按具名资产确认。
4. 已接通的客户继续使用原工作区。准备广告、上传素材、建单或启用时，核对本次具体对象、权限、预算与负责人批准。

## 本版本可以完成什么

- 保存品牌资料、研究来源、素材概念及变体；把评语关联到实际观察和素材文件。
- 在具备访问条件时查询公开广告，或导入实际观察，整理为自己的研究来源。
- 准备单图或视频广告；通过配置好的受管服务上传素材，核对平台回执和就绪状态。
- 编译多组 ABO/CBO 或复用已有父级的暂停广告计划，保存每项请求和原尝试。
- 确认上传未发送并封存原尝试后，在新的批准下继续；同一客户范围内的已就绪媒体可在新计划复用。
- 读取同一广告的素材、活动和指标，形成有来源的复盘与下一步建议。

自动写入需要管理员完成独立受管服务配置，并按具体操作批准。具名批次按 Ad 分开预览、核对及复盘。父级启用、支出控制和停止时刻按手册中的具名步骤准备。公开广告观察不等于竞品成交数据，复盘建议不构成效果承诺。

## Product Capabilities & Evidence

V1 FINAL 的六项一级能力覆盖 30 个二级能力。索引分别保留历史候选和本次具名核验。版本和代码基线见证据详情。

- [账户接入与业务准备](docs/evidence/01-meta-account-and-business.md) · [广告素材诊断](docs/evidence/02-creative-diagnostics.md) · [投放策略与广告计划](docs/evidence/03-strategy-and-ad-planning.md)
- [竞品广告研究](docs/evidence/04-competitor-ad-research.md) · [素材管理与受控广告执行](docs/evidence/05-material-and-controlled-execution.md) · [账户诊断、经营核算与持续复盘](docs/evidence/06-account-diagnosis-and-business-review.md)
- [30 项能力矩阵](docs/evidence/capability-matrix.md) · [横向底座](docs/evidence/07-cross-cutting-capabilities.md) · [平台与验收边界](docs/evidence/08-platform-and-runtime-boundaries.md)
- [依赖图](docs/evidence/capability-dependency-map.md) · [证据登记](docs/evidence/evidence-register.csv) · [证据版与候选信息](docs/evidence/evidence-version.json)

安装一次后，用自然语言描述 Meta 广告目标。助手自动加载四项随包方法或有需要时获取固定版本参考，应用到客户资料并把结果交给本机业务模块。[方法使用说明](codex-ads-live/methods/ADAPTATION.md)

## 使用指南

- [安装与日常使用](codex-ads-live/references/INSTALL_RUN.md)
- [账户接通流程](codex-ads-live/references/CUSTOMER_SOP.md)
- [品牌、素材与广告复盘](codex-ads-live/references/CREATIVE_AND_DELIVERY.md)
- [第一条广告的步骤](codex-ads-live/references/FIRST_LAUNCH_CUSTOMER.md)
- [管理员受管配置与批准](codex-ads-live/references/FIRST_LAUNCH_LOCAL_AUTHORITY.md)

## 下载与版本

- [当前 V1 客户预览版发布页](https://github.com/Modernlinc/meta-ads-assistant/releases/tag/v1.0.0-preview) · [安装件校验值](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.0.0-preview/SHA256SUMS.txt)

已有项目升级时保留原工作区、计划、媒体请求包和历史记录。按安装说明分阶段接续，并核对对应的受管端版本。

MODERNLINC 自有源码及说明采用 [MIT 许可](LICENSE)。第三方材料的许可和归属见[随包说明](codex-ads-live/references/DISTRIBUTION_NOTICES.md)。
