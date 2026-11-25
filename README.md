# 摩登领客 · Meta 广告助手

面向 macOS 上的 Codex 项目。使用客户已有的品牌、商品和市场资料准备 Meta 广告，读取获授权账户的数据，并沿同一广告对象持续复盘。首次接入的信息保存在客户工作区，日常回到原项目即可接续。

## 快速开始

1. [下载 V1.6R2 客户使用包](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.6-r2/modernlinc-meta-ads-customer-v1.6r2.zip)，解压后打开 `先读我.md`。
2. 在自己的 Mac 和 Codex 客户项目中，按随包安装说明安装。需要 Python 3.12 或更新版本，运行版本为 `0.1.0.dev1+v16r2`。
3. 在该项目使用 `$codex-ads-live`，按说明接通自己的 Meta App、具名广告账户与 Page，提供实际商品资料和获准使用的素材。
4. 已接通的客户继续使用原工作区。准备广告、上传素材、建单或启用时，核对本次具体对象、权限、预算与负责人批准。

## 本版本可以完成什么

- 保存品牌资料、研究来源、素材概念及变体；把评语关联到实际观察和素材文件。
- 在具备访问条件时查询公开广告，或导入实际观察，整理为自己的研究来源。
- 准备单图或视频广告；通过配置好的受管服务上传素材，核对平台回执和就绪状态。
- 按最多六份素材串行创建共享父级的暂停广告，保存逐项记录，沿原尝试恢复。
- 确认上传未发送并封存原尝试后，在新的批准下继续；同一客户范围内的已就绪媒体可在新计划复用。
- 读取同一广告的素材、活动和指标，形成有来源的复盘与下一步建议。

自动写入需要管理员完成独立受管服务配置，并按具体操作批准。批次后续预览、启用和复盘入口目前处理最后完成的 Ad；父级启用、支出控制和停止时刻按手册中的具名步骤准备。公开广告观察不等于竞品成交数据，复盘建议不构成效果承诺。

## 使用指南

- [安装与日常使用](codex-ads-live/references/INSTALL_RUN.md)
- [账户接通流程](codex-ads-live/references/CUSTOMER_SOP.md)
- [品牌、素材与广告复盘](codex-ads-live/references/CREATIVE_AND_DELIVERY.md)
- [第一条广告的步骤](codex-ads-live/references/FIRST_LAUNCH_CUSTOMER.md)
- [管理员受管配置与批准](codex-ads-live/references/FIRST_LAUNCH_LOCAL_AUTHORITY.md)

## 下载与版本

- [V1.6R2 发布页](https://github.com/Modernlinc/meta-ads-assistant/releases/tag/v1.6-r2)
- [客户使用包](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.6-r2/modernlinc-meta-ads-customer-v1.6r2.zip)
- [单独的 Skill 安装件](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.6-r2/codex-ads-live.zip)
- [源码归档](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.6-r2/modernlinc-meta-ads-source-v1.6r2.zip)
- [下载校验值](https://github.com/Modernlinc/meta-ads-assistant/releases/download/v1.6-r2/SHA256SUMS.txt)

已有项目升级时保留原工作区、计划、媒体请求包和历史记录，按安装说明分阶段接续；受管端同步使用本版本。当前发布类型为预发布，客户按上述支持环境和具体权限范围使用。

MODERNLINC 自有源码及说明采用 [MIT 许可](LICENSE)。第三方材料的许可和归属见[随包说明](codex-ads-live/references/DISTRIBUTION_NOTICES.md)。
