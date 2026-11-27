# 竞品广告研究

## 当前候选的客户路径

记录原 Page 和广告资料库编号。权限中断保留已观察分页。标题、CTA、媒体观察可进入创意方法，缺正文时仍保留事实。

| 能力 | 客户任务 | 入口 | 回传场景 |
| --- | --- | --- | --- |
| C07 | 公开竞品广告研究 | `public-ad-query → workspace-public-ad-observations → workspace-creative-research` | A04 |

完整输入、回执和 SHA-256 随同一候选归档。以下各节保留既有参考和历史验证层级。

将具名公开广告观察与创意推断分开，保留可复查的样本范围。

能力 ID C06、C07、C10、C30。见[矩阵](capability-matrix.md)。

## 客户入口与输入

在客户项目选择 $codex-ads-live，说明「按具名广告和公共主页核对资料库记录，只把来源可确认的内容纳入研究。」

地区、查询范围、Page ID、Ads Library Ad ID、保存的公开记录与已获准素材引用。

```text
public-ad-query --input <具名查询JSON>
```

## 流程和专业依据

查询预览 → 有资格时调官方接口或导入导出 → 按 Ad/Page、分页和字段核对 → 标记观察及推断 → 关联创意。

**官方事实或工具合同**　官方资料库定义可查询对象。文案与媒体可用字段属于观察；对方销售和投放策略仅能作待核假设。 [Ads Archive v27.0](https://developers.facebook.com/docs/graph-api/reference/ads_archive/) · [Archived Ad](https://developers.facebook.com/docs/marketing-api/reference/archived-ad/)

**从业者方法与客户业务规则**　先挑同市场并可比的样本，再把观察、素材引用和角度假设分开。商品与素材权利由客户确认。

## 代码、输入输出和验证

| 能力 | 客户 CLI | 代码 | 输入 → 输出 | 测试与可引用结果 |
| --- | --- | --- | --- | --- |
| C06 | media-buyer prepare,knowledge-search,strategy-review | [operator_v1.py:knowledge_search](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L130)、[operator_review.py:strategy_review](../../codex-ads-live/src/codex_ads_live/operator_review.py#L129) | 目标、关键词和商业证据 → 候选策略及适用条件，来源/观点/假设保留 | EV-E09，EV-E10，EV-E11，EV-F01，EV-F06。492 条索引和 13 条整理观点；有分页证据。 |
| C07 | public-ad-query,public-ad-inspect,media-buyer prepare | [ads_library.py:inspect_detail](../../codex-ads-live/src/codex_ads_live/ads_library.py#L150)、[creative_research.py:import_public_sources](../../codex-ads-live/src/codex_ads_live/creative_research.py#L281)、[ads_library.py:run_query](../../codex-ads-live/src/codex_ads_live/ads_library.py#L107)、[ads_library.py:fetch](../../codex-ads-live/src/codex_ads_live/ads_library.py#L55) | 有界地区查询或已存 Ad/Page 双 ID → 公共观察、字段覆盖和游标；推断独立 | EV-E16，EV-E17，EV-E18，EV-F01。合成 Ad/Page 双 ID，缺字段不补成实测。 |
| C10 | workspace-creative-research,media-buyer prepare,creative-compare | [creative_research.py:freeze_selection](../../codex-ads-live/src/codex_ads_live/creative_research.py#L266)、[media_buyer.py:_intake](../../codex-ads-live/src/codex_ads_live/media_buyer.py#L31) | 品牌、商品、草案证据和已登记素材 → 复用或对照创意 Brief、角度和变体 | EV-E12，EV-E13，EV-F01。同一创意变体进入本地草案。 |
| C30 | knowledge-search,workflow-guide,partner-brief-review | [operator_v1.py:knowledge_search](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L130)、[partner_brief.py:review](../../codex-ads-live/src/codex_ads_live/partner_brief.py#L32)、[operator-knowledge.json](../../codex-ads-live/references/operator-knowledge.json)、[operator_v1.py:operator_route](../../codex-ads-live/src/codex_ads_live/operator_v1.py#L174) | 官方资料、观点、案例和独立模块合同 → 保留来源、适用、撤回及验证状态 | EV-E09，EV-E10，EV-E11，EV-E41，EV-E42，EV-E43，EV-F05，EV-F06。本机复验 492 条索引与 14 个具名交接角色。 |

**现成输入输出示例**　同一个合成资料库记录用真实代码校验 Ad / Page 双编号。资料库缺字段保留未知，观察和角度假设分别输出。

**验证调用**　在源码根目录运行 python3 -I -B -m unittest discover -s tests -p test_v1_operator_boundaries.py。[已有测试文件](../../tests/test_v1_operator_boundaries.py)。批次、受管宿主、安装及恢复以证据登记中的原合成或实机回执为准。

以上 CLI 为安装版 run.py 的子命令。原评测、文件路径、校验值及证据类型见[证据登记](evidence-register.csv)。[只读回传摘录](evidence-receipts.json)保留可引用字段。

## 能证明什么与当前边界

可证合成匹配、分页、缺字段和原归属；公开样本不能证明对方的成交、收入或 ROAS。

Ads Archive v27.0；账户资格、国家、类别和响应覆盖决定数据范围。

macOS 有隔离安装及本机或合成的结果。Windows 有兼容指南和模拟边界，原生状态为 NOT_RUN_NO_WINDOWS_HOST。参见[平台边界](08-platform-and-runtime-boundaries.md)。

**缺条件时的客户替代路径**　权限不足时选择此前获取且可公开的记录，保留原观察时间和范围。
