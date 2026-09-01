# MemLoci

MemLoci 是一个面向 Coding Agent 的工程记忆系统：从 GitLab 代码、Commit、MR、Diff 和 Review 中建立可追溯的 Code Graph 与 Evidence，再提炼带有适用条件和 `do_not_copy` 边界的 Memory，经过 Dreaming 验证后，为 Project 内其它 Repo 提供压缩但详细的上下文。MemLoci 不修改用户仓库、不自动提交 MR，也不把来源 Repo 的历史架构当成目标 Repo 的要求。

## 核心原则

- GitLab 是代码与历史事实源；Repository Mirror 只是可重建缓存。
- 一个 Project 可以包含多个 Repo，跨 Repo 检索以 Project 为候选池。
- 正式分支变化统一为幂等 `ReleaseChange`。
- `Evidence`、`Candidate Memory` 和长期 `Memory` 分离。
- 每条可迁移经验同时说明 `pattern`、`implementation`、`do_not_copy`、`apply_when`、`do_not` 和 Evidence。
- Memory 只能约束“怎么做”，不能扩大当前用户任务 Scope。
- 状态演化、人工纠错、Dream Change 和 Agent 查询均可审计。

## 系统架构

```mermaid
flowchart LR
  gitlab[GitLab] --> ingestion[WebhookAndHistorySync]
  ingestion --> mirror[RepositoryMirror]
  mirror --> codeGraph[CodeGraph]
  ingestion --> evidence[EvidenceCatalog]
  codeGraph --> retrieval[ContextCompiler]
  evidence --> memory[MemoryEngine]
  memory --> dreaming[DreamingEngine]
  dreaming --> retrieval
  retrieval --> mcp[MCPServer]
  api[CoreAPI] --> db[(PostgreSQLAndPgvector)]
  codeGraph --> db
  evidence --> db
  memory --> db
  dreaming --> db
  worker[DramatiqWorker] --> db
  queue[Redis] --> worker
  web[ReactConsole] --> api
```

- `apps/api`：FastAPI HTTP API、健康检查、配置、CRUD、Webhook 和查询入口。
- `apps/worker`：Dramatiq 后台任务；PostgreSQL 保存 Job/Checkpoint 状态，Redis 只负责分发。
- `apps/mcp`：只读 Agent 工具，经 API `/mcp` 对外提供，不依赖某个 Coding Agent 插件。
- `apps/web`：React Web Console，提供 Project、Repo、Memory、Dream、初始化和图谱视图。
- `packages/gitlab`：GitLab Client、Webhook、Mirror、Ignore、`ReleaseChange`。
- `packages/code_intelligence`：Parser、Symbol、Relation、Code Graph 查询。
- `packages/evidence`、`memory`、`dreaming`、`retrieval`：事实、知识生命周期、Dreaming 和上下文编译。

## 技术栈与版本要求

- Python 3.13+
- FastAPI、SQLAlchemy、Alembic、Pydantic
- PostgreSQL + pgvector
- Redis + Dramatiq
- Git CLI、`tree-sitter`
- React、TypeScript、Vite、TanStack Query、Sigma.js、Graphology
- Python 优先使用 `uv`，Web 优先使用 `pnpm`

## 项目目录结构

```text
apps/
  api/          FastAPI
  worker/       Dramatiq
  mcp/          MCP Server
  web/          React Console
packages/
  common/       配置、模型、Schema、安全、审计
  gitlab/       GitLab、Mirror、Webhook
  code_intelligence/
  evidence/
  memory/
  dreaming/
  retrieval/
  embeddings/
  llm/
  initialization/
migrations/
tests/
docs/adr/
```

## 快速启动

需要 Python 3.13+、`uv`、Node.js、`pnpm`，以及本机或容器里的 PostgreSQL 与 Redis。
仓库里的 `Dockerfile` 只跑这两项依赖；API、Worker、MCP 和 Web 在宿主机启动。

```bash
cp .env.example .env

# Docker（或兼容的 container CLI）启动 PostgreSQL + Redis。
docker build -t memloci-infra:local .
docker volume create memloci-postgres-data
docker run -d \
  --name memloci-infra \
  -e POSTGRES_USER=memloci \
  -e POSTGRES_PASSWORD=memloci \
  -e POSTGRES_DB=memloci \
  --mount type=volume,source=memloci-postgres-data,target=/var/lib/postgresql/data \
  -p 5432:5432 \
  -p 6379:6379 \
  memloci-infra:local

# .env 用 localhost 连容器映射到宿主机的端口。
uv sync --extra dev
pnpm install --dir apps/web
uv run alembic upgrade head
uv run memloci-api
```

另开终端启动 Worker 和 Web。远程 MCP 挂在 API 的 `/mcp`，不必再单独起 Python 进程：

```bash
# LLM 任务串行执行，避免多个 Worker 同时打网关。
uv run dramatiq apps.worker.tasks --processes 1 --threads 1
pnpm --dir apps/web dev
```

查看依赖容器日志或停止：

```bash
docker logs memloci-infra
docker stop memloci-infra
```

健康检查：

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

预期 `live` 返回 `{"status":"ok"}`；`ready` 至少包含 `"database":"ok"`。本地开发必须先启动 PostgreSQL；不启动 Worker 时可以不启 Redis。执行 Candidate、Dreaming 或真实初始化前，必须先配置外部 LLM 的 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。

## 环境变量与配置

| 变量 | 必需 | 默认值 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| `ADMIN_TOKEN` | 是 | `change-me` | 是 | HTTP 管理接口的 `X-Admin-Token` |
| `MCP_TOKEN` | 远程 MCP | 回退 `ADMIN_TOKEN` | 是 | Agent 客户端的 Bearer Token；生产应单独设置 |
| `DATABASE_URL` | 是 | PostgreSQL | 否 | 默认 `postgresql+psycopg://memloci:memloci@127.0.0.1:5432/memloci` |
| `REDIS_URL` | Worker | `redis://localhost:6379/0` | 否 | Dramatiq 任务队列；不启动 Worker 时可不使用 |
| `GITLAB_BASE_URL` | GitLab | 示例地址 | 否 | GitLab 实例根地址 |
| `GITLAB_TOKEN` | GitLab | 空 | 是 | 最小只读 Repository/API 权限 |
| `GITLAB_WEBHOOK_SECRET` | Webhook | 空 | 是 | 所有 GitLab Webhook 共用的校验 Secret |
| `MIRROR_ROOT` | Mirror | `./mirrors` | 否 | 本地 Bare Mirror 根目录 |
| `EMBEDDING_*` | Embedding | Hash Provider | 否 | Provider、模型和向量维度 |
| `LLM_PROVIDER` / `LLM_MODEL` | Dreaming | `openai` / `gpt-4o-mini` | 否 | 外部模型 Provider 和模型名 |
| `LLM_REASONING_EFFORT` | LLM | `medium` | 否 | 默认推理强度；明确冲突复核临时使用 `high`；留空则不下发 |
| `AUTO_DREAM_ENABLED` | Worker | `false` | 否 | 全局夜间自动整理 Candidate 开关 |
| `AUTO_DREAM_HOUR` / `AUTO_DREAM_TIMEZONE` | Worker | `3` / `UTC` | 否 | 每天到点后为有待审记忆的项目补跑一次“整理新变更” |
| `OPENAI_API_KEY` | LLM | 空 | 是 | 外部 OpenAI-compatible API Key，默认 Provider 必填 |
| `OPENAI_BASE_URL` | LLM | 空 | 否 | 外部 API Base URL，默认 Provider 必填 |

真实 Token、Secret 和 LLM Key 不得写入仓库、数据库普通日志、URL 或前端构建产物。Web Console 的管理 Token 由操作者在页面中输入，仅保存在当前浏览器 `sessionStorage`，不会通过 `VITE_*` 编译进 JavaScript。

### 为什么有些配置在 `.env`，有些配置在 Repository 页面？

MemLoci 把配置分成两层：

1. **实例级 `.env` 配置**：整个 MemLoci 服务共用。
   - `GITLAB_BASE_URL`：GitLab 实例地址，例如 `https://gitlab.example.com`；不是某个项目的 URL。
   - `GITLAB_TOKEN`：服务账号凭证。一个 Token 可以访问多个 GitLab Repo，实际能访问哪些 Repo 由 GitLab 权限决定。
   - `GITLAB_WEBHOOK_SECRET`：所有 Repo Webhook 共用；必须显式配置，没有代码默认值。
   - `OPENAI_API_KEY`、`OPENAI_BASE_URL`：整个服务调用外部 LLM 的凭证和入口。
2. **Repository 级数据库配置**：每个 Repo 可以不同，在 Web Console/API 中填写。
   - `gitlab_project_id`
   - `clone_url`
   - `release_branch`
   - Ignore 规则

因此，两个不同 GitLab 项目共享 `.env` 中的 GitLab 实例、服务账号和 Webhook Secret，但分别填写自己的 Repo ID、Clone URL、正式分支和 Ignore 规则。共享 Secret 只负责请求认证；服务还会校验 Payload 中的 GitLab Project ID 是否匹配 URL 对应的 Repository。

### 外部 LLM 配置

默认 `LLM_PROVIDER=openai`，不会静默使用本地模型。必须在 `.env` 填写：

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_REASONING_EFFORT=medium
AUTO_DREAM_ENABLED=true
AUTO_DREAM_HOUR=3
AUTO_DREAM_TIMEZONE=UTC
OPENAI_API_KEY=你的APIKey
OPENAI_BASE_URL=https://api.openai.com/v1
```

MemLoci 使用官方 `openai` Python SDK 的 Responses Structured Outputs，不使用 `openai-agents`：当前 LLM 只负责 Candidate 提取和 Memory 比较，没有多 Agent、工具调用或 handoff 场景。测试环境如需离线运行，必须显式设置 `LLM_PROVIDER=heuristic`，不能作为生产降级策略。

## GitLab 接入

1. 在 GitLab 创建最小权限 Token，配置 `GITLAB_BASE_URL` 和 `GITLAB_TOKEN`。
2. 通过 `POST /api/v1/repositories/{id}/connection-test` 验证连接。
3. 在 Webhook 中配置：
   - URL：`POST /api/v1/webhooks/gitlab/{repository_id}`
   - Secret：与 `GITLAB_WEBHOOK_SECRET` 相同
   - Push events、Merge request events
4. 每个 Repo 在 Web Console 中设置唯一 `release_branch` 和 Ignore 规则。
5. API 快速持久化事件，Worker 再执行 Mirror、Ignore、Code、Evidence 和 Candidate 管线。

Push Hook、MR merge Hook、Direct Push、Revert、Cherry-pick、Bot Push 和 Force Push 均归一到 `ReleaseChange`。重复 Hook 使用唯一变更指纹去重，Webhook 丢失时通过 release branch SHA 对账补偿。

## Repository Mirror 与 Ignore

Mirror 使用 Bare Repository，并以内部 `repository_id` 生成路径。Git 命令全部使用参数数组，不执行仓库内脚本；Token 通过临时 Git 配置 Header 使用，不持久化到 remote URL。

Ignore 规则在全量初始化、历史分析、Code Graph、Webhook 处理、Embedding、Evidence、Dreaming 和 Agent `code_search` 之前统一应用。`GET /api/v1/repositories/{id}/ignore-preview` 可预览给定路径集合的包含/排除结果。

## MCP Server 配置与工具说明

MCP Server 提供以下只读工具。连上之后应在动手前先调 `memory_context`，不必等用户提醒：

```text
list_projects()
code_search(project, repo, query, limit)  # token 路径/符号匹配，空结果带 hint 和 snapshot_sha
code_context(project, repo, symbol)
code_trace(project, repo, source, target, max_depth)
memory_context(task, project?, repo?, files, symbols, session_id, token_budget)
  # 先判 Project，再按仓亲和度加权；repo 只是提示，不锁当前工作区
memory_expand(memory_id)
memory_compare(left_memory_id, right_memory_id)
evidence_open(evidence_id, file_path)  # cluster 默认截断；file_path 只留相关 hunk
```

`code_search` 按路径/符号 token 匹配，不是语义搜索；自然语言请用 `memory_context`。`files` 请带有区分度的目录名，不要只传 `index.tsx`。

默认通过已经启动的 API 提供远程 Streamable HTTP MCP，客户端只填 URL 和 Token，不需要本机 Python：

```json
{
  "mcpServers": {
    "memloci": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer 你的MCP_TOKEN"
      }
    }
  }
}
```

本机调试仍可用 stdio：

```json
{
  "mcpServers": {
    "memloci": {
      "command": "uv",
      "args": ["run", "python", "-m", "apps.mcp.server"],
      "cwd": "/absolute/path/to/MemLoci"
    }
  }
}
```

最小调用：

```json
{
  "project": "mall",
  "repo": "mall-h5",
  "files": ["src/request/index.ts"],
  "symbols": ["refreshToken"],
  "task": "修复 Token 过期后的重复刷新问题",
  "session_id": "agent-session-001",
  "token_budget": 4000
}
```

`memory_context` 在 Project 候选池内统一排序，返回事实、相关原因、可迁移 Pattern、`do_not_copy`、Evidence ID 和 Action Firewall 边界；不会要求 Agent 上传整个仓库。

## Memory 模型

Memory 类型包括 `episodic`、`semantic`、`procedural` 和 `convention`；状态流转为：

```text
candidate -> tentative -> active
                         -> deprecated -> archived
candidate/tentative -> rejected
```

全量初始化只生成 `tentative` Memory；MCP 有相关 `active` 时不返回 `tentative`，仅在没有合格 `active` 时降级返回并标记未确认。明确冲突的近邻会在默认 `medium` 比较后，以 `high` 再复核一次。

没有 Evidence 的 Candidate 不能晋升 `active`。人工修正通过 `PATCH /api/v1/memories/{id}` 完成，状态、Confidence、Pattern、Scope 和原因都会进入 `audit_logs`。

## Dreaming 工作方式

- `incremental`：只处理新 Candidate 关联的 Dirty Topic。
- `manual`：用户主动触发局部 Dream。
- `genesis`：全量初始化后的首次 Dream。
- `full_validation`：重新验证当前 Project 的 Memory、Evidence 和状态，不删除历史审计。

每次 `DreamRun` 保存 Provider、Model、Prompt 版本、输入、输出、Token、耗时、错误和 `DreamChange`。高影响的 Scope 晋升、合并和替代可以人工确认，并支持 Change Set 撤回。

## 首次全量初始化

`POST /api/v1/initializations?project_id={id}` 创建 Job，`GET /api/v1/projects/{id}/initializations` 查看进度，五个 Pass 为：

1. `current_state_scan`
2. `full_history_scan`
3. `architecture_epochs`
4. `topic_reconstruction`
5. `genesis_dream`

每个 Job 保存 `status`、`current_stage`、`progress`、`retry_count`、`checkpoint`、`error` 和时间戳。可以暂停、继续、取消和重试；已完成 Pass 不会重复执行。PostgreSQL 保存 Workflow State，Redis 只负责 Worker 消息分发。

## Web Console 与 Graph

`apps/web` 提供 Dashboard、Repo/Mirror/branch/Ignore 设置、Memory Browser、Dream Browser、Initialization 状态和人工纠错。Graph 页面按需加载 `code`、`memory`、`combined` 三种 Graph，并保留列表、详情和 Evidence 作为无图谱替代入口：

```text
GET /api/v1/projects/{id}/graphs/code?repository_id={repo_id}
GET /api/v1/projects/{id}/graphs/memory?repository_id={repo_id}
GET /api/v1/projects/{id}/graphs/combined?repository_id={repo_id}
```

## 开发规范与注释规范

- 核心注释解释“为什么”和业务约束，不逐行翻译代码。
- 非平凡的幂等、状态转换、安全和边界逻辑必须有测试。
- 新增模型必须有 Alembic Migration；新增 HTTP/MCP Schema 必须同步文档。
- 提交前执行：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run alembic check
pnpm --dir apps/web build
```

## 数据库迁移

```bash
uv run alembic upgrade head
uv run alembic check
uv run alembic downgrade base
```

PostgreSQL 是唯一事实存储，本地开发、测试和生产都使用它。pytest 默认连接同实例上的 `memloci_test`。当前向量仍以 JSON 保存，并记录 Provider、Model、维度和版本以便后续重建。

## 测试与验收

- Unit：Ignore、`ReleaseChange` 幂等、Memory 状态机、检索排序、Action Firewall、Checkpoint。
- Integration：迁移、GitLab Fixture/Mirror、Webhook→ReleaseChange→Job、Parser→Graph、Evidence→Dream、MCP Schema。
- E2E：双 Repo 场景中 Repo A 的历史经验在架构不同的 Repo B 被召回，且明确可迁移内容和禁止照搬架构。

Fixture 只能证明本地逻辑；连上本机 PostgreSQL 的测试只能证明本地集成；只有真实 GitLab、真实 Repo 和真实 MCP Client 才能声明真实链路通过。

真实双 Repo 验收脚本见 `docs/acceptance/core-poc.md`。GitLab 地址和仓库用环境变量注入，不要把真实实例写进仓库：

```bash
export ADMIN_TOKEN="${ADMIN_TOKEN:?请先注入 MemLoci 管理 Token}"
uv run python scripts/real_poc.py
```

## 部署、备份与恢复

生产至少分离 API、Worker、MCP、Web、PostgreSQL、Redis 和 Mirror 持久卷。定期备份 PostgreSQL 并演练恢复；Mirror 可从 GitLab 重建，Redis 不作为恢复事实源。Secret 由部署平台管理，不进入普通数据库备份。

## 常见问题与排障

- `401 Webhook`：检查 `GITLAB_WEBHOOK_SECRET` 和 GitLab Secret 是否完全一致，以及 Payload 中的 GitLab Project ID 是否匹配。
- `ReleaseChange` 重复：检查同一 Repo 的 `before_sha`、`after_sha`、`release_branch`；重复事件应返回已有 ID。
- Mirror Fetch 失败：查看 `repository_sync_state`，确认 Token 权限、Clone URL 和 release branch；旧 Mirror 会保留。
- Job 中断：读取 `jobs.checkpoint`，使用 `/retry` 从最近完成 Pass 继续。
- MCP 无结果：先确认 Project/Repo 名称、Code Graph/Evidence 是否已同步，再用 `memory_expand` 检查 Evidence。
- `ready` 失败：先检查数据库连通性和迁移版本。

## 安全说明

MemLoci 不执行仓库脚本、不运行构建命令、不加载不可信插件。GitLab 代码、MR、Review 和 Evidence 都是不可信数据，不能作为系统指令执行；Ignore 和敏感路径过滤发生在 LLM/Embedding 调用前。日志只记录 ID、耗时、状态和脱敏错误，不记录 Secret 或大段私有代码。

## 路线图

- Phase 0：基础工程、CRUD、配置、迁移、健康检查。
- Phase 1：GitLab Ingestion、Mirror、Webhook、ReleaseChange。
- Phase 2：Code Intelligence 和增量 Code Graph。
- Phase 3～5：Evidence、Memory、Dreaming。
- Phase 6：跨 Repo Retrieval、Context Compiler、7 个 MCP 工具和 Action Firewall。
- Phase 7：可恢复的五 Pass 全量初始化。
- Phase 8：Web Console、人工纠错和三种 Graph。

阶段状态以测试、真实验收命令和 `docs/adr/` 记录为准，不以文档文字代替验证。

## 许可证

尚未选定公开许可证。在补上 `LICENSE` 之前，默认保留所有权利；请先和仓库维护者确认后再分发或再授权。
