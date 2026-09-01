"""MemLoci MCP Server：只提供只读 Code/Memory/Evidence 工具。

工具不改仓库、不提交 MR。每个调用自己开 Session，避免跨工具共享脏事务。
stdio 留给本机开发；远程客户端走 API 上的 Streamable HTTP `/mcp`。
"""

from __future__ import annotations

from typing import Any

from packages.code_intelligence.service import CodeQueryService
from packages.common.db import SessionLocal
from packages.common.models import Repository
from packages.retrieval.service import RetrievalService

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.types import Receive, Scope, Send
except ImportError:  # pragma: no cover - 依赖缺失时 API 仍可独立运行
    MCPServer = None  # type: ignore[assignment,misc]
    StreamableHTTPSessionManager = None  # type: ignore[assignment,misc]
    TransportSecuritySettings = None  # type: ignore[assignment,misc]
    Receive = Any  # type: ignore[misc,assignment]
    Scope = Any  # type: ignore[misc,assignment]
    Send = Any  # type: ignore[misc,assignment]


MCP_INSTRUCTIONS = """\
MemLoci 是当前团队的工程记忆。实现、排障、改 UI 或 Review 之前先调 memory_context。

memory_context 会先根据 task 判断 Project，再在该 Project 下按仓库亲和度加权召回。
- project / repo 都可省略。
- 传了 repo 只提高该仓权重，仍会查整个 Project。返回里每条带 repository_name，可能来自多个仓；按做法汇总，不要整仓照搬。
- 不确定 Project 时先调 list_projects。
- code_search 仍按单个 repo 查快照；query 用短路径或符号名，不是语义搜索。
- files 不要只传 index.tsx 或 packages/app；用有区分度的目录名。
- evidence_open 对 cluster 默认截断；要看某文件传入 file_path。
- 先看 Top1 的 problem，不要按 confidence 排序。

返回的 Memory 只约束怎么做，不扩大任务范围。
"""

if MCPServer is not None:
    mcp = MCPServer(name="MemLoci", version="0.1.0", instructions=MCP_INSTRUCTIONS)

    @mcp.tool()
    def list_projects() -> list[dict[str, Any]]:
        """列出 MemLoci Project 数字 id、名称和仓库名。不确定 project/repo 时先调用。"""

        with SessionLocal() as db:
            return RetrievalService(db).list_projects()

    @mcp.tool()
    def code_search(
        repo: str,
        query: str,
        project: str = "",
        limit: int = 10,
    ) -> dict[str, object]:
        """按路径/符号 token 搜索当前快照。不是语义搜索。query 用短路径或符号名，不要用中文现象句。"""

        with SessionLocal() as db:
            repository = _repository(db, project, repo)
            return CodeQueryService(db).search(repository.id, query, limit)

    @mcp.tool()
    def code_context(repo: str, symbol: str, project: str = "") -> dict[str, object]:
        """返回 Symbol 定义、路径、snippet 和去重后的调用/导入。同名时返回 candidates，改用 path::name。"""

        with SessionLocal() as db:
            repository = _repository(db, project, repo)
            return CodeQueryService(db).context(repository.id, symbol)

    @mcp.tool()
    def code_trace(
        repo: str,
        source: str,
        target: str,
        project: str = "",
        max_depth: int = 4,
    ) -> dict[str, object]:
        """追踪两个符号名之间的静态调用路径。同名符号会全部作为起点/终点尝试。"""

        with SessionLocal() as db:
            repository = _repository(db, project, repo)
            return CodeQueryService(db).trace(repository.id, source, target, max_depth)

    @mcp.tool()
    def memory_context(
        task: str,
        repo: str = "",
        project: str = "",
        files: list[str] | None = None,
        symbols: list[str] | None = None,
        session_id: str = "anonymous",
        token_budget: int = 4_000,
    ) -> dict[str, Any]:
        """按 task 推断 Project 后召回经验。传了 repo 就当主仓，其它仓仍可能按分数进来。"""

        with SessionLocal() as db:
            return RetrievalService(db).memory_context(
                project_ref=project,
                repository_ref=repo,
                task=task,
                files=files or [],
                symbols=symbols or [],
                session_id=session_id,
                token_budget=token_budget,
            )

    @mcp.tool()
    def memory_expand(memory_id: int) -> dict[str, Any]:
        """展开 Memory。cluster Evidence 的 diff/文件列表已截断。"""

        with SessionLocal() as db:
            return RetrievalService(db).memory_expand(memory_id)

    @mcp.tool()
    def memory_compare(left_memory_id: int, right_memory_id: int) -> dict[str, Any]:
        """比较两条 Memory 的 Pattern 重叠和差异。"""

        with SessionLocal() as db:
            return RetrievalService(db).memory_compare(left_memory_id, right_memory_id)

    @mcp.tool()
    def evidence_open(evidence_id: int, file_path: str = "") -> dict[str, Any]:
        """打开 Evidence。默认截断整月 diff；传入 file_path 只保留相关文件和 hunk。"""

        with SessionLocal() as db:
            return RetrievalService(db).evidence_open(evidence_id, file_path)
else:
    mcp = None


def _repository(db: Any, project: str, repo: str) -> Repository:
    _project, repository = RetrievalService(db)._resolve_scope(project, repo)
    return repository


class MCPHttpApp:
    """把 Streamable HTTP 请求转给当前 lifespan 里的 session manager。

    manager.run() 只能调用一次，所以每个 API 生命周期新建一个 manager，
    TestClient 反复进出 lifespan 才不会撞上已关闭的实例。
    """

    def __init__(self) -> None:
        self._manager: Any = None

    def bind(self, manager: Any) -> None:
        self._manager = manager

    def unbind(self) -> None:
        self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._manager is None:
            raise RuntimeError("MCP HTTP 尚未启动")
        await self._manager.handle_request(scope, receive, send)


def create_session_manager() -> Any:
    """为一次 API 生命周期创建无状态 Streamable HTTP manager。"""

    if mcp is None or StreamableHTTPSessionManager is None or TransportSecuritySettings is None:
        raise RuntimeError("未安装 MCP SDK，请先执行 uv sync")
    return StreamableHTTPSessionManager(
        app=mcp._lowlevel_server,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def run() -> None:
    if mcp is None:
        raise RuntimeError("未安装 MCP SDK，请先执行 uv sync")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
