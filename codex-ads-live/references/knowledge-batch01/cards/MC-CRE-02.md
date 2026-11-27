# Sam Piliero：画像探索、有效方向迭代与自然内容转付费候选

MC-CRE-02 · 2026-11-27.1

作者或归属 Sam Piliero（对 Hormozi 公开片段的归纳）。来源 YT-D-jpozVTfxQ，见 [来源登记](../sources.json)。

## 原问题

只选择最高 ROAS 的小体量广告、只做表面迭代，或把自然内容爆款直接当付费赢家。

## 机制与设计归属

先探索不同画像的购买问题，再深入有效方向；以每个方向的规模和经济性共同决策，并将自然内容表现作为额外创意来源。

## 所需输入

- 客户画像、问题、产品机制及证明
- 概念/角度/素材版本的明确标签
- 分方向花费、收入、目标效率与利润
- 自然内容互动、受众与转化线索
- 测试预算上限和实际可用控制层级

## 条件化决策规则

```json
[
  {
    "condition": "还没有可重复的方向",
    "action": "横向探索不同画像与购买问题。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "某方向已表现出可重复业务结果",
    "action": "深入该方向的人物、场景、证明和表达，不仅换一句标题。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "方向 ROAS 较低但规模更大且超过业务底线",
    "action": "保留为组合候选，同时核对边际利润与受众重合，不能只按最高 ROAS 排序。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "自然内容互动较好",
    "action": "加入付费测试候选并限制风险；自然热度不等于购买意图或盈利。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "作者认为不同画像完全独立",
    "action": "保留其说法，同时将真实重合和增量标记待检验，不把互斥当设计前提。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 用客户证据定义问题、机制和画像，再标注表达模板。
- 建立横向方向及每个方向的深入变体，区分概念和版本。
- 为每方向保存独立观察口径和目标效率。
- 将既有广告、竞品线索和自然内容候选放入同一证据台账，但标明验证程度。
- 按实际预算控制能力形成有限测试提案，不臆造广告层最小花费设置。
- 回看规模、利润和重合，再确定方向保留或缩减。

## 预期输出

- 画像及机制驱动的概念树
- 按规模与经济性比较的方向组合
- 自然内容转付费候选及验证计划

## 指标与计算

```json
[
  {
    "name": "方向层 ROAS",
    "formula": "同窗口方向归因收入 / 方向花费",
    "limitation": "不是增量利润；需要去重、退款和商品毛利解释。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "规模/效率示例",
    "formula": null,
    "limitation": "100 美元@5x、10,000 美元@2x 等均为作者假设，不作为账户实绩或推荐目标。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 可把广告与方向及商品经济性关联
- 有明确业务效率底线
- 自然内容与目标客户存在相关性

## 适用情况

- 已经找到部分方向但扩量受限的品牌
- 有自然内容积累并愿意做付费验证的团队

## 不适用情况

- 只凭自然播放量要求自动大量加预算
- 没有贡献利润资料却要求择最大花费方案

## 风险

- 把画像假设当已识别用户身份
- 同一客户跨角度被多次归因
- 平均 ROAS 掩盖边际亏损
- 自然流量受众与付费目标不一致

## 反例

- 设计反例：自然视频获得大量娱乐互动却无购买，不能成为获胜广告。
- 设计反例：高花费角度平均 ROAS 达标，但退款后亏损，应回到经济约束。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-06",
    "difference": "Sam 讨论画像/概念的扩量组合；Mike 主要解决角度库与表达生产，两个“角度”口径需标明。"
  },
  {
    "method_id": "MC-STR-01",
    "difference": "深入有效方向可保留变体，不能将所有近似素材视为无价值。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "公开二次讲解和假设数值；不能扩展成 Hormozi 的全部方法或已核实认可。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 175,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 223,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 320,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 368,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 417,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 481,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 417,
    "end_seconds": null
  },
  {
    "source_id": "YT-D-jpozVTfxQ",
    "start_seconds": 320,
    "end_seconds": null
  }
]

## Skill 适配

探索与有效迭代分开；自然效果仅为候选。计划字段、客户授权、平台回读和作者案例分别取证。
