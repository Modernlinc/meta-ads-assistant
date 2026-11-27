# Ben Heath：把概念探索与相似素材的细节实验分开

MC-CRE-04 · 2026-11-27.1

作者或归属 Ben Heath。来源 YT-onFwSud9C2Y，见 [来源登记](../sources.json)。

## 原问题

误以为相似素材不再值得测试，或在默认分配没有提供机会时仍声称比较出某个细节的效果。

## 机制与设计归属

大概念差异可作为常规探索，相同主体的 Hook、文字或颜色差异则评估专门 Creative testing；以转化量和真实目标选择窗口。

## 所需输入

- 明确是概念、Hook、文案还是视觉细节测试
- 待测主体及版本的文件/hash/变量
- 当前账户 Creative testing 可用性及其确切机制
- 目标事件、目标成本、预算、预期转化量
- 测试指标、观察期和实际结果

## 条件化决策规则

```json
[
  {
    "condition": "初期探索格式和概念差异",
    "action": "可先常规投递；若要因果比较，仍须说明实验设计。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "同一主体只改变 Hook 或细节且需要比较",
    "action": "评估广告层 Creative testing；不要擅自改用另一工具并声称等价。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "默认指标是互动成本而业务目标为购买/获客",
    "action": "保持业务目标，增加预算/窗口或接受不确定，不能用互动自动替代购买成功。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "用户无法持续生产二十条全新素材",
    "action": "选择可维持的小批制作和变体节奏，保留作者资源条件。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "工具当前资格和分组规则未知",
    "action": "停止生成已确认可执行的配置，保留计划与需核字段。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 先给测试目的分类，并将每个版本的变化记录清楚。
- 核实当前账户工具、版本数量、预算/时长及分组规则；来源中的二至五和七天仅作历史示例。
- 选择与真实业务任务一致的主要指标。
- 按预期转化量评估试验规模，不把 £25/日七天当默认方案。
- 完成后检查实际投递、事件数和结果，再决定保留哪版。
- 将选出的 Hook 版本放回概念组合继续观察，不声称一次测试结论永久有效。

## 预期输出

- 测试目的分类及变量表
- 预算/样本/窗口可行性检查
- 工具资格和功能差异清单
- 胜出、未决或重测结论模板

## 指标与计算

```json
[
  {
    "name": "业务目标成本",
    "formula": "test_spend / selected_business_events",
    "limitation": "根据线索或购买定义；不能替换成互动成本却保留原结论。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "样本是否充分",
    "formula": null,
    "limitation": "作者未给功效计算、置信水平和最低样本；不能承诺七天能判断。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 有明确单变量或组合变量定义
- 工具资格和机制可以核实
- 测试预算与实际目标事件相匹配

## 适用情况

- 已找到有效主体、希望改进 Hook 或细节的团队
- 制作资源有限但可持续迭代的品牌

## 不适用情况

- 无法核实当前工具机制却要求立即执行
- 多个业务条件同时改变却要求把变化归因于 Hook

## 风险

- 将广告层 Creative testing 混同独立 A/B Test 或单广告强制投递
- 默认软指标误导业务判断
- 低样本差异被夸大
- 跨日期把二至五/二至七版本误当同一现行规格

## 反例

- 设计反例：七天仅两次购买，不能因系统显示互动更高就判定购买赢家。
- 作者自己表示大格式探索未必用该工具，不能把它设为每次素材上新的必经流程。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-08",
    "difference": "Dara 的批次多样性解决概念探索，Ben 的同主体 Hook 实验解决细节比较。"
  },
  {
    "method_id": "MC-CRE-03",
    "difference": "手工 ABO 不自动具备相同的受众隔离或实验机制。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "完整字幕讲解与历史 UI 说法；未独立核实当前官方机制或实际完成的实验。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 174,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 190,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 253,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 284,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 445,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 574,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 668,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 795,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 284,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 668,
    "end_seconds": null
  },
  {
    "source_id": "YT-onFwSud9C2Y",
    "start_seconds": 795,
    "end_seconds": null
  }
]

## Skill 适配

拆概念与同主体钩子，薄产能保留旧主体。计划字段、客户授权、平台回读和作者案例分别取证。
