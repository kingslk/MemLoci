import httpx
import pytest

from packages.gitlab.client import GitLabClient


def test_gitlab_client_keeps_token_in_header_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"username": "reader"})
        if request.url.path.endswith("/branches/main"):
            return httpx.Response(200, json={"commit": {"id": "a" * 40}})
        return httpx.Response(404)

    client = GitLabClient("https://gitlab.example.com", "secret-token")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gitlab.example.com/api/v4",
    )
    try:
        assert client.connection_test()["username"] == "reader"
        assert client.get_branch_sha("1", "main") == "a" * 40
    finally:
        client.close()

    assert all(request.url.password == "" for request in seen)
    assert all(request.headers["PRIVATE-TOKEN"] == "secret-token" for request in seen)
    assert "secret-token" not in str(seen[0].url)


def test_gitlab_errors_do_not_echo_token() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = GitLabClient("https://gitlab.example.com", "secret-token")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gitlab.example.com/api/v4",
    )
    try:
        with pytest.raises(RuntimeError) as error:
            client.connection_test()
    finally:
        client.close()
    assert "secret-token" not in str(error.value)
