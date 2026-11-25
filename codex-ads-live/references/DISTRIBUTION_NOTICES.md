# 随包许可与来源

安装器和 Skill 在本地使用 Python 3.12 或更新版本。MODERNLINC 在本公开版发布的自有源码及说明采用 [MIT 许可](../LICENSE.txt)。随包方法模块及第三方材料按各自目录内的许可和归属声明提供。

- 四个方法模块保留各自的 LICENSE.txt 和 NOTICE.md。广告创意目录另附 Corey Haines 及 Nexscope AI 的 MIT 许可文本。[方法目录](../methods/ADAPTATION.md)
- 时区回退文件来自 tzdata。来源、版本和摘要见[时区数据来源](../assets/iana-timezones/PROVENANCE.json)，附有[许可](../assets/iana-timezones/LICENSE.txt)及[完整条款](../assets/iana-timezones/LICENSE_APACHE.txt)。
- `vendor/t0_core.py` 是 MODERNLINC 自有的本地数据与模拟操作模块。模拟练习使用虚构数据。

## V1.6 的实现参考

下列仓库用于核对机制与请求合同。V1.6 新增 Python 实现由 MODERNLINC 编写，安装件沿用现有许可证和随包归属。

- 素材观察维度与品牌、创意简报流程参考 MIT 许可的 [Master Skill](https://github.com/swaylq/master-skill/tree/3dd8a77bcf2d6c415a30e8d2093d3b09823ea136) 和 [Marketing Skills](https://github.com/coreyhaines31/marketingskills/tree/8fd2fcade2fd6629026e9dfe6d133b55b36968b9)。
- 冻结批次及逐项取回思路参考 MIT 许可的 [Ocean Watch](https://github.com/westng/ocean-watch/tree/9d02ba5e6477af3acb462f2d3daac4b77edf9131)。
- 公开样本与分页合同参考 MIT 许可的 [Meta Ads Library 客户端](https://github.com/eminberkayd/meta-ads-library/tree/730c2573fb6e189a7aaa5f98d16689cb4c558214)。广告与创意关联和账户活动字段参考 MIT 许可的 [GoMarble Facebook Ads MCP](https://github.com/gomarble-ai/facebook-ads-mcp-server/tree/a3b555a12dff5b888b172ef9113dd1f36d69e525)。
- 公开样本及详情的关联参考 MIT 许可的 [HyperFX Marketing Skills](https://github.com/hyperfx-ai/marketing-skills/tree/463e5026e5db09123f99e668622bf5e3879c08bf)。具名首投和报表下钻参考 MIT 许可的 [GoMarble Marketing Agent](https://github.com/gomarble-ai/marketing-agent/tree/3e58349a6d29076c474a3c3b32b5afe2a3b8cdcd)。
- 媒体上传及广告对象请求参考 [Meta Business SDK 固定源码](https://github.com/meta/facebook-python-business-sdk/tree/1145e73272236f03f495099ee2cb1bdcc08a4828)。该 SDK 的许可由 Meta 管理，安装件使用自己实现的受管 HTTP 请求。
