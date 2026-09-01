from packages.code_intelligence.service import CodeIndexer, CodeQueryService
from packages.common.models import CodeFile, Project, Repository


def test_code_index_and_trace_marks_static_uncertainty(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name="frontend",
        gitlab_project_id="1",
        clone_url="https://gitlab.example.com/shop/frontend.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()

    result = CodeIndexer(db).index_snapshot(
        repository,
        sha="c" * 40,
        files={
            "src/upload.py": """
def send():
    return True

def upload():
    return send()
""",
            "dist/generated.py": "def ignored():\n    return False\n",
        },
        ignore_patterns=["dist/**"],
    )

    assert result == {"indexed_files": 1, "ignored_files": 1, "symbols": 2}
    code_file = db.query(CodeFile).one()
    assert code_file.embedding_provider == "hash"
    assert code_file.embedding_dimensions == 384
    search = CodeQueryService(db).search(repository.id, "upload")
    hits = search["results"]
    assert search["snapshot_sha"] == "c" * 40
    assert any(item["kind"] == "symbol" and item["name"] == "upload" for item in hits)
    trace = CodeQueryService(db).trace(repository.id, "upload", "send")
    assert trace["found"] is True
    assert trace["uncertainty"] == "contains_inferred_relation"


def test_incremental_update_removes_deleted_symbols(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name="frontend",
        gitlab_project_id="1",
        clone_url="https://gitlab.example.com/shop/frontend.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()

    CodeIndexer(db).index_snapshot(
        repository,
        sha="a" * 40,
        files={"src/old.py": "def old_symbol():\n    return True\n"},
    )

    class FakeMirror:
        def read_file(self, _repository_id: int, _sha: str, _path: str) -> str:
            return "def new_symbol():\n    return True\n"

    result = CodeIndexer(db, FakeMirror()).incremental_update(
        repository,
        sha="b" * 40,
        changed_files=[
            {"status": "D", "path": "src/old.py"},
            {"status": "A", "path": "src/new.py"},
        ],
    )

    assert result["deleted_files"] == 1
    assert CodeQueryService(db).context(repository.id, "old_symbol")["found"] is False
    assert CodeQueryService(db).context(repository.id, "new_symbol")["found"] is True


def test_code_search_tokenizes_and_returns_envelope(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name="frontend",
        gitlab_project_id="1",
        clone_url="https://gitlab.example.com/shop/frontend.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()
    CodeIndexer(db).index_snapshot(
        repository,
        sha="d" * 40,
        files={
            "src/components/input/index.tsx": (
                "export const Input: FC<Props> = forwardRef((props, ref) => {\n"
                "  return null\n"
                "})\n"
            )
        },
    )
    empty = CodeQueryService(db).search(repository.id, "WKWebView IME 输不出字")
    assert empty["results"] == []
    assert empty["hint"]
    assert empty["snapshot_sha"] == "d" * 40
    hits = CodeQueryService(db).search(repository.id, "Input")
    assert hits["count"] >= 1
    context = CodeQueryService(db).context(repository.id, "Input")
    assert context["found"] is True
    assert context["symbol"]["path"] == "src/components/input/index.tsx"
    assert context["symbol"]["snippet"]
