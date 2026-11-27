# Sarah Levinger：按客户心理任务审阅创意与补齐 Brief

MC-CRE-05 · 2026-11-27.1

作者或归属 Sarah Levinger。来源 YT-po85BKDTG-Y，见 [来源登记](../sources.json)。

## 原问题

广告虽按冷暖漏斗排列，但内容集中在产品和折扣，缺少需求被唤起或品类探索的表达。

## 机制与设计归属

作者将触发、探索、评估和购买视为可往返的心理状态，用每条创意服务的心理任务补充媒体漏斗分类。该分类是策略框架，不是对单个客户内心的实测。

## 所需输入

- 现有广告正文与素材
- 客户采访、评价、客服和购买异议
- 产品、品类与可核实证明
- 落地页/支付/履约等摩擦证据
- 创意对应的实际结果与观察窗口

## 条件化决策规则

```json
[
  {
    "condition": "用户尚未把问题变成明确需求",
    "action": "拟定回应生活变化、累积不满或场景触发的内容。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "客户正在理解方案和品类",
    "action": "准备探索内容，不立即假定其已在产品间比较。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "客户比较方案且担心选错",
    "action": "用真实证明、适配条件和风险信息建立选择信心。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "购买意向已强但行动受阻",
    "action": "核查真实摩擦，不只增加折扣。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "心理状态判断没有 VOC 支撑",
    "action": "标为待验证假设，同一素材可有多个标签；不强推线性漏斗。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 保留现有漏斗标签，新增心理任务标签，避免覆盖旧知识。
- 逐条创意写明其假设中的客户处境、情绪和需要解决的疑问。
- 将标签与真实 VOC/素材证据关联，未知保持未知。
- 找出触发、探索、评估或购买摩擦上的空缺。
- 给每个空缺形成可制作 Brief、证明要求和验证指标。
- 根据后续实际结果修正标签，不把分类完整误认为效果已验证。

## 预期输出

- 创意—心理任务审阅矩阵
- 有证据与假设分开的缺口清单
- 按心理任务生成的 Brief 与验证计划

## 指标与计算

```json
[
  {
    "name": "状态覆盖",
    "formula": "有内容支持的心理任务类别数 / 本次选定类别数",
    "limitation": "仅为 MODERNLINC 的内容盘点指标，不是效果评分或作者统计结论。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "购买摩擦",
    "formula": null,
    "limitation": "需要 LPV/ATC/IC/Purchase、客服或可用性证据；不能从心理框架单独判断。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 有客户语言与创意原材料
- 团队愿意把心理标签当假设而非确定分类
- 不同状态可以共存和往返

## 适用情况

- 需要完善消息匹配和创意 Brief 的 DTC 团队

## 不适用情况

- 要根据个人行为强行断定心理状态
- 没有客户证据却希望由模型编造情绪与生活事件

## 风险

- 标签主观且重复归类
- 把折扣效果差一律归咎于上游创意
- 忽略产品、价格或实际支付故障

## 反例

- 设计反例：支付系统出错导致低购买，补触发创意不能修复技术故障。
- 设计反例：紧急补货客户可直接购买，不必被强制经历四类广告。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-01",
    "difference": "画像/动机/格式矩阵与心理任务是不同组织维度，可以交叉标注。"
  },
  {
    "method_id": "MC-STR-05",
    "difference": "投递结构合并不代表创意心理任务也被合并。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "作者自有心理框架和匿名经验；没有公开实验、样本或确定因果。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 112,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 175,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 252,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 315,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 361,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 424,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 440,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 472,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 424,
    "end_seconds": null
  },
  {
    "source_id": "YT-po85BKDTG-Y",
    "start_seconds": 361,
    "end_seconds": null
  }
]

## Skill 适配

心理任务作假设，须有 VOC 或产品证据。计划字段、客户授权、平台回读和作者案例分别取证。
