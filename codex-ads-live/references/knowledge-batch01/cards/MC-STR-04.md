# Mason：按获胜素材用途分配 Broad、低 CPC、兴趣与价值优化扩量池

MC-STR-04 · 2026-11-27.1

作者或归属 Mason Littlejohn。来源 YT-w1JqdrQIYkA，见 [来源登记](../sources.json)。

## 原问题

只靠单一获胜池可能无法探索低点击成本、兴趣受众和高订单价值等不同机会。

## 机制与设计归属

把通过测试的素材分配到不同流量和价值目标的候选扩量池，强调规模、获客效率与整体经营回报；其增量与客户质量承诺没有被本轮验证。

## 所需输入

- MC-STR-03 或等效流程的获胜证据
- CPC、CPA、ROAS 与统一时间窗口
- AOV、退款、利润、重复购买及混合营销回报
- Broad/兴趣控制与价值目标的账户资格
- 既有 Post ID、受众重合和总预算上限

## 条件化决策规则

```json
[
  {
    "condition": "素材已有稳定目标达成记录",
    "action": "可进入主 Broad CBO 获胜池；保留 Post ID 及原证据。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "已达标素材的点击成本较低且后链路质量可接受",
    "action": "另评低 CPC 池，并用经营回报防止只买便宜点击；具体金额门槛未决，不能填 $1.50 或 $150。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "希望检验 Broad 外的兴趣机会",
    "action": "只用已证明素材测试兴趣控制；10–15m 与 10–50m 数值矛盾保持待核。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "价值事件、金额/币种及资格具备",
    "action": "评估价值优化池；作者 5,000 次购买是个人建议，不能当官方门槛。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "只见 AOV 提高但没有利润/退款/复购资料",
    "action": "不宣称获得更高质量客户或更高 LTV。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 核对每条获胜素材的来源、归因和原商品目标。
- 把四种扩量用途分别列为可选方案，不默认全部开启。
- 主 Broad 池先保留现有稳定结构与历史基线。
- 低 CPC、兴趣、价值优化池分别写清目标、约束和不可用状态。
- 汇总各池花费及去重经营结果，避免仅靠平台 ROAS 重复归因。
- 输出增减预算提案与所需回读，待资格和用户批准后才能执行。

## 预期输出

- 四类用途的条件比较表
- 需核验的目标/受众/预算配置清单
- 经营回报与受众增量的待验证计划

## 指标与计算

```json
[
  {
    "name": "CPC",
    "formula": "spend / chosen_click_metric",
    "limitation": "必须选择 link/outbound/all 等具体点击定义；本源低 CPC 金额未决。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "经营回报",
    "formula": null,
    "limitation": "作者提 blended MER 但未给完整分母；先定义总收入/媒体花费与利润口径，不能直接假定。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "高价值客户",
    "formula": null,
    "limitation": "订单价值、退款和复购需实际分组数据，不能从价值优化目标推断。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 有真正已验证素材而非单次订单假阳性
- 各池能按统一窗口比较
- 存在可用于经营核算的订单数据

## 适用情况

- 已有获胜广告和预算余量，希望比较不同扩量路线的品牌

## 不适用情况

- 没有购买金额或币种数据
- 无法核实受众控制或价值目标资格
- 总预算或人员不足以管理多个池

## 风险

- 增加 Campaign 造成数据分散
- 把便宜点击当成盈利客户
- 平台归因重叠冒充增量
- 历史截图被误作当前资格

## 反例

- 设计反例：低 CPC 广告点击多但退款后亏损，不能因流量便宜长期保留。
- 设计反例：高 AOV 由一次异常大单驱动，不足以证明 LTV 更高。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-STR-02",
    "difference": "Kast 更倾向主 CBO，兴趣例外可用于新账户；Mason 将兴趣主要留给已证明素材。"
  },
  {
    "method_id": "MC-STR-05",
    "difference": "老吴以目标/产品/预算约束作为拆分理由，Mason 按流量功能拆池。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "字幕方法已读；TOOL_VISUAL_REPORT 有数值识别和历史日期疑点，不能提升为实盘金额验证。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 432,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 496,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 542,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 607,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 624,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 686,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 733,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 812,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 911,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 943,
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
    "start_seconds": 542,
    "end_seconds": null
  },
  {
    "source_id": "YT-w1JqdrQIYkA",
    "start_seconds": 911,
    "end_seconds": null
  }
]

## Skill 适配

可记扩量候选；价值优化和分池资格逐项另核。计划字段、客户授权、平台回读和作者案例分别取证。
