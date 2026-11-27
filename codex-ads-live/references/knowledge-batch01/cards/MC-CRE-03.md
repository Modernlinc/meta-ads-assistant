# Nikhil：每广告独立 ABO 的创意探索与漏斗指标初筛

MC-CRE-03 · 2026-11-27.1

作者或归属 D2C Decoded By Nikhil。来源 YT-Qfv74ZtLa80，见 [来源登记](../sources.json)。

## 原问题

在单广告组中多数广告没有消耗，却直接被归类为失败创意。

## 机制与设计归属

以一广告一 Ad Set 的预算安排给创意探索机会，再用注意力、点击和落地页/加购指标识别值得继续验证的素材。预算独立不保证随机或等额投递。

## 所需输入

- 要检验的 Hook/格式/Offer/角度假设
- AOV、预期 CPP/目标 CPA、币种和总可承受预算
- 待测素材与落地页
- 三秒播放、展示、点击、LPV、ATC、购买及其口径
- 数据延迟与最小可用观察窗口

## 条件化决策规则

```json
[
  {
    "condition": "素材尚无充分投递",
    "action": "先区分没有机会与实际低表现，不直接判失败。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "选择本流派并能负担独立预算",
    "action": "一 Campaign、多 Ad Set、每组一条广告；数量按素材数，不固定十组。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "设置测试预算",
    "action": "作者提出每组约 30% AOV，同时要求足够发生约二至三次购买；两条件不必然一致，应展示冲突而非静默套公式。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "CPM 偏高但 CTR 较高、CPC 较低",
    "action": "不因 CPM 单一指标淘汰，进一步看 LPV、ATC 与购买。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "仅前链路指标较好",
    "action": "记录为继续验证候选；在扩量前补购买与经营目标，避免把便宜点击当获胜。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 写清测试变量，保留其他需要固定的条件。
- 计算并展示作者 AOV 启发式与预期购买机会，检查总预算是否可承担。
- 生成一广告一组的 Broad / ABO 草案，广告与组名称关联。
- 替换创意时记录是否同时改文案或 URL；多个变量变化标为组合测试。
- 计算注意力、点击、落地页及加购指标并检查分母。
- 选继续验证或进入 CBO/独立扩量的候选，补足购买证据与明确的风险预算。

## 预期输出

- 广告—Ad Set 一对一测试计划
- 预算条件冲突与总成本提示
- 漏斗初筛表、缺项及后续购买验证计划

## 指标与计算

```json
[
  {
    "name": "作者预算启发式",
    "formula": "daily_adset_budget ≈ 0.30 × AOV",
    "limitation": "不是利润公式；币种一致，和可负担 CPP 及购买样本需另核。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "预期购买机会（设计估算）",
    "formula": "预算 / 预期 CPP",
    "limitation": "估算不是保证，也不是统计样本；观察窗口必须定义。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "三秒停留比率",
    "formula": "3_second_video_plays / impressions × 100%",
    "limitation": "采用来源定义；静态图不适用，展示为零保留缺失。",
    "origin": "SOURCE_VIEW"
  },
  {
    "name": "ATC 率",
    "formula": null,
    "limitation": "来源未说分母；不得默认使用点击、LPV 或会话中的任一种。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 有足够总预算保证每组得到可解释数据
- 各广告的目标与受众条件可比
- 了解初筛与购买验证不同

## 适用情况

- 需要探索低消耗素材的账户
- 希望更清晰地比较有限数量广告的团队

## 不适用情况

- 预算不足以负担所有组
- 没有购买事件却准备直接大规模扩量
- 需要正式随机化因果实验但仅具普通 ABO 结构

## 风险

- 数据碎片化和相互竞争
- 30% AOV 在高 CPA/低毛利条件下不可用
- 转写金额异常导致错误预算
- 好看前链路指标不转化为利润

## 反例

- 设计反例：AOV 1,000、CPP 600 时，30% AOV=300 不足以支持作者说的二至三次购买，应暴露冲突。
- MC-CRE-08 提供 CBO 批次自适应分配作为另一资源选择。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-08",
    "difference": "Dara 以每周多创意 Ad Set 交给 CBO；Nikhil 给每条广告独立预算。"
  },
  {
    "method_id": "MC-STR-01",
    "difference": "对低消耗素材的解释相反。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "印地语字幕完整阅读；方法属于实践者实验预算设计，卢比数字歧义及业务胜者门槛仍待核。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 17,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 112,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 144,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 242,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 274,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 291,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 339,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 372,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 387,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 274,
    "end_seconds": null
  },
  {
    "source_id": "YT-Qfv74ZtLa80",
    "start_seconds": 356,
    "end_seconds": null
  }
]

## Skill 适配

独立组预算可编译；普通 ABO 不保证等分或随机。计划字段、客户授权、平台回读和作者案例分别取证。
