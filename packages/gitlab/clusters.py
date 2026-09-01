"""把上万条 commit 收成可给 LLM 看的变更团。

一万条不能一条打一轮，也不能十几条一组（仍会打出几百轮）。
按「模块 × 自然月」合：三年仓库大约几十到两百个团，每个团看过滤后的 diff。
车道默认取路径前两段；前面若是 web/packages/tool 这类容器，再往下走到真正的模块名。
标题只作辅证。图片、嵌套 release、仓库 Ignore 在合组前就丢掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from packages.gitlab.ignore import IgnoreMatcher

# 单团上限：再大就按时间切开，避免一次 diff 塞进整月所有改动。
MAX_CLUSTER_COMMITS = 60
# 只跳过会把整仓糊成一条道的前缀，不把 src 算进去，避免 src/upload 被拆碎。
CONTAINER_SEGMENTS = frozenset(
    {
        "web",
        "packages",
        "package",
        "service",
        "services",
        "tool",
        "tools",
        "app",
        "apps",
    }
)


@dataclass(frozen=True)
class HistoryCommit:
    sha: str
    title: str
    author: str
    committed_at: datetime
    files: tuple[str, ...]


@dataclass(frozen=True)
class ChangeCluster:
    source_type: str
    source_id: str
    title: str
    summary: str
    shas: tuple[str, ...]
    files: tuple[str, ...]
    before_sha: str | None
    after_sha: str
    lane: str
    period: str


def build_clusters(
    commits: list[HistoryCommit],
    *,
    matcher: IgnoreMatcher,
) -> list[ChangeCluster]:
    buckets: dict[tuple[str, str], list[HistoryCommit]] = {}
    for commit in _oldest_first(commits):
        files = _usable_files([commit], matcher)
        if not files:
            continue
        lane = _lane(files)
        scoped = tuple(path for path in files if _lane_key(path) == lane)
        if not scoped:
            continue
        trimmed = HistoryCommit(
            sha=commit.sha,
            title=commit.title,
            author=commit.author,
            committed_at=commit.committed_at,
            files=scoped,
        )
        buckets.setdefault((_period(commit.committed_at), lane), []).append(trimmed)

    clusters: list[ChangeCluster] = []
    for (period, lane), group in buckets.items():
        for chunk in _split(group, MAX_CLUSTER_COMMITS):
            files = _usable_files(chunk, matcher)
            if not files:
                continue
            titles = [item.title for item in chunk if item.title]
            clusters.append(
                ChangeCluster(
                    source_type="commit_cluster",
                    source_id=f"{period}:{lane}:{chunk[-1].sha[:12]}",
                    title=(titles[-1] if titles else lane)[:500],
                    summary="；".join(titles)[:2_000],
                    shas=tuple(item.sha for item in chunk),
                    files=files,
                    before_sha=chunk[0].sha,
                    after_sha=chunk[-1].sha,
                    lane=lane,
                    period=period,
                )
            )
    return clusters


def _split(commits: list[HistoryCommit], size: int) -> list[list[HistoryCommit]]:
    return [commits[index : index + size] for index in range(0, len(commits), size)]


def _period(stamp: datetime) -> str:
    return f"{stamp.year:04d}-{stamp.month:02d}"


def _lane(files: tuple[str, ...]) -> str:
    scores: dict[str, int] = {}
    for path in files:
        key = _lane_key(path)
        if key == "root":
            continue
        scores[key] = scores.get(key, 0) + 1
    if not scores:
        return "root"
    return max(scores, key=scores.get)


def _lane_key(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if not parts:
        return "root"
    index = 0
    while index < len(parts) - 1 and parts[index].lower() in CONTAINER_SEGMENTS:
        index += 1
    if index == 0:
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return parts[index]


def _oldest_first(commits: list[HistoryCommit]) -> list[HistoryCommit]:
    return sorted(commits, key=lambda item: (item.committed_at, item.sha))


def _usable_files(commits: list[HistoryCommit], matcher: IgnoreMatcher) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        for path in commit.files:
            if path in seen or matcher.matches(path):
                continue
            seen.add(path)
            paths.append(path)
    return tuple(paths)
