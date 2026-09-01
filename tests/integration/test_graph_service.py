from packages.code_intelligence.service import CodeIndexer
from packages.common.models import EvidenceFile, Project, Repository
from packages.evidence.service import EvidenceService
from packages.graph.service import GraphService


def test_graph_service_exposes_code_memory_and_combined_views(db) -> None:
    project = Project(name="graph-project")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name="backend",
        gitlab_project_id="1",
        clone_url="https://gitlab.example.com/backend.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()
    CodeIndexer(db).index_snapshot(
        repository,
        sha="a" * 40,
        files={"src/handler.py": "def handle_event():\n    return True\n"},
    )
    evidence = EvidenceService(db).create_external_evidence(
        repository,
        source_type="commit",
        source_id="commit-1",
        title="Fix event handler",
        summary="Keep event handling idempotent.",
        importance_score=0.8,
    )
    EvidenceService(db).candidate_from_evidence(evidence)

    CodeIndexer(db).index_snapshot(
        repository,
        sha="b" * 40,
        files={
            "src/a.py": "def a():\n    return 1\n",
            "src/nested/b.py": "def b():\n    return 2\n",
            "pkg/c.py": "def c():\n    return 3\n",
        },
    )
    db.add(EvidenceFile(evidence_id=evidence.id, path="src/a.py", change_type="modified"))
    db.flush()
    service = GraphService(db)
    code = service.code(repository.id)
    memory = service.memory(project.id)
    combined = service.combined(project.id)
    root = service.code(repository.id, prefix="")
    src = service.code(repository.id, prefix="src")

    assert any(node["kind"] == "code" for node in code["nodes"])
    assert any(node["kind"] == "memory" for node in memory["nodes"])
    assert {node["kind"] for node in combined["nodes"]} >= {"code", "evidence", "memory"}
    assert any(edge["type"] == "derived_from" for edge in combined["edges"])
    assert all(not str(edge["target"]).startswith("name:") for edge in combined["edges"])
    assert any(node["kind"] == "topic" for node in memory["nodes"])
    assert {node["path"] for node in root["nodes"] if node.get("subtype") == "directory"} >= {
        "src",
        "pkg",
    }
    assert any(node.get("path") == "src/a.py" for node in src["nodes"])
    assert src["meta"]["prefix"] == "src"
    assert src["meta"]["file_total"] >= 3
    assert any(node.get("subtype") == "directory" and node["label"] == "仓库根" for node in root["nodes"])

    CodeIndexer(db).index_snapshot(
        repository,
        sha="c" * 40,
        files={"src/a.py": "from helper import run\n\ndef a():\n    return run()\n"},
    )
    file_row = next(node for node in service.code(repository.id, prefix="src")["nodes"] if node.get("path") == "src/a.py")
    file_graph = service.code_file(repository.id, int(str(file_row["id"]).split(":")[1]))
    assert any(node.get("subtype") == "symbol" for node in file_graph["nodes"])
    assert any(edge["type"] in {"imports", "calls", "contains"} for edge in file_graph["edges"])
