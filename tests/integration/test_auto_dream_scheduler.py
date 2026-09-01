from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

from apps.worker import tasks
from packages.common.models import Job, Memory, Project


def test_nightly_dream_uses_candidates_and_deduplicates_per_day(db, monkeypatch) -> None:
    project = Project(name="nightly")
    db.add(project)
    db.flush()
    db.add(Memory(project_id=project.id, title="待整理", status="candidate"))
    db.commit()
    sent: list[int] = []
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            auto_dream_enabled=True,
            auto_dream_hour=3,
            auto_dream_timezone="Asia/Taipei",
        ),
    )
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(tasks.run_dream, "send", lambda job_id: sent.append(job_id))
    now = datetime(2026, 8, 15, 4, 0, tzinfo=UTC)

    assert tasks.enqueue_nightly_dreams(now) == 1
    assert tasks.enqueue_nightly_dreams(now) == 0
    job = db.query(Job).one()
    assert job.kind == "dream_incremental"
    assert job.checkpoint["automatic"] is True
    assert sent == [job.id]
