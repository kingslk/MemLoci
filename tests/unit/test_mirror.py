import subprocess
from pathlib import Path

from packages.gitlab.mirror import (
    RepositoryMirror,
    parse_git_progress,
    unquote_git_path,
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_bare_mirror_reads_and_updates_branch_ref(tmp_path: Path) -> None:
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    work.mkdir()
    _git("init", "-b", "master", cwd=work)
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.com", cwd=work)
    (work / "version.txt").write_text("one")
    (work / "images").mkdir()
    (work / "images" / "优惠券图标.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (work / "images" / "说明.txt").write_text("图注", encoding="utf-8")
    _git("add", "version.txt", "images", cwd=work)
    _git("commit", "-m", "first", cwd=work)
    _git("clone", "--bare", str(work), str(remote), cwd=tmp_path)

    mirror = RepositoryMirror(tmp_path / "mirrors")
    assert mirror._git_env().get("GIT_SSL_NO_VERIFY") == "1"
    first = mirror.sync(1, str(remote), "master")
    assert first.rebuilt is True
    assert first.remote_sha == _git("rev-parse", "master", cwd=work)
    listed = mirror.list_files(1, first.remote_sha)
    assert "images/优惠券图标.png" in listed
    assert "images/说明.txt" in listed
    history = mirror.history(1, "master")
    assert any(item.sha == first.remote_sha for item in history)
    assert mirror.read_file(1, first.remote_sha, "version.txt") == "one"
    assert mirror.read_file(1, first.remote_sha, "images/优惠券图标.png") is None
    assert mirror.read_file(1, first.remote_sha, "images/说明.txt") == "图注"
    quoted_image = '"' + "".join(
        chr(byte) if 32 <= byte < 127 and byte not in {0x22, 0x5C} else f"\\{byte:03o}"
        for byte in "images/优惠券图标.png".encode()
    ) + '"'
    assert mirror.read_file(1, first.remote_sha, quoted_image) is None

    _git("remote", "add", "origin", str(remote), cwd=work)
    (work / "version.txt").write_text("two")
    _git("commit", "-am", "second", cwd=work)
    _git("push", "origin", "master", cwd=work)

    second = mirror.sync(1, str(remote), "master")
    assert second.rebuilt is False
    assert second.remote_sha == _git("rev-parse", "master", cwd=work)


def test_unquote_git_path_restores_cjk_filename() -> None:
    path = "images/优惠券图标.png"
    escaped = "".join(
        chr(byte) if 32 <= byte < 127 and byte not in {0x22, 0x5C} else f"\\{byte:03o}"
        for byte in path.encode()
    )
    assert unquote_git_path(f'"{escaped}"') == path


def test_parse_git_progress_maps_clone_phases() -> None:
    assert parse_git_progress("Receiving objects:  45% (123/273)") == ("拉取对象 45%", 0.405)
    assert parse_git_progress("Resolving deltas:  80% (200/250)") == ("解析增量 80%", 0.972)
    assert parse_git_progress("Already up to date.") is None
