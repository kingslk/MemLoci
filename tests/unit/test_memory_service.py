from types import SimpleNamespace
from unittest.mock import MagicMock

from packages.embeddings.provider import HashEmbeddingProvider
from packages.memory.service import MemoryService


def test_human_correction_refreshes_retrieval_embedding() -> None:
    memory = SimpleNamespace(
        id=7,
        project_id=1,
        title="支付回调处理",
        status="tentative",
        confidence=0.6,
        problem="旧问题",
        pattern=["旧做法"],
        implementation={},
        do_not_copy=[],
        apply_when=["旧场景"],
        do_not=[],
        scope={},
        embedding=HashEmbeddingProvider().embed_query("旧内容"),
        embedding_provider="hash",
        embedding_model="hash-384",
        embedding_dimensions=384,
        embedding_version="v1",
        version=2,
    )
    db = MagicMock()
    service = MemoryService(db)
    service.get = MagicMock(return_value=memory)
    previous_embedding = memory.embedding

    service.correct(
        memory.id,
        {"problem": "新问题", "pattern": ["新做法"], "apply_when": ["新场景"]},
        reason="人工确认内容",
    )

    assert memory.embedding != previous_embedding
    assert memory.embedding == HashEmbeddingProvider().embed_query(
        "支付回调处理 新问题 新做法 新场景"
    )
    assert memory.version == 3
    assert db.flush.called


def test_sqlalchemy_models_carry_table_and_column_comments() -> None:
    from packages.common.models import Evidence, Memory, MemoryEvidence

    assert Memory.__table__.comment
    assert Memory.__table__.c.status.comment
    assert Evidence.__table__.comment
    assert MemoryEvidence.__table__.comment
    assert "memory_relations" not in {table.name for table in Memory.metadata.tables.values()}


def test_candidate_can_become_active_when_evidence_exists() -> None:
    from packages.memory.service import ALLOWED_TRANSITIONS

    assert "active" in ALLOWED_TRANSITIONS["candidate"]


def test_batch_correct_reports_per_item(db) -> None:
    from packages.common.models import Memory, Project
    from packages.memory.service import MemoryService

    project = Project(name="review-batch")
    db.add(project)
    db.flush()
    first = Memory(
        project_id=project.id,
        title="有证据才能启用",
        status="candidate",
        problem="x",
    )
    second = Memory(
        project_id=project.id,
        title="可以先试用",
        status="candidate",
        problem="y",
    )
    db.add_all([first, second])
    db.flush()
    results = MemoryService(db).batch_correct(
        [first.id, second.id],
        {"status": "active"},
        reason="批量启用",
    )
    assert results[0]["ok"] is False
    assert "Evidence" in results[0]["error"]
    assert results[1]["ok"] is False
    trial = MemoryService(db).batch_correct(
        [second.id],
        {"status": "tentative"},
        reason="批量试用",
    )
    assert trial == [{"id": second.id, "ok": True, "status": "tentative"}]
