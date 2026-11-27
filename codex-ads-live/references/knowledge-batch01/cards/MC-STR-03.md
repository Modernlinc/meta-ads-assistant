# Mason：Broad ABO 测试、分阶段扩量与毕业资格

MC-STR-03 · 2026-11-27.1

作者或归属 Mason Littlejohn。来源 YT-w1JqdrQIYkA，见 [来源登记](../sources.json)。

## 原问题

创意未获测试机会、凭短期高 ROAS 过早毕业，或没有明确观察和停止条件。

## 机制与设计归属

以独立 Ad Set 预算减少素材没有消耗的问题，并分别设置止损、测试内扩量和向获胜池毕业的条件。

## 所需输入

- 每个测试的素材及变体关系
- 目标 CPA、ROAS 的业务口径
- 账户币种、可承受总测试预算、风险偏好
- 每日购买数、订单价值、花费及归因延迟
- 转移时的 Post ID 和原测试来源

## 条件化决策规则

```json
[
  {
    "condition": "要判断创意而非兴趣受众",
    "action": "采用 Broad 下每个测试一个 Ad Set 的预算安排，通常每组一至四条变体。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "预算设计",
    "action": "保留作者 25–50 美元/日及新品牌 5–10 美元例子；实际需由目标 CPA 和总资金约束决定。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "花费达到目标 CPA 二至三倍仍未达标，或数日持续低于目标",
    "action": "按作者止损规则列为暂停候选；边缘表现可减预算或延长观察，必须明确选择条件。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "测试内连续二至三天达到目标",
    "action": "作者允许开始扩量，可选 10–25% 或翻倍；后者明确需更高风险承受能力。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "达到连续七天目标且有多次购买",
    "action": "考虑毕业到获胜池；单个高客单订单造成的短期高 ROAS 不视为充分证据。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 执行步骤

- 按创意问题定义测试单元和唯一 ID，明确是否只有一个变量。
- 使用产品利润和允许损失设置总测试预算，不直接采用作者金额。
- 生成 Broad、Ad Set Budget 的计划草案，每组记录对应广告。
- 逐日记录目标、花费和订单分布，分开判断止损、起量和毕业。
- 在扩量方案中显示风险、前值、新值、比较窗口与回读要求。
- 获胜素材进入 MC-STR-04 时保留原测试与 Post ID，避免把新复制结果当旧结果。

## 预期输出

- 测试单元及预算草案
- 暂停/观察/起量/毕业的独立状态表
- 证据不足与延迟回读清单

## 指标与计算

```json
[
  {
    "name": "花费/目标 CPA",
    "formula": "test_spend / target_cpa",
    "limitation": "作者的二至三倍边界不是统计显著性。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "扩量预算",
    "formula": "current_budget * (1 + selected_change_rate)",
    "limitation": "比例是由用户选择的情景参数，不能自动默认翻倍。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "目标连续达成天数",
    "formula": "逐日目标达成的连续计数",
    "limitation": "需同币种和归因窗口；不把七日总值当七个连续日。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 可承受为多个测试单元分别付费
- 购买事件与目标口径可靠
- 明确测试内扩量与毕业为两阶段

## 适用情况

- 素材探索因缺少消耗而受阻，且有足够预算和观测能力的账户

## 不适用情况

- 总预算仅能支持很少购买却同时开大量组
- 购买严重延迟而按每日读数立即判断
- 目标 CPA/ROAS 未定义

## 风险

- 广告组碎片化与信号不足
- 翻倍扩量超出现金承受能力
- 连续天数规则在低样本下不稳
- 多个变体同组仍可能分配不均

## 反例

- 设计反例：单次大额订单使两日 ROAS 达标，但没有重复购买信号，不能直接毕业。
- MC-STR-02 提供 CBO 内探索作为减少人工预算控制的另一选择。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-STR-01",
    "difference": "与集中单 Ad Set 自适应分配相反。"
  },
  {
    "method_id": "MC-CRE-03",
    "difference": "Nikhil 强调一广告一组；Mason 允许同一测试一至四个变体，且有额外毕业标准。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "预算层级在字幕明确；画面工具报告存在异常，不以其金额为依据；效果未独立验证。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 46,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 109,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 126,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 176,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 208,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 290,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 337,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 353,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 401,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 401,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 193,
    "end_seconds": null
  }
]

## Skill 适配

分组预算仅用客户上限，样本不足不可机械开组。计划字段、客户授权、平台回读和作者案例分别取证。
