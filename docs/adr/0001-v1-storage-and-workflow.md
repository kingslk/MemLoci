# ADR 0001：v1 使用 PostgreSQL 关系模型承载图与 Workflow State

- 状态：accepted
- 日期：2026-08-13

## 背景

MemLoci 需要 Code Graph、Memory Graph、Evidence 追溯和可恢复的全量初始化，但 v1 不应该因为“以后可能需要”提前引入专用 Graph DB 或 Workflow 平台。

## 决策

- 使用 PostgreSQL 保存结构化实体、关系表、JSON 元数据、审计和 Job/Checkpoint。
- 使用 pgvector 作为后续可替换的向量索引边界；本地默认 Hash Provider 仍保存 provider/model/维度元数据。
- 使用 Redis + Dramatiq 分发任务，PostgreSQL 是状态事实源。
- 所有异步 Handler 按至少一次投递设计，依靠 `ReleaseChange.change_key`、Evidence source 唯一键和持久化 Checkpoint 保证幂等。

## 取舍

关系表让来源、状态和审计边界清晰，且能在无需额外基础设施的情况下完成 v1 双 Repo POC。只有真实负载证明关系遍历、全文检索、向量规模或长 Workflow 成为瓶颈时，才重新评估 Graph DB、独立向量库、Elasticsearch 或 Temporal。
