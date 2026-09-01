from datetime import UTC, datetime, timedelta

from packages.gitlab.clusters import MAX_CLUSTER_COMMITS, HistoryCommit, build_clusters
from packages.gitlab.ignore import IgnoreMatcher


def _commit(sha: str, day: int, files: list[str], title: str = "wip") -> HistoryCommit:
    return HistoryCommit(
        sha=sha,
        title=title,
        author="dev",
        committed_at=datetime(2024, 3, 1, tzinfo=UTC) + timedelta(days=day),
        files=tuple(files),
    )


def test_clusters_by_month_and_module_and_drops_images() -> None:
    matcher = IgnoreMatcher(["dist/**"])
    commits = [
        _commit("a1", 0, ["src/upload/a.ts"]),
        _commit("a2", 1, ["src/upload/b.ts", "assets/logo.png"]),
        _commit("b1", 2, ["src/auth/token.ts"]),
        _commit("img", 3, ["assets/icon.svg"]),
        _commit("dist", 3, ["dist/bundle.js"]),
        _commit("later", 40, ["src/upload/c.ts"]),
    ]
    clusters = build_clusters(commits, matcher=matcher)
    lanes = {item.lane for item in clusters}
    periods = {item.period for item in clusters}
    files = {path for item in clusters for path in item.files}
    assert "src/upload" in lanes
    assert "src/auth" in lanes
    assert "2024-03" in periods
    assert "2024-04" in periods
    assert "assets/logo.png" not in files
    assert "assets/icon.svg" not in files
    assert "dist/bundle.js" not in files
    assert all(item.files for item in clusters)


def test_large_same_lane_month_is_split() -> None:
    matcher = IgnoreMatcher([])
    commits = [
        _commit(f"c{index}", 0, ["pkg/app/file.ts"], title=f"n{index}")
        for index in range(MAX_CLUSTER_COMMITS + 5)
    ]
    clusters = build_clusters(commits, matcher=matcher)
    assert len(clusters) == 2
    assert sum(len(item.shas) for item in clusters) == MAX_CLUSTER_COMMITS + 5


def test_skips_monorepo_containers_and_keeps_shallow_lanes() -> None:
    matcher = IgnoreMatcher([])
    commits = [
        _commit("ys1", 0, ["web/packages/service/tool/yuanshen/page.tsx"]),
        _commit("ys2", 1, ["web/packages/app/tool/yuanshen/map/index.ts"]),
        _commit("zzz", 2, ["web/packages/service/tool/zzz/gacha.ts"]),
        _commit("sdk", 3, ["mobile/packages/app/coupon.ts"]),
        _commit("build", 4, ["web/packages/app/release/tool/index.js"]),
        _commit("next", 4, ["web/release/.next/cache/chunk.js"]),
    ]
    clusters = build_clusters(commits, matcher=matcher)
    lanes = {item.lane for item in clusters}
    files = {path for item in clusters for path in item.files}
    assert "yuanshen" in lanes
    assert "zzz" in lanes
    assert "mobile/packages" in lanes
    assert "web/packages" not in lanes
    assert "src/upload" not in lanes
    assert not any("release" in path or ".next" in path for path in files)
    yuanshen = next(item for item in clusters if item.lane == "yuanshen")
    assert len(yuanshen.shas) == 2


def test_shallow_src_lane_still_uses_first_two_segments() -> None:
    matcher = IgnoreMatcher([])
    commits = [_commit("up", 0, ["src/upload/a.ts"])]
    clusters = build_clusters(commits, matcher=matcher)
    assert [item.lane for item in clusters] == ["src/upload"]
