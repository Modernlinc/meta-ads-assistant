# 咔咔的老吴：合并投放、分群观察与三类业务拆分例外

MC-STR-05 · 2026-11-27.1

作者或归属 咔咔的老吴。来源 YT-mOdMt6SjMQw，见 [来源登记](../sources.json)。

## 原问题

以 Campaign 名称假定获客与再营销完全隔离，同时因过多组分散低量转化信号。

## 机制与设计归属

作者在其 Advantage+ Sales 观察中认为冷暖投递难以按命名隔离，转向集中预算，再用受众分群报表观察新客/互动/老客贡献。

## 所需输入

- 当前目标、Campaign 类型与实际受众控制
- 客户/互动受众的来源和定义
- 分群报表及各分群花费/购买
- 产品线、库存、利润和独立预算要求
- 近期转化量与归因窗口

## 条件化决策规则

```json
[
  {
    "condition": "相近产品和目标且没有独立预算约束",
    "action": "比较合并 Campaign/Ad Set 的方案，保留原结构与分群基线。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "需要区分新客与已接触客户",
    "action": "先核实并设置受众分群定义，再观察报表；报表分类不作为强制投递比例控制。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "存在不同转化目标、不同产品受众或独立库存/预算控制",
    "action": "按作者三类例外拆分，不能因“集中信号”而忽略经营约束。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "只有再营销 Campaign 名称或扩展受众建议",
    "action": "不能断言只触达暖受众；应检查实际设置和可得报表。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 盘点命名与真实目标/受众设置是否对应。
- 确认互动与老客数据来源、定义窗口及覆盖缺失。
- 记录合并前新客、互动用户、老客的可得花费和结果。
- 依三类拆分条件形成主方案与例外，注明库存控制。
- 解释每周 70 次与十组各七次只是示例，不当作平台学习门槛。
- 在批准的比较期观察整体经营结果；不把再营销全部无效写成知识库规则。

## 预期输出

- 受众定义与报表字段映射
- 合并/保留/拆分结构建议
- 库存与预算控制约束清单

## 指标与计算

```json
[
  {
    "name": "各分群花费占比",
    "formula": "segment_spend / total_spend",
    "limitation": "分群可能有未知/覆盖差异，先核实定义；示例 263 未知币种不作为客户基准。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "转化集中示例",
    "formula": "70 / 10 = 7",
    "limitation": "仅为平均分配算术例子，实际分配和效果不由该算式保证。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 分群数据可用且定义一致
- 有集中信号的现实需求
- 业务目标或库存约束可清晰描述

## 适用情况

- 考虑从冷暖多 Campaign 转向集中架构的 DTC 或单服务账户

## 不适用情况

- 明确需要独立商品预算
- 不同转化目标混投
- 受众数据缺失却希望判断新客增量

## 风险

- 报表分类与投递控制混淆
- 误把一个账户的受众扩展泛化到所有配置
- 合并后掩盖产品库存或利润差异

## 反例

- 作者本人列出缺货和独立预算的拆分例外。
- MC-STR-02 保留长周期品牌的独立再营销测试，因此本观点不能排除该替代路径。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-STR-02",
    "difference": "对独立再营销的预期不同，需要按销售周期与实际投递比较。"
  },
  {
    "method_id": "MC-STR-04",
    "difference": "Mason 按流量用途拆 Campaign，不仅按目标和产品拆分。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "中文作者的账户观察与策略变化；无本轮画面报告、原图或独立账户数据。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 93,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 433,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 513,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 607,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 686,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 766,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 798,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 877,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 1027,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 433,
    "end_seconds": null
  },
  {
    "source_id": "YT-mOdMt6SjMQw",
    "start_seconds": 1027,
    "end_seconds": null
  }
]

## Skill 适配

合并与分群观察，保留业务必要时拆分。计划字段、客户授权、平台回读和作者案例分别取证。
