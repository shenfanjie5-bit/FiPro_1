# BACKLOG（直接可拆 Epic）

下面是可以直接进入 Jira/Linear 的 Epic 列表。

## Epic 1：策略配置中心（版本化）

- Story：创建策略/版本（权重、风控）。
- Story：版本锁定与回放（report 绑定版本）。
- 验收：任一报告可追溯到策略版本与权重。

## Epic 2：数据层（ETL + 快照）

- Story：接入行情/财务/资金/热度/宏观（先最小集合）。
- Story：清洗标准化 + 缺失标记。
- Story：生成 Snapshot JSON。
- 验收：Snapshot 可复用、可缓存、可引用。

## Epic 3：LLM 工作流（工具化）

- Story：工具层（skills）实现 + 单元测试。
- Story：LLM 结构化输出 + schema 校验。
- Story：风控门禁（risk_gate）。
- 验收：100% 输出 schema 通过；门禁生效。

## Epic 4：UI Dashboard（最小闭环）

- Story：标的页：今日建议、分段指数、重点关注项。
- Story：证据链/引用展示。
- Story：决策日志与回放。
- 验收：能看“今天为什么这么建议”。

## Epic 5：记忆与重点标的增强

- Story：写入记忆（报告摘要+标签）。
- Story：检索召回（向量+关键词）。
- Story：tier 机制与预算。
- 验收：重点标的比普通标的检索更深、刷新更勤。

## Epic 6：产业链图谱（骨架先行）

- Story：Neo4j 图结构与导入。
- Story：图谱查询 API + 前端可视化。
- Story：事件/商品 -> 影响路径。
- 验收：点击事件能高亮路径并联动标的。

## Epic 7：多模型与 Shadow

- Story：模型路由器（按任务/成本/延迟）。
- Story：Shadow 运行与对比面板。
- Story：降级策略（主模型不可用）。
- 验收：可无缝替换模型而不改业务代码。

## Epic 8：评估与可观测

- Story：trace（工具调用、成本、延迟）。
- Story：离线回测与在线反馈。
- Story：幻觉/缺证据监控。
- 验收：可定位“坏报告”的原因与来源。
