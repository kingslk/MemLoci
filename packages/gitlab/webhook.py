"""GitLab Webhook 验证与事件边界。"""

from __future__ import annotations

import hmac
import json
from typing import Any


class WebhookError(ValueError):
    """Webhook 不可信或不符合允许边界。"""


def parse_payload(
    raw_body: bytes,
    *,
    configured_secret: str,
    received_secret: str | None,
    expected_project_id: str,
) -> dict[str, Any]:
    """先校验 Secret 再解析 JSON。Secret 缺失与不匹配都当不可信请求，不进入业务。"""
    if not configured_secret or not received_secret:
        raise WebhookError("Webhook Secret 未配置或缺失")
    # compare_digest 防止按校验耗时猜测 Secret；失败统一 401，不回显两边的值。
    if not hmac.compare_digest(configured_secret, received_secret):
        raise WebhookError("Webhook Secret 校验失败")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise WebhookError("Webhook Payload 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookError("Webhook Payload 必须是对象")
    project = payload.get("project")
    if not isinstance(project, dict):
        raise WebhookError("Webhook Payload 缺少 GitLab 项目标识")
    project_ids = {
        str(value)
        for value in (project.get("id"), project.get("path_with_namespace"))
        if value is not None
    }
    if expected_project_id not in project_ids:
        raise WebhookError("Webhook GitLab 项目与 Repository 不匹配")
    return payload


def event_id_from_headers(headers: dict[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    return normalized.get("x-gitlab-event-uuid") or normalized.get("x-gitlab-event-id")
