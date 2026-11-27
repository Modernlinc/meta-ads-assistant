# Jared：画像 × 购买角度 × 创意类型的批次探索

MC-CRE-01 · 2026-11-27.1

作者或归属 Jared Robinson。来源 YT-_-y0NZ30VKE，见 [来源登记](../sources.json)。

## 原问题

素材大量重复，缺少角度与表达类型的覆盖，发现赢家后又停止探索。

## 机制与设计归属

围绕同一画像的不同购买动机，用多种表达类型探索，再按业务目标保留表现方向；将批次而非孤立广告作为组织单元。

## 所需输入

- 目标画像及其可追溯客户需求
- 产品真实机制、Offer、评价和异议证据
- 目标 CPA/ROAS、预算和观察期
- 创意生产能力与已有素材
- 既有客户排除条件及账户资格

## 条件化决策规则

```json
[
  {
    "condition": "已有可说明的画像与多种购买动机",
    "action": "按作者示例建立三角度 × 五类型的候选网格；规模随预算与生产能力调整。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "需要获客批次探索",
    "action": "生成 Broad CBO、每批一个 Ad Set 的候选结构，客户排除是否有效必须核验。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "约七天后有可比购买/目标结果",
    "action": "比较是否超过业务目标；可继续原 CBO 或转独立扩量池，不把七天当充分样本的证明。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "价格异议在点击后频繁出现",
    "action": "可将真实可用的付款或价格解释提前进入创意，同时检查利润与付款成本。",
    "origin": "SOURCE_VIEW"
  },
  {
    "condition": "没有相关评价或产品证明",
    "action": "保留为待证实概念，不生成虚构体验、功效或社会证明。",
    "origin": "MODERNLINC_DESIGN"
  }
]
```

## 执行步骤

- 从 VOC 写出画像、购买动机及证据，不直接照搬视频的补剂案例。
- 将每个角度配问题解决、创始人叙事、社会证明、对比、故事型视频或 Offer 等合适类型。
- 给每个概念/类型/版本独立 ID，并明确本次变化的维度。
- 按预算缩放当批数量，再生成批次 Ad Set 草案。
- 比较目标结果、实际投递和数据缺项，不把系统预算偏好当随机实验结论。
- 扩展有效组合，同时预留新角度探索；保存失败与无投递结果。

## 预期输出

- 可追溯的画像—角度—格式矩阵
- 制作 Brief 和批次清单
- 保留/迭代/待数据的方向记录
- 计划层的批次映射

## 指标与计算

```json
[
  {
    "name": "候选组合数",
    "formula": "画像数 × 每画像角度数 × 每角度类型数",
    "limitation": "作者 3×5=15 是示例；候选组合不等于已生产或已验证广告。",
    "origin": "MODERNLINC_DESIGN"
  },
  {
    "name": "CPA/ROAS 达标",
    "formula": null,
    "limitation": "以客户实际目标、归因窗口和毛利背景定义，不从作者案例复制阈值。",
    "origin": "SOURCE_VIEW"
  }
]
```

## 前提假设

- 画像与需求有材料支持
- 制作和预算能支撑多个组合
- 产品承诺可核实且落地页可承接

## 适用情况

- 需要系统化概念探索的 DTC 创意团队

## 不适用情况

- 预算不足却一次强开三十条
- 只有单一素材微调却需要纯单变量实验
- 无事实支撑的功效或虚构评价

## 风险

- CBO 内部探索不均导致未试素材被误判
- 多个变量同时改变导致因果归属弱
- 承诺、付款选项和网站不一致

## 反例

- 设计反例：不同创意都没有获得足够花费，不能宣布对应角度无需求。
- MC-CRE-04 可为相同主体的 Hook 差异提供另一测试路径，不需要每轮重新拍十五种素材。

## 与其他方法的分歧

```json
[
  {
    "method_id": "MC-CRE-03",
    "difference": "批次自适应分配与每广告独立 ABO 的探索目的不同。"
  },
  {
    "method_id": "MC-CRE-04",
    "difference": "Jared 先扩展概念和格式，Ben 保留细微表达的受控比较。"
  }
]
```

## 证据等级

```json
{
  "basis": "已审阅实践者字幕；非官方规则或独立效果验证",
  "scope": "来源提供可复用批次组织与广告结构建议，效果与增长故事未独立验证。",
  "raw_account_data_available": false,
  "independent_effect_validation": false
}
```

## 来源时间定位

```json
[
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 65,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 146,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 246,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 262,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 343,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 564,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 726,
    "end_seconds": null
  }
]
```

## 加工归属

依据来源观点作原创结构化整理；MODERNLINC 补充条件与设计建议已标注。

## 原始来源定位

[
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 146,
    "end_seconds": null
  },
  {
    "source_id": "YT-_-y0NZ30VKE",
    "start_seconds": 726,
    "end_seconds": null
  }
]

## Skill 适配

画像、角度与格式矩阵受素材产能限制。计划字段、客户授权、平台回读和作者案例分别取证。
