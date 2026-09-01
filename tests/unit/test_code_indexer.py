from types import SimpleNamespace
from unittest.mock import MagicMock

from packages.code_intelligence.service import CodeIndexer


def test_snapshot_filters_ignored_paths_before_reading_and_skips_binary() -> None:
    class FakeMirror:
        reads: list[str] = []

        def list_files(self, _repository_id: int, _sha: str) -> list[str]:
            return ["assets/logo.png", "assets/icon.svg", "build/app.min.js", "src/app.ts"]

        def read_file(self, _repository_id: int, _sha: str, path: str) -> str:
            self.reads.append(path)
            return "export const ok = 1\n"

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    mirror = FakeMirror()

    result = CodeIndexer(db, mirror).index_snapshot(
        SimpleNamespace(id=1),
        sha="a" * 40,
        ignore_patterns=["**/*.min.js"],
    )

    assert result["ignored_files"] == 3
    assert result["indexed_files"] == 1
    assert mirror.reads == ["src/app.ts"]
