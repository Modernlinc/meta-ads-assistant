# 按业务动作核查浏览器事件，并独立保留服务器验收状态

MC-TRK-01 · 2026-11-27.1

作者或归属 Jamie Stenton（浏览器测试流程）；MODERNLINC补充独立服务器验收边界。来源 YT-rRaXstLeHjY，OFF-20261127-01，OFF-20261127-03，OFF-20261127-04，见 [来源登记](../sources.json)。

## 原问题

只确认Pixel已安装，却没有检查事件是否与实际业务动作匹配；容易漏报、错报或把局部测试当全链路验收。

## 机制与设计归属

```json
{
  "creator_method": "作者建议在正确Pixel的Test Events中操作网站并核对回报事件。",
  "official_mechanism": "Meta分别提供服务器收件与去重验证；双路接收及去重后的事件不是同一指标。",
  "modernlinc_design": "将browser、server、dedup、Purchase完成范围分栏，保留未测试项。"
}
```

## 所需输入

- 客户已确认的网站、Pixel/数据集与集成方式
- 业务动作—预期事件映射，包含表单、加购、结账和购买的实际语义
- 每次动作时间、事件名、来源及可取得的脱敏事件ID和平台回执
- 允许测试的路线；涉及真实下单、付款或事件发送时的独立批准

## 条件化决策规则

```json
[
  {
    "when": "只取得浏览器PageView/ATC/IC证据",
    "then": "仅更新这些浏览器检查项；Purchase与server保持未验证。",
    "prohibited": "把整条CAPI链路标为通过。"
  },
  {
    "when": "同一个联系表单同时产生不符合业务含义的Purchase",
    "then": "列出错误映射候选及证据，请人工核对配置。",
    "prohibited": "仅凭标题或经验判断自动删改触发器。"
  },
  {
    "when": "同次行为有browser和server两条",
    "then": "转入事件ID、事件名、数据集与平台去重回执检查。",
    "prohibited": "直接按两条接收记录判定两次购买。"
  },
  {
    "when": "付款路线未实际完成或没有Purchase证据",
    "then": "报告覆盖不足及最小补证动作。",
    "prohibited": "把计划检查的步骤写成已执行。"
  }
]
```

## 执行步骤

- 确认业务动作映射和测试范围，先处理具名资产与集成路线。
- 在得到授权的条件下记录各网站动作及其浏览器事件；本研究仅制定检查，不执行。
- 逐项比较实际动作、事件名、来源和顺序，记录缺失与异常候选。
- 将浏览器结果与服务器收件、事件新鲜度、去重检查分别列账。
- 形成已核/未测/待核报告；任何修改进入既有批准与回读流程。

## 预期输出

- 动作—事件核查表
- browser/server/Purchase/去重独立状态
- 异常候选及缺证据项
- 经批准后才执行的补证或修复提案

## 指标与计算

```json
{
  "recorded_observations": [
    "实际动作数、各来源接收事件、事件名、时间和平台处理状态"
  ],
  "formula": null,
  "not_interchangeable": [
    "browser触发正常",
    "server收件正常",
    "去重通过",
    "EMQ分数",
    "广告归因购买"
  ],
  "note": "不设置通用成功率或分数阈值；多条接收记录须依语义与处理状态判断。"
}
```

## 前提假设

- 客户能够提供对应资产的合法读取证据
- 预期事件映射已由业务负责人确认

## 适用情况

- 新接入上线前核查
- 结账或追踪变更后核查
- 疑似错报/漏报且能取得具名证据的账户

## 不适用情况

- 仅凭视频字幕远程判定某客户配置
- 以浏览器测试代替完整CAPI验收
- 未批准的真实订单或生产事件测试

## 风险

- 未区分事件来源造成假重复告警
- 把未完成购买写为Purchase已验收
- test_event_code被误当成隔离沙盒
- 把作者经验比例当行业概率

## 反例

- 浏览器正常而服务器凭据、路由或发送任务失效。
- 同一事件被browser与server各发送一次并正确去重，接收数多于业务次数是正常现象。

## 与其他方法的分歧

```json
[
  {
    "source_id": "YT-rRaXstLeHjY",
    "topic": "浏览器正常即可推断CAPI正常",
    "treatment": "保留作者经验描述，不作为产品通过条件。"
  }
]
```

## 证据等级

```json
{
  "creator": "完整阅读返回字幕；无画面核查",
  "official": "去重、收件与测试机制有公开正文",
  "customer": "无客户实证"
}
```

## 原始来源定位

[
  {
    "source_id": "YT-rRaXstLeHjY",
    "start_seconds": 83,
    "end_seconds": null
  },
  {
    "source_id": "YT-rRaXstLeHjY",
    "start_seconds": 115,
    "end_seconds": null
  },
  {
    "source_id": "OFF-20261127-04"
  },
  {
    "source_id": "YT-rRaXstLeHjY",
    "start_seconds": 33,
    "end_seconds": null
  },
  {
    "source_id": "OFF-20261127-01"
  },
  {
    "source_id": "OFF-20261127-03"
  }
]

## Skill 适配

浏览器、服务器、事件去重及购买定义分别保留。计划字段、客户授权、平台回读和作者案例分别取证。
