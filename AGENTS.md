# MemLoci Agent Guide

给编码 Agent 的最小上下文。代码结构、类型和调用链自己读源码推导；本文只写代码里看不出来的事实。

## 协作

- 全程使用中文。
- 复杂、方向性或高影响改动：先给 1–2 个方案等用户选择；已选定后在范围内自主完成。
- 开始前看 `git status --short`；已有改动属于用户，禁止覆盖、回滚或顺手整理。
- 按开源仓库写：示例和默认值只用 `example.com` 与通用占位；Commit 不写脱敏过程。

## 产物边界

- `mirrors/`、`apps/web/dist/`、`web-dist/` 是生成物，只改源码，不改产物。

## 硬边界

- GitLab 是代码与历史事实源；Mirror 只是可重建缓存，Token 不写入 remote URL。
- 不修改用户仓库、不自动提交 MR。
- Memory 只约束「怎么做」，不能扩大当前任务 Scope；来源仓架构不能当成目标仓要求。
- 生产 LLM 不得静默降级 `heuristic`；测试必须显式 `LLM_PROVIDER=heuristic`。
- 记忆冲突只记账不删；人改过的记忆不参与合并。宁可漏合，也不误合。
- 当前 Hash Embedding 与 JSON 向量是决策不是缺陷，改动先给方案。

## 完成标准

- 验证与风险成比例。pytest 需本机 PostgreSQL（`memloci_test`）。未运行的验证如实说明。
- 只读直接答；小改动一行；多文件/高影响用四行卡片：✅ 结果 / ⚖️ 取舍 / 🔍 验证 / ⚠️ 风险。

设计意图与历史决策（仅需要时读）：`.agents/decisions.md`
