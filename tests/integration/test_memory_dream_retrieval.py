from packages.code_intelligence.service import CodeIndexer
from packages.common.models import CodeFile, DreamChange, Memory, Project, Repository
from packages.dreaming.service import DreamService
from packages.evidence.service import EvidenceService
from packages.llm.provider import HeuristicLLMProvider
from packages.memory.service import MemoryService
from packages.retrieval.service import RetrievalService


def _repository(db, project_id: int, name: str) -> Repository:
    repository = Repository(
        project_id=project_id,
        name=name,
        gitlab_project_id=name,
        clone_url=f"https://gitlab.example.com/shop/{name}.git",
        release_branch="main",
    )
    db.add(repository)
    db.flush()
    return repository


def test_repo_a_memory_is_recalled_for_repo_b_without_copying_architecture(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    repo_a = _repository(db, project.id, "repo-a")
    repo_b = _repository(db, project.id, "repo-b")
    CodeIndexer(db).index_snapshot(
        repo_a,
        sha="a" * 40,
        files={"src/upload.py": "def upload():\n    return True\n"},
    )
    CodeIndexer(db).index_snapshot(
        repo_b,
        sha="b" * 40,
        files={"src/upload/index.tsx": "export const Upload = () => null;\n"},
    )
    languages = {
        item.language
        for item in db.query(CodeFile).filter(CodeFile.repository_id.in_([repo_a.id, repo_b.id]))
    }
    assert languages == {"python", "typescript"}

    evidence = EvidenceService(db).create_external_evidence(
        repo_a,
        source_type="gitlab_mr",
        source_id="mr-42",
        title="上传流程统一处理取消和异常",
        summary="上传历史变更验证了状态集中管理。",
        payload={"changed_files": [{"path": "src/upload/service.ts"}]},
        importance_score=0.9,
    )
    EvidenceService(db).create_external_evidence(
        repo_a,
        source_type="gitlab_mr",
        source_id="mr-43",
        title="上传流程补充异常处理",
        summary="补充相同上传服务的异常分支。",
        payload={"changed_files": [{"path": "src/upload/service.ts"}]},
        importance_score=0.7,
    )
    stories = EvidenceService(db).change_stories(project.id)
    assert any(len(story["evidence_ids"]) == 2 for story in stories)
    candidate = EvidenceService(db).candidate_from_evidence(evidence)
    assert candidate is not None
    db.commit()

    run = DreamService(db).run(project.id, dream_type="genesis")
    db.commit()
    assert run.status == "succeeded"
    assert MemoryService(db).get(candidate.id).status == "tentative"
    detail = DreamService(db).detail(run.id)
    assert detail["prompt_version"] == "v3"
    assert detail["changes"]

    context = RetrievalService(db).memory_context(
        project_ref="shop",
        repository_ref="repo-b",
        task="实现上传流程并统一处理取消和异常",
        files=["src/upload/index.ts"],
        session_id="test-session",
    )
    assert context["returned_count"] == 1
    result = context["results"][0]
    assert result["memory_id"] == candidate.id
    assert result["do_not_copy"]
    assert result["action_boundary"]

    repeated = RetrievalService(db).memory_context(
        project_ref="shop",
        repository_ref="repo-b",
        task="实现上传流程并统一处理取消和异常",
        session_id="test-session",
    )
    assert repeated["results"][0]["is_new_in_session"] is False
    assert "facts" not in repeated["results"][0]

    incremental = DreamService(db).run(project.id, dream_type="incremental")
    manual = DreamService(db).run(project.id, dream_type="manual")
    full_validation = DreamService(db).run(project.id, dream_type="full_validation")
    db.commit()
    assert {incremental.status, manual.status, full_validation.status} == {"succeeded"}

    change = db.query(DreamChange).filter(DreamChange.dream_run_id == run.id).first()
    assert change is not None
    reverted = DreamService(db).revert_change(change.id, reason="测试撤回")
    db.commit()
    assert reverted.status == "reverted"
    assert MemoryService(db).get(candidate.id).status == "candidate"


def test_history_extract_stays_tentative_until_review(db) -> None:
    project = Project(name="shop-init")
    db.add(project)
    db.flush()
    repo = _repository(db, project.id, "repo-init")
    evidence = EvidenceService(db).create_external_evidence(
        repo,
        source_type="gitlab_commit",
        source_id="c-1",
        title="上传取消后仍收到进度回调",
        summary="取消后回调仍触发",
        payload={"changed_files": [{"path": "src/upload.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )
    created = EvidenceService(db).candidates_from_evidence([evidence])
    db.commit()
    assert created == 1
    memory = db.query(Memory).filter(Memory.repository_id == repo.id).one()
    assert memory.status == "tentative"


def test_extract_skips_failed_llm_item_without_aborting(db) -> None:
    project = Project(name="shop-llm-fail")
    db.add(project)
    db.flush()
    repo = _repository(db, project.id, "repo-llm-fail")
    good = EvidenceService(db).create_external_evidence(
        repo,
        source_type="gitlab_commit",
        source_id="ok-1",
        title="上传取消后仍收到进度回调",
        summary="取消后回调仍触发",
        payload={"changed_files": [{"path": "src/upload.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )
    bad = EvidenceService(db).create_external_evidence(
        repo,
        source_type="gitlab_commit",
        source_id="bad-1",
        title="鉴权重试要统一出口",
        summary="失败也要有状态",
        payload={"changed_files": [{"path": "src/auth.ts"}], "diff": "--- a\n+++ b\n"},
        importance_score=0.8,
    )

    class FlakyLLM:
        def extract_signals(self, items):
            raise RuntimeError("外部 LLM 调用失败: ValidationError")

        def extract_signal(self, item):
            if "鉴权" in str(item.get("title") or ""):
                raise RuntimeError("外部 LLM 调用失败: ValidationError")
            from packages.llm.provider import HeuristicLLMProvider

            return HeuristicLLMProvider().extract_signal(item)

    created = EvidenceService(db, llm=FlakyLLM()).candidates_from_evidence(
        [good, bad], batch_size=2
    )
    db.commit()
    assert created == 1
    titles = {item.title for item in db.query(Memory).filter(Memory.project_id == project.id)}
    assert "上传取消后仍收到进度回调" in titles


def test_history_extract_uses_default_batch_and_stays_tentative(db) -> None:
    project = Project(name="shop-batch")
    db.add(project)
    db.flush()
    repo = _repository(db, project.id, "repo-batch")
    evidence = [
        EvidenceService(db).create_external_evidence(
            repo,
            source_type="gitlab_commit",
            source_id=f"batch-{index}",
            title=f"上传异常处理 {index}",
            summary="上传失败统一收口",
            payload={"changed_files": [{"path": f"src/upload-{index}.ts"}], "diff": "x"},
            importance_score=0.8,
        )
        for index in range(3)
    ]

    class BatchLLM:
        def __init__(self) -> None:
            self.batch_sizes = []

        def extract_signals(self, items):
            self.batch_sizes.append(len(items))
            return [HeuristicLLMProvider().extract_signal(item) for item in items]

        def extract_signal(self, item):
            return HeuristicLLMProvider().extract_signal(item)

    llm = BatchLLM()
    created = EvidenceService(db, llm=llm).candidates_from_evidence(evidence)
    db.commit()

    assert created == 3
    assert llm.batch_sizes == [3]
    assert {item.status for item in db.query(Memory).all()} == {"tentative"}


def test_incremental_dream_auto_activates_only_high_confidence_candidates(db) -> None:
    statuses: list[str] = []
    for name, score in (("high", 0.9), ("low", 0.5)):
        project = Project(name=f"auto-{name}")
        db.add(project)
        db.flush()
        repository = _repository(db, project.id, f"repo-{name}")
        evidence = EvidenceService(db).create_external_evidence(
            repository,
            source_type="gitlab_commit",
            source_id=f"commit-{name}",
            title=f"{name} confidence candidate",
            summary="真实变更证据",
            payload={"changed_files": [{"path": f"src/{name}.ts"}]},
            importance_score=score,
        )
        candidate = EvidenceService(db).candidate_from_evidence(evidence)
        assert candidate is not None
        DreamService(db).run(project.id, dream_type="incremental")
        statuses.append(MemoryService(db).get(candidate.id).status)

    assert statuses == ["active", "tentative"]


def test_trivial_evidence_is_skipped(db) -> None:
    project = Project(name="shop-skip")
    db.add(project)
    db.flush()
    repo = _repository(db, project.id, "repo-skip")
    evidence = EvidenceService(db).create_external_evidence(
        repo,
        source_type="commit",
        source_id="typo-1",
        title="fix typo in README",
        summary="correct spelling",
        importance_score=0.9,
    )
    assert EvidenceService(db).candidate_from_evidence(evidence) is None


def test_memory_without_evidence_cannot_become_active(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    memory = Memory(
        project_id=project.id,
        title="无来源结论",
        status="tentative",
        confidence=0.9,
        pattern=["不要直接复制架构"],
    )
    db.add(memory)
    db.flush()

    try:
        MemoryService(db).transition(memory.id, "active", reason="test")
    except ValueError as exc:
        assert "Evidence" in str(exc)
    else:
        raise AssertionError("无 Evidence 的 Memory 不应晋升 Active")
