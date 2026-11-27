# 线索到订单的队列验证

MC-COM-02 · 2026-11-27.1

作者或归属 启发：Johnny Person / Aug11；验证结构为 MODERNLINC 设计。来源 YT-OvjGs5AwzwQ，见 [来源登记](../sources.json)。

## 原问题

Lead 活动获得便宜联系人后，需要判断线索能否经后续触达形成购买，避免把不同客户群的差异归给单一流程。

## 机制与设计归属

```json
{
  "source_basis": [
    "AUG-03",
    "AUG-04"
  ],
  "design": "沿 source/cohort 保留收集、有效触达、购买、退款和贡献链路；要检验欢迎流程因果作用，应在同源合格线索中设计可比或随机分组。"
}
```

## 所需输入

```json
{
  "required": [
    "获客源、时间与线索去重标识",
    "触达/流程状态与分组规则",
    "固定观察窗口内订单、退款和成本",
    "既有客户/新线索识别"
  ],
  "optional": [
    "同源随机分组记录、邮件送达与点击"
  ],
  "missing_policy": "无法对齐订单时只评估线索采集，购买效果未验证。"
}
```

## 条件化决策规则

```json
[
  {
    "if": "CPL 低但购买未知",
    "then": "输出 LEAD_ACQUISITION_ONLY，不称购买成功。"
  },
  {
    "if": "新线索与老客名单比较",
    "then": "标记 cohort 不可比，不将差异完全归于欢迎流程。"
  },
  {
    "if": "同源随机试验已设计且功效足够",
    "then": "用预设购买/贡献结果评估；仍报告退款与窗口。"
  },
  {
    "if": "零购买且窗口、送达或样本不足",
    "then": "保留不确定性，不概括全部 Lead 策略无效。"
  }
]
```

## 执行步骤

- 定义线索来源、有效标准及观察期。
- 审查欢迎流程是否真正进入/送达。
- 按 cohort 对齐订单与退货。
- 分开描述购买结果与原因假说。
- 提出可验证的下一轮方案，邮件发送权限独立处理。

## 预期输出

- lead_to_order_cohort_report
- funnel_breakpoint_candidates
- welcome_flow_test_proposal

## 指标与计算

```json
[
  {
    "name": "CPL",
    "formula": "采集活动支出 / 去重有效线索数",
    "limits": "不等于购买 CPA；零分母返回 null。"
  },
  {
    "name": "队列购买率",
    "formula": "窗口内至少一次购买的有效线索人数 / 该队列有效线索人数",
    "limits": "需客户与订单可连接，不以邮件点击替代购买。"
  },
  {
    "name": "每线索贡献",
    "formula": "队列净收入减约定变动成本与队列获客费用后 / 有效线索人数",
    "limits": "若只核到广告花费，必须明确不是完整贡献。"
  }
]
```

## 前提假设

- 可取得授权范围内的线索与订单联结，且不把平台购买归因直接当因果分组。

## 适用情况

- Meta 获线索后由邮件/CRM 承接的电商漏斗。

## 不适用情况

- 只有 Meta 表单数量、无订单或后续触达资料的归因结论。

## 风险

- 选择偏差、老客占比、送达失败、观察期偏短、折扣差异、退款滞后。

## 反例

- 同一产品线索质量较高且后续转化充分时，Lead 也可能形成有利经营结果。
- 欢迎流程完整但 offer 或受众不匹配时，仍可能无购买。

## 与其他方法的分歧

```json
[
  {
    "other_method_id": "MC-MEA-02",
    "relationship": "漏斗关联报告不能替代随机增量试验；需要因果判断时提高证据要求。"
  }
]
```

## 证据等级

```json
{
  "source": "创始人自述失败；分组可比性不足",
  "design": "待评测的队列/漏斗诊断",
  "refs": [
    {
      "source_id": "YT-OvjGs5AwzwQ",
      "claim_refs": [
        "AUG-03",
        "AUG-04"
      ],
      "start_seconds": 1395,
      "end_seconds": null
    }
  ]
}
```

## 原始来源定位

[
  {
    "source_id": "YT-OvjGs5AwzwQ",
    "claim_refs": [
      "AUG-03",
      "AUG-04"
    ],
    "start_seconds": 1395,
    "end_seconds": null
  },
  {
    "source_id": "YT-OvjGs5AwzwQ",
    "claim_refs": [
      "AUG-03"
    ],
    "start_seconds": 1395,
    "end_seconds": null
  }
]

## Skill 适配

线索到已成交队列用自有材料；无原生 CRM 联接。计划字段、客户授权、平台回读和作者案例分别取证。
