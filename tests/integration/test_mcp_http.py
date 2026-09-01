import json

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.common.config import get_settings

EXPECTED_TOOLS = {
    "list_projects",
    "code_search",
    "code_context",
    "code_trace",
    "memory_context",
    "memory_expand",
    "memory_compare",
    "evidence_open",
}


@pytest.fixture
def mcp_headers(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "test-mcp-token")
    get_settings.cache_clear()
    try:
        yield {
            "Authorization": "Bearer test-mcp-token",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
    finally:
        get_settings.cache_clear()


def _rpc_result(response) -> dict:
    if response.headers["content-type"].startswith("text/event-stream"):
        data_line = next(line for line in response.text.splitlines() if line.startswith("data:"))
        payload = json.loads(data_line[5:].strip())
    else:
        payload = response.json()
    assert "result" in payload, payload
    return payload["result"]


def test_remote_mcp_requires_bearer_token(db, mcp_headers) -> None:
    with TestClient(app) as client:
        missing = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert missing.status_code == 401
        wrong = client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert wrong.status_code == 401


def test_remote_mcp_handshake_lists_seven_tools(db, mcp_headers) -> None:
    with TestClient(app) as client:
        initialized = client.post(
            "/mcp",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "memloci-test", "version": "0.1.0"},
                },
            },
        )
        assert initialized.status_code == 200
        assert _rpc_result(initialized)["serverInfo"]["name"] == "MemLoci"

        listed = client.post(
            "/mcp",
            headers=mcp_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200
        names = {tool["name"] for tool in _rpc_result(listed)["tools"]}
        assert names == EXPECTED_TOOLS


def test_agent_http_requires_mcp_token(db, mcp_headers) -> None:
    with TestClient(app) as client:
        denied = client.post(
            "/api/v1/agent/memory-context",
            json={"project": "mall", "repo": "h5", "task": "fix token refresh"},
        )
        assert denied.status_code == 401
        missing = client.post(
            "/api/v1/agent/memory-context",
            headers=mcp_headers,
            json={"project": "mall", "repo": "h5", "task": "fix token refresh"},
        )
        assert missing.status_code == 404
