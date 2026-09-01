from fastapi.testclient import TestClient

import apps.api.main as api_main
from apps.api.main import app
from packages.common.config import get_settings
from packages.common.db import get_db
from packages.common.models import Job, Project


def test_run_endpoint_only_enqueues_memory_polish(db, monkeypatch) -> None:
    def override_db():
        yield db

    project = Project(name="dispatch")
    db.add(project)
    db.flush()
    job = Job(project_id=project.id, kind="memory_polish", status="paused")
    db.add(job)
    db.commit()
    calls: list[int] = []
    monkeypatch.setattr(
        api_main,
        "_enqueue_memory_polish",
        lambda job_id: calls.append(job_id) or True,
    )

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/jobs/{job.id}/run",
                headers={"X-Admin-Token": get_settings().admin_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert calls == [job.id]


def test_dream_endpoint_creates_job_without_running_llm_in_api(db, monkeypatch) -> None:
    def override_db():
        yield db

    project = Project(name="dream-dispatch")
    db.add(project)
    db.commit()
    calls: list[int] = []
    monkeypatch.setattr(api_main, "_enqueue_dream", lambda job_id: calls.append(job_id) or True)

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/dreams",
                headers={"X-Admin-Token": get_settings().admin_token},
                json={"project_id": project.id, "dream_type": "incremental"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["kind"] == "dream_incremental"
    assert response.json()["status"] == "queued"
    assert calls == [response.json()["id"]]
