# Motion：用广告组合与探索产能评价创意系统

MC-CRE-07 · 2026-11-27.1

作者或归属 Motion (Creative Analytics)。来源 YT-cSm2NPvKLHo，见 [来源登记](../sources.json)。

## 原问题

要求每条广告都获胜或只追高命中率，导致团队回避新方向；又把所有低消耗素材都当失败。

## 机制与设计归属

把少数增长贡献者、中等消耗稳定者及探索成本作为组合评价对象，关注持续产生和识别有效素材的能力；研究中的相关性不能直接解释为数量导致效果。

## 所需输入

- 广告 ID、上线时间、概念/格式与实际制作成本
- 一致窗口的花费、订单、收入及经营口径
- 客户自定义的胜出和中等表现标准
- 创意生产产能及测试预算
- 样本覆盖、未投递与已充分试验的区分

## 条件化决策规则

```json
[
  {
    "condition": "准备使用外部 5–8% 胜出率",
    "action": "先核实赢家指标、样本与窗口；本来源定义不清，不能把该比例设为客户目标。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "只看命中率会抑制新方向探索",
    "action": "同时报告新概念数量、实际测试机会、增长贡献、稳定贡献和探索成本。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "素材中等表现且有稳定业务贡献",
    "action": "评估保留在组合中的价值，不仅因不是冠军而停用。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "高成本制作延缓上线",
    "action": "可对比更轻量的形式，但仍须满足品牌、产品解释和真实证明需求。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "数据只证明高产能与好表现相关",
    "action": "不把增加数量作为必然增益，先按资源约束设计可验证方案。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 明确本账户的结果指标与评价窗口，避免套用报告未明定义。
- 区分未获得投递、已测试低表现、中等稳定与可扩量贡献者。
- 核算创意制作成本与探索支出，不只看媒体 ROAS。
- 按组合贡献审查保留和淘汰，保存中等素材的理由。
- 设计可持续产能与探索比例，轻量格式只是选项。
- 输出观察结论与尚未证明的因果解释，持续回填实际结果。

## 预期输出

- 创意组合健康报告
- 制作与媒体探索成本账
- 定义清楚的胜出/稳定/未检验标签
- 产能约束下的下一轮候选计划

## 指标与计算

```json
[
  {
    "name": "内部命中率",
    "formula": "符合已定义胜出条件的广告数 / 符合纳入条件的测试广告数",
    "limitation": "只作为另行设计的运营指标；记录新旧广告、观测期和有效测试定义。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "来源 5–8% 指标",
    "formula": null,
    "limitation": "作者 earns 十倍的指标含义未明确，标题与字幕样本量不一致，不计算客户差距。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "探索总成本",
    "formula": "探索媒体支出 + 对应创意制作成本",
    "limitation": "成本归属和复用方式需客户定义，不能把缺失制作成本当零。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 能关联广告、制作成本和结果
- 团队愿意保留试验失败与无投递记录
- 业务允许持续探索但有风险上限

## 适用情况

- 需要评价创意团队与广告组合健康的品牌或代理商

## 不适用情况

- 报告样本口径未清却希望精确对标
- 没有利润/成本数据却要求计算净收益

## 风险

- 把更多数量误认为更高质量或必然更多利润
- 用外部不匹配样本压迫创意团队
- 亏损素材借“中等稳定”标签长期存活

## 反例

- 设计反例：增加大量同质素材扩大成本而无新学习，数量提升不构成成功。
- 设计反例：持续亏损素材不因需要稳定组合就必须保留。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-STR-01",
    "difference": "V8 倾向让预算集中于顶尖素材，Motion 强调中等贡献者可能支撑探索。"
  },
  {
    "method_id": "MC-CRE-03",
    "difference": "未消耗的原因需先分辨，不能把报告中的 losers 当作所有低消耗广告的质量裁决。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "商业报告的视频概述，完整样本方法及表格未取得；胜者定义和样本量保留歧义。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 16,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 35,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 70,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 105,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 123,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 160,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 210,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 227,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 35,
    "end_seconds": null
  },
  {
    "source_id": "YT-cSm2NPvKLHo",
    "start_seconds": 210,
    "end_seconds": null
  }
]

## Skill 适配

区分组合贡献和探索，保留适用样本条件。计划字段、客户授权、平台回读和作者案例分别取证。
