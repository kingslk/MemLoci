"""安全的 Bare Repository Mirror。"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.common.security import safe_exception_message
from packages.gitlab.clusters import HistoryCommit

ProgressCallback = Callable[[str, float], None]

_GIT_PROGRESS = re.compile(
    r"(Receiving objects|Resolving deltas):\s+(\d+)%",
    re.IGNORECASE,
)


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DIFF_CHAR_LIMIT = 10_000
_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".svg",
    ".avif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".jfif",
    ".apng",
}


def parse_git_progress(line: str) -> tuple[str, float] | None:
    """把 git --progress 的回车刷新行收成可展示的阶段和 0-1 进度。"""

    match = _GIT_PROGRESS.search(line)
    if not match:
        return None
    percent = int(match.group(2))
    receiving = match.group(1).lower().startswith("receiving")
    if receiving:
        return f"拉取对象 {percent}%", percent / 100 * 0.9
    return f"解析增量 {percent}%", 0.9 + percent / 100 * 0.09


@dataclass(frozen=True)
class MirrorSyncResult:
    repository_id: int
    path: Path
    remote_sha: str | None
    rebuilt: bool


class RepositoryMirror:
    def __init__(self, root: Path, *, token: str = "", ssl_verify: bool = False) -> None:
        self.root = root
        self.token = token
        self.ssl_verify = ssl_verify
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, repository_id: int) -> Path:
        """只用内部 ID 生成路径，避免用户输入参与路径拼接。"""

        return self.root / f"repository-{repository_id}.git"

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if not self.ssl_verify:
            env["GIT_SSL_NO_VERIFY"] = "1"
        if self.token:
            # Token 只进进程环境 Header，不写进 remote URL，避免 Mirror 目录泄漏凭证。
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            env["GIT_CONFIG_VALUE_0"] = f"PRIVATE-TOKEN: {self.token}"
        return env

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> str:
        if progress is None:
            try:
                completed = subprocess.run(
                    args,
                    cwd=cwd,
                    env=self._git_env(),
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(
                    f"Git mirror operation failed: {safe_exception_message(exc)}"
                ) from exc
            return completed.stdout.strip()
        return self._run_with_progress(args, cwd=cwd, progress=progress)

    def _run_with_progress(
        self,
        args: list[str],
        *,
        cwd: Path | None,
        progress: ProgressCallback,
    ) -> str:
        try:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                env=self._git_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(
                f"Git mirror operation failed: {safe_exception_message(exc)}"
            ) from exc
        stderr_chunks: list[str] = []
        assert process.stderr is not None
        for line in _iter_git_output(process.stderr):
            stderr_chunks.append(line)
            parsed = parse_git_progress(line)
            if parsed:
                progress(*parsed)
        stdout = process.stdout.read() if process.stdout else ""
        code = process.wait()
        if code != 0:
            detail = "".join(stderr_chunks)[-300:]
            raise RuntimeError(
                f"Git mirror operation failed: {safe_exception_message(RuntimeError(detail))}"
            )
        return stdout.strip()

    def sync(
        self,
        repository_id: int,
        clone_url: str,
        branch: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> MirrorSyncResult:
        """同步到可删除、可重建的 bare mirror；Mirror 不是事实源。"""
        mirror = self.path_for(repository_id)
        branch_ref = f"refs/heads/{branch}"
        rebuilt = False
        if not (mirror / "HEAD").exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            if progress:
                progress("正在克隆代码镜像", 0.02)
            # Bare clone：只缓存对象，不 checkout 工作区，也不会执行仓库内 hook/脚本。
            self._run(
                ["git", "clone", "--progress", "--bare", clone_url, str(mirror)],
                progress=progress,
            )
            rebuilt = True
        else:
            if progress:
                progress("正在拉取代码镜像", 0.02)
            self._run(
                [
                    "git",
                    "fetch",
                    "--progress",
                    "--prune",
                    "origin",
                    f"+{branch_ref}:{branch_ref}",
                ],
                cwd=mirror,
                progress=progress,
            )
        remote_sha = self._run(["git", "rev-parse", "--verify", branch_ref], cwd=mirror)
        return MirrorSyncResult(repository_id, mirror, remote_sha, rebuilt)

    def list_files(self, repository_id: int, sha: str) -> list[str]:
        output = self._run(
            ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", sha],
            cwd=self.path_for(repository_id),
        )
        return [unquote_git_path(line) for line in output.splitlines() if line]

    def read_file(self, repository_id: int, sha: str, path: str) -> str | None:
        safe_path = normalize_git_path(path)
        if ".." in Path(safe_path).parts:
            raise ValueError("非法文件路径")
        if _is_image_path(safe_path):
            return None
        try:
            return self._run(
                ["git", "show", f"{sha}:{safe_path}"], cwd=self.path_for(repository_id)
            )
        except (UnicodeDecodeError, RuntimeError):
            # 二进制、损坏路径或 git 读失败都跳过，不能让单文件打断整次初始化。
            return None

    def changed_files(
        self, repository_id: int, before_sha: str, after_sha: str
    ) -> list[dict[str, str]]:
        output = self._run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--name-status",
                before_sha,
                after_sha,
            ],
            cwd=self.path_for(repository_id),
        )
        changes: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                changes.append({"status": parts[0], "path": unquote_git_path(parts[1])})
            elif len(parts) >= 3:
                changes.append(
                    {
                        "status": parts[0],
                        "old_path": unquote_git_path(parts[1]),
                        "path": unquote_git_path(parts[2]),
                    }
                )
        return changes

    def history(self, repository_id: int, branch: str) -> list[HistoryCommit]:
        """一次读出正式分支上的 commit 和改动文件，供本地整合 diff。"""

        output = self._run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "log",
                "--format=%H%x09%s%x09%an%x09%aI",
                "--name-only",
                f"refs/heads/{branch}",
            ],
            cwd=self.path_for(repository_id),
        )
        commits: list[HistoryCommit] = []
        current: list[str] | None = None
        files: list[str] = []
        for line in output.splitlines():
            if "\t" in line:
                if current is not None:
                    commits.append(_history_commit(current, files))
                current = line.split("\t", 3)
                files = []
                continue
            if line:
                files.append(unquote_git_path(line))
        if current is not None:
            commits.append(_history_commit(current, files))
        return commits

    def first_parent(self, repository_id: int, sha: str) -> str | None:
        try:
            parent = self._run(
                ["git", "rev-parse", f"{sha}^1"], cwd=self.path_for(repository_id)
            )
        except RuntimeError:
            return None
        return parent or None

    def commits_between(self, repository_id: int, before: str, after: str) -> list[str]:
        output = self._run(
            ["git", "log", "--format=%H", f"{before}..{after}"],
            cwd=self.path_for(repository_id),
        )
        return [line for line in output.splitlines() if line]

    def unified_diff(
        self,
        repository_id: int,
        before_sha: str | None,
        after_sha: str,
        *,
        paths: list[str] | None = None,
        max_chars: int = DIFF_CHAR_LIMIT,
    ) -> str:
        start = before_sha or EMPTY_TREE
        args = [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--no-color",
            "-U2",
            "--find-renames",
            start,
            after_sha,
        ]
        if paths:
            args.append("--")
            args.extend(paths[:80])
        try:
            text = self._run(args, cwd=self.path_for(repository_id))
        except RuntimeError:
            return ""
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}\n…(diff truncated)"


def _history_commit(fields: list[str], files: list[str]) -> HistoryCommit:
    sha, title, author, stamp = (fields + ["", "", "", ""])[:4]
    try:
        committed_at = datetime.fromisoformat(stamp)
        if committed_at.tzinfo is None:
            committed_at = committed_at.replace(tzinfo=UTC)
    except ValueError:
        committed_at = datetime.min.replace(tzinfo=UTC)
    return HistoryCommit(
        sha=sha,
        title=title,
        author=author,
        committed_at=committed_at,
        files=tuple(files),
    )


def unquote_git_path(path: str) -> str:
    """还原 git quotePath 的 \"\\344\\274\\230.png\" 转义，避免中文文件名被拆成假目录。"""

    text = path.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    raw = bytearray()
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\":
            raw.extend(char.encode("utf-8"))
            index += 1
            continue
        octal = text[index + 1 : index + 4]
        if len(octal) == 3 and all(part in "01234567" for part in octal):
            raw.append(int(octal, 8))
            index += 4
            continue
        if index + 1 < len(text):
            escaped = text[index + 1]
            raw.extend({"n": b"\n", "t": b"\t", "r": b"\r", '"': b'"', "\\": b"\\"}.get(
                escaped, escaped.encode("utf-8")
            ))
            index += 2
            continue
        raw.append(ord("\\"))
        index += 1
    return raw.decode("utf-8")


def normalize_git_path(path: str) -> str:
    return unquote_git_path(path).replace("\\", "/").lstrip("/")


def _is_image_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in _IMAGE_SUFFIXES


def _iter_git_output(stream) -> Iterator[str]:
    """git --progress 用 \\r 刷新同一行，按回车或换行切开。"""

    buffer = ""
    while True:
        chunk = stream.read(256)
        if not chunk:
            if buffer:
                yield buffer
            return
        buffer += chunk
        while True:
            newline = buffer.find("\n")
            carriage = buffer.find("\r")
            cuts = [index for index in (newline, carriage) if index >= 0]
            if not cuts:
                break
            index = min(cuts)
            line = buffer[:index]
            buffer = buffer[index + 1 :]
            if line:
                yield line
