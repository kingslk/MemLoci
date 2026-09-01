# Core Dual-Repo POC 验收记录

## 目标

验证：

```text
Repo A GitLab 历史
  -> ReleaseChange / Evidence
  -> Code Graph / Candidate Memory
  -> Dreaming Promote
  -> Repo B memory_context
  -> 明确可迁移内容、Evidence 和 do_not_copy
```

## 真实 Repo 候选

脚本默认值是占位符。连真实 GitLab 时，用环境变量指向你自己的两个仓库：

- `POC_REPO_A_NAME` / `POC_REPO_A_ID` / `POC_REPO_A_URL` / `POC_REPO_A_BRANCH`
- `POC_REPO_B_NAME` / `POC_REPO_B_ID` / `POC_REPO_B_URL` / `POC_REPO_B_BRANCH`

选两个分层和运行边界不同的仓库。Repo A 的历史实现不能直接变成 Repo B 的目录或后端架构要求。

## 可重复执行命令

前置：

```bash
cp .env.example .env
# 在 .env 注入真实 GitLab Base URL、只读 Token、Webhook Secret
uv run alembic upgrade head
uv run memloci-api
uv run dramatiq apps.worker.tasks --processes 1 --threads 1
```

执行真实 GitLab + 真实 MCP Client POC：

```bash
# ADMIN_TOKEN 必须通过当前 shell/部署平台注入，不要写入脚本或仓库
export ADMIN_TOKEN="${ADMIN_TOKEN:?请先注入 MemLoci 管理 Token}"
export POC_REPO_A_NAME=backend
export POC_REPO_A_ID=100
export POC_REPO_A_URL=https://gitlab.example.com/group/backend.git
export POC_REPO_A_BRANCH=main
export POC_REPO_B_NAME=frontend
export POC_REPO_B_ID=101
export POC_REPO_B_URL=https://gitlab.example.com/group/frontend.git
export POC_REPO_B_BRANCH=main
uv run python scripts/real_poc.py
```

脚本不会创建或修改 GitLab 远端项目，只会在 MemLoci 中建立/复用 POC Project 和两个 Repository 配置，并运行初始化、Dreaming 和 MCP `memory_context`。

## 当前边界

- 已验证：本地 Fixture 双 Repo 主链路、MCP 7 工具集合、Action Firewall、Session Dedup、Token Budget。
- 直接由 MemLoci `GitLabClient`/`RepositoryMirror` 拉取的部署验收仍需要在 API/Worker 环境注入
  `GITLAB_TOKEN` 后运行 `scripts/real_poc.py`；这一步不能用当前 MCP 连接的凭证替代，也不能把
  replay 结果冒充 Mirror/Hook 全链路结果。
