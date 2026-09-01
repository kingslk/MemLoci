from fastapi import HTTPException

from packages.common.config import get_settings
from packages.common.security import authorized_mcp, mcp_token, require_mcp


def test_mcp_token_falls_back_to_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOKEN", "")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    get_settings.cache_clear()
    try:
        assert mcp_token() == "admin-secret"
        assert authorized_mcp("Bearer admin-secret")
        assert not authorized_mcp("Bearer other")
        assert not authorized_mcp(None)
        assert not authorized_mcp("admin-secret")
    finally:
        get_settings.cache_clear()


def test_mcp_token_prefers_dedicated_value(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOKEN", "mcp-only")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    get_settings.cache_clear()
    try:
        assert mcp_token() == "mcp-only"
        assert authorized_mcp("Bearer mcp-only")
        assert not authorized_mcp("Bearer admin-secret")
        require_mcp("Bearer mcp-only")
        try:
            require_mcp("Bearer admin-secret")
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("admin token must not pass dedicated MCP token")
    finally:
        get_settings.cache_clear()
