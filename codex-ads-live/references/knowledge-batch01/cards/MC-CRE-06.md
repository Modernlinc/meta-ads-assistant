# MikeMakesAds：从客户证据建立角度库与三类表达排期

MC-CRE-06 · 2026-11-27.1

作者或归属 MikeMakesAds。来源 YT-zv03UY4k5UE，见 [来源登记](../sources.json)。

## 原问题

依赖灵感，混淆角度、Hook、脚本和格式，导致无来源的重复创意。

## 机制与设计归属

先归纳客户问题与价值理由，再为基础角度生成利益、问题和故事三类表达，以相关性、差异与生产能力选择当期候选。

## 所需输入

- 有来源定位的评价、客服、论坛与社媒反馈
- 自身历史广告及实际表现
- 公开竞品广告与仅可观察事实
- 产品真实能力和可用证明
- 制作资源、当期预算与素材档期

## 条件化决策规则

```json
[
  {
    "condition": "客户表达包含怀疑、失败体验、价格异议或身份需求",
    "action": "先提取带来源的问题，再形成基础角度，不将原话自动视为普遍需求。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "基础角度已经明确",
    "action": "分别尝试利益、问题和故事表达，并记录是否仍是同一概念的版本。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "候选多于当期生产能力",
    "action": "比较价值主张相关性、与已有素材差异和制作可行性；来源未给权重，不伪造标准分数。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "竞品广告长期在线",
    "action": "仅标为值得研究的线索，不称已经盈利。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "拟写的 Hook 含产品功效或客户经历",
    "action": "必须有可核证据；不能照搬假设示例为事实广告。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 将客户证据登记来源、语境和代表性限制。
- 把同类问题聚合成基础角度，并区分问题、利益和证明。
- 给每角度生成三类表达，保留父概念和变体 ID。
- 检查真实性、重复和制作依赖。
- 按相关性、差异、可制作性说明排序理由，选择可负担的一批。
- 上新后回填实际表现与失败原因，使角度库保留历史而不是无限追加。

## 预期输出

- 证据链接完整的基础角度库
- 父概念—三类表达—素材版本关系
- 制作优先级及排期理由
- 测试后可回填的学习记录

## 指标与计算

```json
[
  {
    "name": "候选表达数",
    "formula": "基础角度数 × 表达方式数",
    "limitation": "10×3=30 表达候选，不自动等于三十个独立需求或已验证赢家。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "优先级",
    "formula": null,
    "limitation": "来源仅给三个维度，未给量表和权重；若采用评分，应标为 MODERNLINC 设计。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 客户表达与目标产品相关
- 可分清事实证据与创意假设
- 团队能记录概念层次与实际结果

## 适用情况

- 需要稳定创意生产输入与可追溯角度积累的团队

## 不适用情况

- 没有产品证明却要直接生成保证结果的文案
- 把竞品公开广告当可复制成功案例

## 风险

- 样本代表性不足
- 将三种措辞误当三种独立人群
- 长期在线广告被误判有效
- 生产速度压过真实性

## 反例

- 设计反例：长期在线的竞品广告可能预算极低或处于测试，不能认证赚钱。
- 设计反例：十个基础概念各换三句 Hook，未必增加三十种独立受众需求。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-02",
    "difference": "此法生成表达候选；Sam 的方法随后比较画像方向的规模和经济性。"
  },
  {
    "method_id": "MC-CRE-04",
    "difference": "近似表达仍可作为细节测试，但不能同时宣称它们必然获得不同人群。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "可执行生产流程来自字幕；算法受众划分、角度数量与一小时产出没有独立验证。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 124,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 187,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 266,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 348,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 379,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 443,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 524,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 266,
    "end_seconds": null
  },
  {
    "source_id": "YT-zv03UY4k5UE",
    "start_seconds": 524,
    "end_seconds": null
  }
]

## Skill 适配

客户事实组成自有角度，不借竞品经济或复制文案。计划字段、客户授权、平台回读和作者案例分别取证。
