# Dara Denney：较小月预算的每周多样创意 CBO 批次

MC-CRE-08 · 2026-11-27.1

作者或归属 Dara Denney。来源 YT-PVoC7H44A5w，见 [来源登记](../sources.json)。

## 原问题

每组只有四至六条近似素材，概念覆盖不足；又把作者所谓小预算误套到极低花费账户。

## 机制与设计归属

把探索重点放在一批差异明显的创意，让 CBO 在这些候选中分配，按周保持新批次；源中的“小预算”有特定月花费背景。

## 所需输入

- 月广告预算及币种
- 预期 CPA/购买量与测试风险上限
- 每周可制作的概念与版本数
- 已有 Campaign/Ad Set 结构和批次
- 业务目标和可比素材结果

## 条件化决策规则

```json
[
  {
    "condition": "预算及资源接近作者所述月 5k–30k 区间",
    "action": "评估每周一个多样创意测试 Ad Set 的 CBO 路线；先核币种与购买量。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "素材主要是同概念不同 Hook",
    "action": "补充不同动机或表达概念，而不是只增加变体数量。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "已有十条左右不同内容且生产可持续",
    "action": "按作者示例形成批次；静态组十五条是例子而非硬门槛。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "月预算远低于示例或购买量过少",
    "action": "缩小批次或考虑其他路线，不强制每周十条。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "condition": "需要晋级或停测决策",
    "action": "注明本短视频没有给完整规则，选择补充方法并保留不同来源，不能假称 Dara 已规定。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 明确预算币种、购买目标和制作能力。
- 标记每条素材属于独立概念或变体，允许少量变体但避免全组同质。
- 提出一个主 CBO 下按周新建测试 Ad Set 的候选计划。
- 估计全部活跃批次的风险支出，而不只看新批次。
- 记录花费、转化与未获投递，按另行定义的观察/停测规则复盘。
- 可与 Ben 的主体复用细节测试或 Nikhil 的独立预算测试比较，不把本路线设成全局默认。

## 预期输出

- 每周创意批次计划
- 概念与变体覆盖说明
- 预算适配与缺少停测规则提示
- 与其他测试流派的比较

## 指标与计算

```json
[
  {
    "name": "来源预算范围",
    "formula": null,
    "limitation": "月 5k–30k 的币种字幕未明确；不得默认为人民币或美元。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "创意数量",
    "formula": null,
    "limitation": "作者建议至少十条，静态示例十五条；数量不是充分学习或达标保证。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "活跃批次成本",
    "formula": "各活跃批次实际花费合计",
    "limitation": "CBO 无固定每组额度时不可把总预算均分后当实际花费。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 前提假设

- 业务可持续投入足够差异化创意
- 接受 CBO 不均等分配
- 可补充明确的退出与观察规则

## 适用情况

- 符合预算和生产条件、侧重创意探索的较小 DTC 品牌

## 不适用情况

- 极低月花费却被标题误导
- 需要保证每条广告得到固定测试预算
- 没有素材或结果台账

## 风险

- 批次数量累计导致结构扩张
- 低投递素材被误判
- 按数量指标牺牲证据与生产质量
- CBO 被误等同所有 Advantage+ 配置

## 反例

- 设计反例：月预算 300 且目标 CPA 50，不应只凭 small budget 标题每周强推十条测试。
- MC-CRE-04 允许较少主体配 Hook 变体，是资源有限团队的另一方案。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-03",
    "difference": "同组多创意 CBO 与一广告一组 ABO 的控制取舍不同。"
  },
  {
    "method_id": "MC-CRE-04",
    "difference": "Dara 强调批次概念差异，Ben 也支持少量主体的细节实验。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "短视频完整字幕方法，具体币种、停测规则和实际效果未给；未调用长视频分析。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 16,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 50,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 68,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 83,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 116,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 133,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 116,
    "end_seconds": null
  },
  {
    "source_id": "YT-PVoC7H44A5w",
    "start_seconds": 133,
    "end_seconds": null
  }
]

## Skill 适配

周批次受花费和多样性约束，保留独立观察备选。计划字段、客户授权、平台回读和作者案例分别取证。
