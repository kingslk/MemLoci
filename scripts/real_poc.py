"""使用真实 GitLab Repo 和真实 MCP Client 执行双 Repo POC。

前置：API 已启动，`.env` 已配置真实 GitLab Token；Worker 可选但建议启动。
脚本不创建或修改 GitLab 远端项目，只在 MemLoci 中建立 Project/Repository 配置。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

API_URL = os.getenv("MEMLOCI_API_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]
PROJECT_NAME = os.getenv("POC_PROJECT_NAME", "memloci-real-poc")
REPO_A = {
    "name": os.getenv("POC_REPO_A_NAME", "backend"),
    "gitlab_project_id": os.getenv("POC_REPO_A_ID", "100"),
    "clone_url": os.getenv(
        "POC_REPO_A_URL",
        "https://gitlab.example.com/group/backend.git",
    ),
    "release_branch": os.getenv("POC_REPO_A_BRANCH", "main"),
}
REPO_B = {
    "name": os.getenv("POC_REPO_B_NAME", "frontend"),
    "gitlab_project_id": os.getenv("POC_REPO_B_ID", "101"),
    "clone_url": os.getenv(
        "POC_REPO_B_URL",
        "https://gitlab.example.com/group/frontend.git",
    ),
    "release_branch": os.getenv("POC_REPO_B_BRANCH", "main"),
}


def api_client() -> httpx.Client:
    return httpx.Client(
        base_url=API_URL,
        timeout=httpx.Timeout(600.0),
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )


def ensure_project(client: httpx.Client) -> dict[str, Any]:
    projects = client.get("/api/v1/projects").raise_for_status().json()
    for project in projects:
        if project["name"] == PROJECT_NAME:
            return project
    return (
        client.post(
            "/api/v1/projects",
            json={
                "name": PROJECT_NAME,
                "description": "真实 GitLab 双 Repo POC；仅用于验收，不代表来源架构可直接复制。",
            },
        )
        .raise_for_status()
        .json()
    )


def ensure_repository(
    client: httpx.Client, project_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    repositories = (
        client.get(f"/api/v1/projects/{project_id}/repositories").raise_for_status().json()
    )
    for repository in repositories:
        if repository["name"] == payload["name"]:
            return repository
    return (
        client.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={**payload, "ignore": ["node_modules/**", "dist/**", "**/*.min.js"]},
        )
        .raise_for_status()
        .json()
    )


def run_initialization(client: httpx.Client, project_id: int) -> dict[str, Any]:
    job = (
        client.post(
            "/api/v1/initializations",
            params={"project_id": project_id},
            json={},
        )
        .raise_for_status()
        .json()
    )
    return client.post(f"/api/v1/jobs/{job['id']}/run").raise_for_status().json()


async def call_real_mcp(project: str, repo: str) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.setdefault("PYTHONPATH", str(Path.cwd()))
    params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "apps.mcp.server"],
        cwd=str(Path.cwd()),
        env=environment,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            expected = {
                "code_search",
                "code_context",
                "code_trace",
                "memory_context",
                "memory_expand",
                "memory_compare",
                "evidence_open",
            }
            if tool_names != expected:
                raise RuntimeError(f"MCP 工具集合不完整: {sorted(tool_names)}")
            result = await session.call_tool(
                "memory_context",
                {
                    "project": project,
                    "repo": repo,
                    "task": os.getenv(
                        "POC_TASK",
                        "分析日志采集中的异常处理和路由边界，只迁移可验证经验",
                    ),
                    "files": ["src/routes/index.js", "src/components/EventBase/index.tsx"],
                    "symbols": ["router"],
                    "session_id": "real-dual-repo-poc",
                    "token_budget": 4_000,
                },
            )
            content = getattr(result, "content", [])
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    return json.loads(text)
            return result.model_dump() if hasattr(result, "model_dump") else {"result": str(result)}


def main() -> None:
    with api_client() as client:
        project = ensure_project(client)
        repo_a = ensure_repository(client, project["id"], REPO_A)
        repo_b = ensure_repository(client, project["id"], REPO_B)
        initialization = run_initialization(client, project["id"])
        context = asyncio.run(call_real_mcp(PROJECT_NAME, repo_b["name"]))

    results = context.get("results", [])
    if not results:
        raise SystemExit(
            "POC 未通过：Repo A 历史没有在 Repo B memory_context 返回；请检查 Token、Worker、"
            "branch、历史 Evidence 和日志。"
        )
    if not any(item.get("do_not_copy") for item in results):
        raise SystemExit("POC 未通过：返回内容缺少 do_not_copy 边界。")
    print(
        json.dumps(
            {
                "status": "passed",
                "project": project,
                "source_repo": repo_a,
                "target_repo": repo_b,
                "initialization": initialization,
                "memory_context": context,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
