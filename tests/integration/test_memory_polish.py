from packages.common.models import Memory, Project, Repository
from packages.dreaming.polish import MemoryPolishService, is_neighbor, token_set
from packages.evidence.service import EvidenceService


def _repository(db, name: str = "backend") -> tuple[Project, Repository]:
    project = Project(name=f"shop-{name}")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name=name,
        gitlab_project_id=name,
        clone_url=f"https://gitlab.example.com/{name}.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()
    return project, repository


def test_neighbor_rule_requires_files_or_strong_terms() -> None:
    assert is_neighbor({"a.ts", "b.ts"}, {"上传", "取消"}, {"a.ts", "b.ts"}, {"上传", "鉴权"})
    assert not is_neighbor({"a.ts"}, {"取消"}, {"b.ts"}, {"重试"})
    assert not is_neighbor({"a.ts", "b.ts"}, {"上传"}, {"a.ts", "b.ts"}, {"支付"})
    assert is_neighbor({"a.ts"}, {"上传", "取消", "鉴权"}, {"a.ts"}, {"上传", "取消", "鉴权"})
    assert "上传" in token_set("上传取消后仍收到进度回调")


def test_polish_merges_file_neighbors_and_skips_strangers(db) -> None:
    project, repository = _repository(db)
    service = EvidenceService(db)
    left = service.create_external_evidence(
        repository,
        source_type="gitlab_commit",
        source_id="c-1",
        title="上传取消后仍收到进度回调",
        summary="取消后回调仍触发",
        payload={"changed_files": [{"path": "src/upload.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )
    right = service.create_external_evidence(
        repository,
        source_type="gitlab_commit",
        source_id="c-2",
        title="上传取消要丢掉过期回调",
        summary="取消后不应再推进度",
        payload={"changed_files": [{"path": "src/upload.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )
    other = service.create_external_evidence(
        repository,
        source_type="gitlab_commit",
        source_id="c-3",
        title="支付回调验签失败要重试",
        summary="验签失败单独处理",
        payload={"changed_files": [{"path": "src/pay.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )
    assert service.candidates_from_evidence([left, right, other], status="active") == 3
    db.commit()

    summary = MemoryPolishService(db).run(project.id)
    db.commit()
    assert summary["pairs"] == 1
    assert summary["merged"] == 1
    active = list(
        db.query(Memory).filter(Memory.project_id == project.id, Memory.status == "active")
    )
    deprecated = list(
        db.query(Memory).filter(Memory.project_id == project.id, Memory.status == "deprecated")
    )
    assert len(active) == 2
    assert len(deprecated) == 1
    titles = " ".join(item.title for item in active)
    assert "支付" in titles or "pay" in titles.lower()
