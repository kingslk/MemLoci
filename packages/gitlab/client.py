"""GitLab REST client.

Token 只进入内存中的 HTTP Header，不进入 URL、异常响应或结构化日志。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

import httpx

from packages.common.security import redact_secret, safe_exception_message


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = 20.0,
        *,
        verify: bool = False,
    ) -> None:
        # 统一在 Client 内拼接 /api/v4，调用方只需提供 GitLab 实例根地址。
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client = httpx.Client(
            base_url=f"{self.base_url}/api/v4",
            timeout=timeout,
            headers={"Accept": "application/json"},
            verify=verify,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> GitLabClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token} if self.token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """失败只抛脱敏信息。Token 在 Header 里，不能进入异常字符串或日志。"""
        try:
            response = self.client.request(method, path, headers=self._headers(), **kwargs)
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = f"GitLab returned HTTP {exc.response.status_code}"
            raise RuntimeError(detail) from exc
        except httpx.HTTPError as exc:
            token_hint = redact_secret(self.token)
            detail = safe_exception_message(exc)
            raise RuntimeError(f"GitLab connection failed ({token_hint}): {detail}") from exc

    def connection_test(self) -> dict[str, Any]:
        data = self._request("GET", "/user")
        return {
            "ok": True,
            "username": data.get("username") if isinstance(data, dict) else None,
            "base_url": self.base_url,
        }

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{quote(project_id, safe='')}")

    def get_branch_sha(self, project_id: str, branch: str) -> str:
        data = self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/branches/{quote(branch, safe='')}",
        )
        return str(data["commit"]["id"])

    def get_tree(
        self,
        project_id: str,
        *,
        ref: str,
        recursive: bool = True,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                f"/projects/{quote(project_id, safe='')}/repository/tree",
                params={"ref": ref, "recursive": recursive, "per_page": per_page},
            )
        )

    def get_raw_file(self, project_id: str, path: str, *, ref: str) -> str:
        response = self.client.get(
            f"/projects/{quote(project_id, safe='')}/repository/files/{quote(path, safe='')}/raw",
            params={"ref": ref},
            headers=self._headers(),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"GitLab raw file request failed: HTTP {exc.response.status_code}"
            ) from exc
        return response.text

    def get_commits(
        self,
        project_id: str,
        *,
        ref: str | None = None,
        per_page: int = 100,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref_name"] = ref
        return list(
            self._paginate(
                f"/projects/{quote(project_id, safe='')}/repository/commits",
                params=params,
                max_items=max_items,
            )
        )

    def get_merge_requests(
        self,
        project_id: str,
        *,
        state: str = "merged",
        per_page: int = 100,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                f"/projects/{quote(project_id, safe='')}/merge_requests",
                params={"state": state, "per_page": per_page, "scope": "all"},
                max_items=max_items,
            )
        )

    def compare(self, project_id: str, *, from_sha: str, to_sha: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/compare",
            params={"from": from_sha, "to": to_sha},
        )

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any],
        max_items: int | None = None,
    ) -> Iterable[dict[str, Any]]:
        page = 1
        yielded = 0
        while True:
            page_params = {**params, "page": page}
            data = self._request("GET", path, params=page_params)
            if not data:
                return
            for item in data:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            if len(data) < int(params.get("per_page", 100)):
                return
            page += 1
