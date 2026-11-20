# 工作流与恢复

在已安装目录运行 `python3 run.py --help` 查看全部命令。下面列出各流程输入、操作和停点。首次只读接入的完整操作见[账户接通流程](CUSTOMER_SOP.md)。

## 首次接入与环境检查

实际项目目录、安装包，以及客户已确认的 App、Business Portfolio 和本次任务所需资产。

1. 记录 OS、架构、Python、时区库和包完整性。 `preflight`
2. 准备表按阶段列出缺项、本人与实施方的待办。 `prepare`、`preparation-review`
3. 新工作区只初始化一次。已存在的档案用 workspace-onboard 补入当前 App 与业务组合资料，再按广告或主页范围汇总接入证据。 `workspace-init`、`workspace-onboard`、`workspace-onboarding-report`

由客户确认的事项。客户确认实际项目路径、任务和资产归属，并在宿主中核对加载的 Skill 路径。

恢复。已有工作区先查看或补齐资料，再进入具名任务；从另一台设备继续时复查实际宿主。

## Meta 具名资产盘点

原工作区，本次广告账户或 Page 的确切编号、归属与角色证据。

1. 读取已保存映射与明确默认项。 `workspace-show`、`workspace-route`
2. 只登记被确认的新账户或主页。由客户指定本次目标，或明确将一个已映射目标设为默认。 `workspace-add`、`workspace-default`
3. 新资产先准备具名连接。具名访问核对后，可准备该广告账户的层级盘点。 `workspace-connection-prepare`、`workspace-plan`

由客户确认的事项。由客户确认资产归属、访问分配和是否设为默认；映射内部登记完成后按具名读取核实访问。

恢复。使用原工作区继续局部新增或变更流程。

## Meta App 配置与审核

客户确认的一个 App、所属 Business、所选用例以及发布、公司验证和逐权限审核的页面观察。

1. 按同一 App 列出发布、验证、审核和缺项。 `preparation-review`
2. 本人在官方页面办理用例、身份或审核，记录实际状态。 
3. 在原准备资料里更新具名 App 的观察，保留旧回执。 `preparation-review`

由客户确认的事项。公司资料、App 归属、发布、审核提交和补件由客户确认并操作。

恢复。沿已确认的同一 App 回到相应页面，更新观察时间和状态。

## 权限确认与本人授权

具名 App、主体、目标和该任务所需权限，还有 App proof 要求的本次本人核对。

1. 对照权限、具名范围和本次 App 的 proof 要求。有本地登记时先核对其声明状态。 `preparation-review`、`credential-status`
2. 本人在官方入口完成必要授权与系统凭据存放。 
3. 系统凭据用非秘密引用登记。实际 grant 留待具名端点检查。 `credential-prepare`、`credential-status`

由客户确认的事项。本人核对并决定实际授权、秘密录入与是否继续。新权限与新范围分别确认。

恢复。按同一任务检查当前权限和有效期，再进入连接验证。

## 具名连接验证

经过核对的 App、主体、单一目标、非秘密登记或本次交互凭据输入；分别选择广告账户或 Page。

1. 从当前工作区与非秘密登记准备一个具名目标。主页使用 --kind PAGE 并核对角色材料。 `workspace-connection-prepare`
2. 首次连接或添加具名资产时，按已确认的范围绑定。已有同范围绑定时继续预览与复查。 `workspace-bind`、`workspace-page-bind`
3. 绑定后复核同一具名请求、权限、凭据登记与预算。 `connection-preview`、`workspace-page-preview`
4. 经当次交互确认，保留具名结果、实际权限观察和有效期；目前产品通过替身合同测试。 `connection-check`、`workspace-refresh`、`workspace-page-refresh`
5. 按同一具名确认核对请求预留、结果和回执摘要，区分保存的失败、中断与冲突。 `connection-review`

由客户确认的事项。本人核对所选 App、资产和至多两次 GET，并在本机终端确认。

恢复。核对原 App、登记或该目标权限。仅为受影响资产重新执行，保留旧记录。

## 广告读取、盘点与对账

一个已核对的广告账户、时区、币种、目标报表类型和明确的日期与归因口径。

1. 复用已保存的报表时，路由按本次日期及报表口径核对原运行。按任务准备 Insights、对象盘点或版位拆分，核对具名范围和 GET 上限。单轮广告报告及对象盘点的分页来源默认各三页，可用 --report-pages 选择 1 至 20 页。 `workspace-route`、`workspace-plan`、`monitor-preview`
2. 启动时重查账户；记录每来源覆盖、空值、错误和历史成功。产品候选已有替身证据。 `monitor-start`
3. 复查报告及同口径 Ads Manager 导出，明确无数据、缺字段和无法对账。 `monitor-report`、`monitor-reconcile-prepare`、`monitor-reconcile`

由客户确认的事项。本人确认账户、时间范围、允许来源、请求上限；后台导出由获准操作者准备。

恢复。保持同一范围时重新确认并从第一页采集。调整页数或日期时，新建计划与运行目录。具名账户复查后可按原范围重做计划。

## Facebook Page Live 对象确认

已映射的具名 Page 和现成 Live ID；只有播放链接时先取 Video ID 线索。

1. 核对具名对象输入和现有关系证据。只有链接时列出需要补充的 Live ID。 `workspace-live-preview`
2. 固定 Page、Live 和已提供的 Video，分别显示身份核验和单轮请求预算。 `workspace-live-plan`、`page-live-contract-preview`
3. 在本人当次授权后执行一次具名对象检查，保留独立的 T1 计划与结果。 `page-live-contract-check`
4. 真实入口仍为 REAL_LIVE_CONTRACT_PENDING；替身验收覆盖对象与归属核对。 `monitor-start`

由客户确认的事项。本人确认具名 Page、现成直播、允许查询范围和独立 Page 凭据。

恢复。补齐 Live ID 或新证据后重新预览。Page 权限拒绝由本人确认具名授权，复查该 Page 后生成新计划。可独立运行固定合成演练。

## 直播评论有界样本

已核对的 Page、Live、Video 与当次评论权限；明确轮数与每轮覆盖。

1. 具名目标和有界首屏采样进入计划。 `workspace-live-plan`、`page-live-contract-preview`
2. 在本人当次授权后验证同一直播的首屏有界样本，保留范围和返回类型。 `page-live-contract-check`
3. 替身采集只保存 ID、时间和样本覆盖；真实入口仍待授权与端点验收。 `monitor-start`
4. 按轮查看评论首屏的有效页、续页、空响应、错误及未采样。用脱敏诊断交接结果，转移原单轮目录后可核对请求和报告范围。 `monitor-report`、`page-live-contract-review`、`workspace-diagnostics`
5. 原前台进程退出后，可选 --from-comment-checkpoint 核对已完成首屏和原批次确认，预览固定 since。中断期间覆盖仍为未知。 `workspace-replan`、`monitor-preview`

由客户确认的事项。本人明确同意本次具名评论读取范围。

恢复。同一范围重新确认并从首页采集。若选择固定评论下界，预览接续计划后用新运行目录并取得本人当次确认。

## Page 直播帖子指标

已核对的 Page、Live、Video、具名帖子和指标授权。需要所选主页的分析任务证据。

1. 明确帖子和 Video 关系，预览具名角色核对和各指标请求。 `workspace-live-plan`、`page-live-contract-preview`
2. 本次授权及具名角色核对满足后逐项验证指标，保留字段和覆盖证据。 `page-live-contract-check`
3. 真实入口待端点验收；替身覆盖逐项指标、角色、时间和缺失。 `monitor-start`
4. 本机报告逐项显示 period、observed_at、未知 data_as_of、freshness 和覆盖。脱敏诊断显示各项响应状态及记录数。单轮回读核对预算、授权快照和指标汇总，保留各项权限或服务错误。 `monitor-report`、`page-live-contract-review`、`workspace-diagnostics`

由客户确认的事项。本人确认所选主页、具名帖子及本次指标和可选角色查询。

恢复。重新核对主页任务证据、帖子与 Live 关系，再以新批次补读可查项目。

## 广告策略与提案审阅

客户商品、市场、预算、目标、素材及权利声明，和经过准入的方法包或已保存复盘。

1. 核固定方法版本与当前业务背景。附归档复盘时可复核原两期回执和合计。 `method`、`workspace-plan`
2. 首次准备单图广告时，从所选广告账户和主页生成四份草案。补填后核对目标、缺失字段和对象依赖。网站转化的数据源、事件和真实父对象仍须取证。 `workspace-first-launch-plan`、`workspace-first-launch-check`
3. 沿原工作区和计划校验同账户图片观察、父级范围、独立审批引用与单 Ad 尝试。客户后台操作的回执按操作者声明登记，正式自动写入需配置可信宿主。 `workspace-first-launch-handoff`
4. 以同一工作区、App、业务、账户、Page 和计划导出绑定，或者核对已配置的 macOS 本机审批服务。该入口不使用 Meta 凭据。 `workspace-first-launch-host`
5. 本机核对单个步骤和原摘要。只有独立可信宿主具备具名批准及写权限时才处理暂停创建。创建结果未知只用原尝试恢复，启用需独立批准。 `workspace-first-launch-progress`
6. 按原链路准备本次 Ad 的移动动态预览。仅在可信宿主具备独立读取授权时请求一次，供客户核对。Creative 降级单独选择。 `workspace-first-launch-preview`
7. 沿原首投对象链核对具名 Ad 预览证据，准备单广告的预算及拟议启用时窗审阅。实际批准和父级状态须独立核对。 `workspace-first-launch-activation-review`
8. 以原审核版本核对一条 Ad 的范围和尝试。正式宿主有独立花费批准、具名授权和已生效的支出限制时才可提交一次。结果未知只回读原尝试。 `workspace-first-launch-activation`
9. 形成广告结构、文案、素材映射和参数 Diff，保留归档核对状态与待补证字段。 `meta-write-prepare`、`material-review`、`material-bind`、`meta-write-review`

由客户确认的事项。由客户确认业务目的、市场、预算、素材权利和草案。

恢复。从具名客户档案、材料和最新证据重新审阅，形成新摘要。

## PAUSED 创建与审批模拟

合成账户、逐对象提案、父级关系、当前版本和明确审核过的 Diff。

1. 审阅合成材料、各级对象、PAUSED 参数与审阅摘要。 `proposal-review`、`mapped-demo`
2. 按模拟动作独立确认。模拟服务发出前先记录未知并重查范围与版本。 `proposal-approve-sim`、`proposal-execute-sim`
3. 用原模拟动作和服务目录核验回执及对象，区分本次已核对、历史状态和当次待核。 `proposal-reconcile-sim`、`mapped-status`、`mapped-reconcile-sim`

由客户确认的事项。操作者在模拟门禁核对单一动作和审阅摘要。真实创建或真实批准另外验收。

恢复。按原动作、状态和账本回执核对，满足要求后再行新模拟动作。四步首投演练用 mapped-demo --first-launch；中断后用 mapped-demo --resume-first-launch 指向原目录，先核对登记的动作和既有回读，再接续下一项。

## 本地前台监控与恢复

同一工作区指定范围、原计划、时长、预算和实际用到的本机凭据登记。

1. 核来源、轮数、间隔、时长、启动预留及预算。工作区广告每轮从第一页开始，核对可选的 --pages-per-cycle。 `workspace-plan`、`workspace-live-plan`、`monitor-preview`
2. 本人确认后获得本机进程锁并重新认证。真实 Page Live 的发布门槛照常检查。 `monitor-start`、`monitor-resume`
3. 查看实际锁、保存阶段、最近成功和错误。明确暂停或停止后保留运行回执。 `monitor-status`、`monitor-health`、`monitor-pause`、`monitor-stop`、`monitor-report`
4. 用完整运行归档离线核对具名计划、请求预留、轮次覆盖和末轮摘要。 `page-live-run-review`

由客户确认的事项。本人确认每次启动或恢复的该份计划、时间与请求上限。

恢复。同一计划在原锁释放后重新确认和核验，从第一页开始新轮次。

## 广告与直播复盘

已保存的具名广告或直播状态、数据口径及可核对的直接对象关系。

1. 分别核对来源覆盖、报告时间和缺失状态。 `monitor-report`
2. 分期比较可使用两份具名采集回执，按原批次核对日期与分页。广告与 Page Live 联合复盘可用同一工作区的两份完整归档或两份快照。用原复盘回执重现原输入。 `monitor-compare`、`monitor-live-ads-review`
3. 用原首投计划的 Ad ID、具名账户与日期复核已保存的 Insights 归档。保留空结果、明确的零和正展示。携带拟议启用审阅时按该账户时区核对报告日桶能否归入时窗。 `workspace-first-launch-delivery`

由客户确认的事项。客户确认实际业务结果定义、日期窗口、促销背景与复查条件。

恢复。从原始两边回执补齐来源和对象证据，再复查可比条件。

## 故障定位与交接

当前工作区任务、明确的运行目录或脱敏分类元数据，以及首次错误和最近成功的证据。

1. 分开核对接入、授权、对象、数据覆盖与本机运行状态。 `workspace-route`、`monitor-health`、`diagnose`
2. 交接单项脱敏诊断。具名广告账户或 Page 复查通过后可沿用原查询准备新计划，保留原记录并预览当前绑定。 `workspace-diagnostics`、`workspace-replan`
3. 换机或档案丢失先预览同次备份与新目标。只有已识别的旧 v1 工作区才使用对应迁移命令。 `workspace-restore`、`workspace-migrate`

由客户确认的事项。新的凭据、平台权限、费用或资产变更由客户确认。

恢复。按相应具名工作流复查受影响范围，再由本人确认可能的读取。

## 本机退出与授权交接

原工作区、已确认的运行目录、具名登记以及客户的保留或退出决定。

1. 登记本地停止并核原进程和持锁情况。 `monitor-stop`、`monitor-status`
2. 有明确选定的模拟账本时，核对原执行锁与待处理动作。UNKNOWN 沿原账本与服务的同一动作回读。 `workspace-exit-report`、`mapped-status`、`mapped-reconcile-sim`
3. 分开核对工作区文件、已选本地登记和 Meta 授权。选择保留可移植档案时保存配套回执。 `workspace-removal-plan`、`workspace-backup`、`workspace-exit-report`
4. 只核对具名本地登记。在客户明确决定停用该登记时保存停用结果，再回读退出交接。 `credential-status`、`credential-disable`
5. 本人确认实际的本机保留、系统条目及平台撤权，并分别保留凭证。 

由客户确认的事项。真实撤权、删除和保留由本人分别决定与确认。

恢复。从原交接报告接续所缺证据，按各范围核对完成状态。
