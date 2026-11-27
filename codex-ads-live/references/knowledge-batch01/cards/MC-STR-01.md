# V8：单一宽泛投放单元与批量多样创意轮换

MC-STR-01 · 2026-11-27.1

作者或归属 V8 Media 访谈嘉宾及 Jandre de Beer。来源 YT-8Lipy8KEE0Q，见 [来源登记](../sources.json)。

## 原问题

多个冷暖漏斗 Campaign 分散预算，创意生产又集中在同一素材的微小变化。

## 机制与设计归属

作者假设集中预算和转化信号，并通过不同创意覆盖不同需求，由系统决定分配；这不是对 Meta 内部算法的确认。

## 所需输入

- 业务目标与商品/服务边界
- 币种、日预算、购买量及目标 CPA/经营回报
- 创意列表、概念与格式标签、生产节奏
- 广告粒度花费、CTR、购买及窗口
- 现有客户定义与可得受众分群

## 条件化决策规则

```json
[
  {
    "condition": "商品目标相近、可把信号集中到同一投放单元",
    "action": "比较一个 Campaign、一个 Ad Set 的宽泛方案，并将不同创意放在广告层。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "创意没有消耗或持续低表现",
    "action": "作者倾向每日/隔日停用并换新；必须同时记录其“无消耗即无匹配人群”的未经验证推断。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "预算、购买量或制作能力不足以支撑其示例数量",
    "action": "缩小候选方案并与 ABO 保障探索或小批持续测试作比较，不强制 10–50 条。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "condition": "库存、产品经济模型或独立预算约束不兼容",
    "action": "转入结构拆分比较，不按本方案自动合并。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 登记现有结构及业务约束，保留原结构基线。
- 将每条素材标为独立概念或变体，列出人物、场景、动机和格式差异。
- 生成单 Campaign/单 Ad Set 方案，注明预算、版位与受众建议须账户核验。
- 选择企业可维持的批次节奏；保留作者每周与每两三周两种说法，不伪造统一周期。
- 逐广告观察消耗和业务结果；将作者停用建议与替代流派及证据不足提示并列输出。
- 比较整体新客、利润和信号变化，再决定是否延续，不用匿名案例涨幅预测客户结果。

## 预期输出

- 结构方案与原结构对照
- 创意轮换计划及资源预算
- 低消耗广告清单和可替代测试路径
- 需要账户核验的功能/受众设置

## 指标与计算

```json
[
  {
    "name": "目标 CPA",
    "formula": "广告花费 / 目标事件数",
    "limitation": "事件、币种、归因与日期一致，零事件时不除零；本源未提供统一阈值。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "匿名案例比较",
    "formula": null,
    "limitation": "R400→R250 与 ROAS 5.78→11.21 仅是作者自述，不作为预测公式。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 目标事件可用且商品经济模型可比
- 有持续创意生产能力
- 用户愿意让系统自适应分配预算

## 适用情况

- 单产品或同类服务、需要减少结构复杂度的方案比较

## 不适用情况

- 须分开控制产品预算或库存
- 需要保证每个实验素材获得既定探索花费
- 仅有少量购买却希望据每日结果作确定判断

## 风险

- 低消耗误判为低质量
- 批量上新造成投递变化难以归因
- 集中预算可能忽略较小但有价值的细分需求

## 反例

- MC-CRE-03 的作者指出，未获得足够投递本身不足以判断创意好坏。
- 设计反例：两个产品毛利与库存相反，混投可能偏向低贡献商品。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-03",
    "difference": "该方法主动分配单广告测试预算；V8 倾向信任现有分配并停低消耗。"
  },
  {
    "method_id": "MC-CRE-07",
    "difference": "Motion 保留中等消耗广告作为组合稳定者。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "单一访谈的操作建议和匿名前后对比；没有画面或原始经营数据。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 126,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 397,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 477,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 495,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 654,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 701,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 831,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 961,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 495,
    "end_seconds": null
  },
  {
    "source_id": "YT-8Lipy8KEE0Q",
    "start_seconds": 397,
    "end_seconds": null
  }
]

## Skill 适配

集中预算与未探索素材的观察，不自动暂停。计划字段、客户授权、平台回读和作者案例分别取证。
