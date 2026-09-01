import json

import pytest

from packages.gitlab.webhook import WebhookError, parse_payload


def test_shared_webhook_secret_still_checks_repository_project() -> None:
    payload = json.dumps({"project": {"id": 12}, "object_kind": "push"}).encode()

    assert parse_payload(
        payload,
        configured_secret="shared-secret",
        received_secret="shared-secret",
        expected_project_id="12",
    )["object_kind"] == "push"

    with pytest.raises(WebhookError, match="Secret 校验失败"):
        parse_payload(
            payload,
            configured_secret="shared-secret",
            received_secret="wrong-secret",
            expected_project_id="12",
        )

    with pytest.raises(WebhookError, match="项目与 Repository 不匹配"):
        parse_payload(
            payload,
            configured_secret="shared-secret",
            received_secret="shared-secret",
            expected_project_id="99",
        )
