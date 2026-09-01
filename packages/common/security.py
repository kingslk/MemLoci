"""请求认证和敏感信息脱敏。"""

import hashlib
import hmac
import json
from typing import Any

from fastapi import Header, HTTPException, status

from packages.common.config import get_settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> str:
    """最小管理员认证；生产部署应把 Token 放在平台 Secret 中。"""

    expected = get_settings().admin_token
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员认证失败")
    return "admin"


def mcp_token() -> str:
    """远程 MCP / Agent 只读 Token；未单独配置时回退到管理员 Token。"""

    settings = get_settings()
    return settings.mcp_token or settings.admin_token


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator == "" or scheme.lower() != "bearer" or not token:
        return None
    return token


def authorized_mcp(authorization: str | None) -> bool:
    token = bearer_token(authorization)
    expected = mcp_token()
    return bool(token and expected and hmac.compare_digest(token, expected))


def require_mcp(authorization: str | None = Header(default=None)) -> str:
    """远程 MCP 与 Agent HTTP 共用的只读 Bearer Token。"""

    if not authorized_mcp(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MCP 认证失败")
    return "mcp"


class RequireMCPToken:
    """挂在 `/mcp` ASGI 应用外层，只校验 Bearer Token，不走 OAuth。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if authorized_mcp(headers.get("authorization")):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "MCP 认证失败"}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def redact_secret(value: str) -> str:
    """只保留 Secret 的不可逆指纹，便于排障时关联同一配置。"""

    if not value:
        return ""
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def redact_payload(payload: Any, sensitive_keys: set[str] | None = None) -> Any:
    """递归移除 Token、Secret 等字段；外部 GitLab 内容仍按不可信文本处理。"""

    keys = sensitive_keys or {
        "token",
        "private_token",
        "webhook_secret",
        "secret",
        "authorization",
        "password",
        "api_key",
    }
    if isinstance(payload, dict):
        return {
            key: "[REDACTED]" if key.lower() in keys else redact_payload(value, keys)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(value, keys) for value in payload]
    return payload


def safe_exception_message(exc: Exception) -> str:
    """错误响应只返回类型和短消息，不返回请求头、URL 中的 Secret 或源码全文。"""

    message = str(exc).replace("\n", " ").strip()
    if len(message) > 300:
        message = f"{message[:297]}..."
    lowered = message.lower()
    if any(term in lowered for term in ("token", "secret", "password", "authorization")):
        return "外部依赖请求失败，详细信息请查看脱敏后的服务日志"
    return message or exc.__class__.__name__
