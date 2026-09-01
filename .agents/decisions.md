# 设计决策记录

只记代码读不出来的「为什么」。每条 3–5 行；决策失效直接删。

## PostgreSQL 承载图与 Workflow

选择关系表 + JSON + Job/Checkpoint，不用独立 Graph DB / Temporal。少一套基础设施，来源、状态、审计边界更清楚。Redis 只分发 Dramatiq 消息，不是状态源。复查：关系遍历、全文或长 Workflow 成为真实瓶颈。完整背景：`docs/adr/0001-v1-storage-and-workflow.md`。运维：`docs/backup-restore.md`。

## Hash Embedding 与 JSON 向量

本地默认 Hash，不是语义模型；向量先放 JSON，并记下 provider/model/维度。现在上 pgvector 列或本地语义模型会假装已经有语义。复查：已接入真实 Embedding 并要按模型整批重建。

## 记忆对撞不用向量

对撞判定「是不是同一条经验」，用文件重叠 + 用词重叠，不用向量近邻。冲突保守、人改过的不合并、一对一轮。复查：误合率高到必须换指纹，或规则筛不动。详述：`docs/memory-collision.md`。

## 验收不写死真实仓库

`scripts/real_poc.py` 和 `docs/acceptance/core-poc.md` 只保留占位与环境变量。复查：有人把真实 GitLab 地址或项目 ID 写回默认值。

## 扩展阶梯

入口超 40 行再分域 `AGENTS.md`；本文件超 60 行再按域拆；同流程重复 ≥3 次再写 Skill。不预装评测 case、Owner 表或任务仪式。
