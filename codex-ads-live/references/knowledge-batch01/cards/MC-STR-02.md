# William Kast：主 Broad CBO 与业务条件驱动的例外分支

MC-STR-02 · 2026-11-27.1

作者或归属 William Kast。来源 YT-ATJcUcaAHIg，见 [来源登记](../sources.json)。

## 原问题

单 CBO 被误读为任何品牌只能一个 Campaign，忽略促销、目录、长周期再营销和冷启动例外。

## 机制与设计归属

将相同业务目标的概念探索与获胜素材放在主 CBO 中，避免过多人工分配，同时为商品经济模型、短促销和特殊受众需求保留独立单元。

## 所需输入

- 商品经济模型、转化目标及独立控制要求
- 目标 CPA/ROAS 和统一观察窗口
- 概念、欲望、画像及对应素材版本
- SKU 目录、促销日历、客户周期
- 受众分群、频次和现有新客/回头客数据

## 条件化决策规则

```json
[
  {
    "condition": "目标与商品经济模型相近",
    "action": "建立主 Broad CBO，概念测试 Ad Set 与获胜广告 Ad Set 同处该主 Campaign。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "某测试花费约为目标 CPA 二至三倍且仍未达到目标",
    "action": "按作者启发式考虑停测，注明样本不足和延迟归因，不把倍数当统计定论。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "多 SKU 或短促销需要独立预算/目标",
    "action": "评估目录或促销专用 Campaign，避免短期活动与常青获客混为同一基线。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "购买周期较长且已有证据显示再触达不足",
    "action": "另测再营销；不能仅凭频次 1.5 自动开新 Campaign。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "新品牌、很窄的品类或历史数据稀少",
    "action": "可测试宽兴趣堆叠与 Broad，保留后续基于实际表现转移的条件。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 执行步骤

- 按业务目标和商品经济模型分组。
- 每个概念建立假设和少量可比版本，保留相同或不同格式的说明。
- 主 CBO 下记录测试与获胜广告池，保持素材来源关联。
- 用花费、目标事件及观察期评价，既看分配也看购买与经营结果。
- 检查目录、促销、长周期再营销、新账户兴趣四种例外是否成立。
- 输出主方案、例外方案与需取得的账户资格，不将“单 CBO”写成硬限制。

## 预期输出

- 主 Campaign 与例外分支图
- 概念—Ad Set—广告的草案映射
- 资格、预算和观察窗口待核清单

## 指标与计算

```json
[
  {
    "name": "测试花费相对目标",
    "formula": "测试花费 / 目标 CPA",
    "limitation": "二至三倍是作者止损启发式；归因延迟和订单金额分布可能改变判断。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "频次",
    "formula": "展示量 / 触达人数",
    "limitation": "需同窗口、同层级；低频不单独证明缺少再营销。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 有相同目标下可比的素材与商品
- 允许系统在 Ad Set 间自适应分配
- 短促销与常青数据能够分开解释

## 适用情况

- 需要集中结构但有目录/促销/长销售周期等明确例外的品牌

## 不适用情况

- 每条素材必须固定预算的实验
- 商品经济模型差异大却无法分组
- 没有事件或时间口径的账户

## 风险

- 预算流向被误当因果赢家
- 迁移获胜素材后投递改变
- 过多例外逐渐恢复碎片化

## 反例

- 作者自己保留窄品类兴趣方案，因此不能以该视频否定所有兴趣投放。
- 设计反例：短促销高 ROAS 来自暖受众，不能据此替换全年获客策略。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-STR-03",
    "difference": "Kast 让 CBO 决定测试分配，Mason 用独立 Ad Set 预算。"
  },
  {
    "method_id": "MC-STR-05",
    "difference": "Kast 保留条件性独立再营销，老吴更倾向合并后用分群报表观察。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "作者教程与界面描述；未取得实验随机化、原始表格或账户资格。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 49,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 129,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 195,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 324,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 386,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 483,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 515,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 594,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 515,
    "end_seconds": null
  },
  {
    "source_id": "YT-ATJcUcaAHIg",
    "start_seconds": 594,
    "end_seconds": null
  }
]

## Skill 适配

同目标可共享系列预算，利润或促销目标不同则独立观察。计划字段、客户授权、平台回读和作者案例分别取证。
