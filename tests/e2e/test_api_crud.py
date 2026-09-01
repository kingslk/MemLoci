import json

from fastapi.testclient import TestClient

import apps.api.main as api_main
from apps.api.main import app
from packages.common.db import get_db


def test_project_repository_crud_and_ignore_preview(db, monkeypatch) -> None:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            unauthorized = client.post("/api/v1/projects", json={"name": "shop"})
            assert unauthorized.status_code == 401

            headers = {"X-Admin-Token": "change-me"}
            project_response = client.post(
                "/api/v1/projects",
                headers=headers,
                json={"name": "shop", "description": "test project"},
            )
            assert project_response.status_code == 201
            project_id = project_response.json()["id"]

            renamed = client.put(
                f"/api/v1/projects/{project_id}",
                headers=headers,
                json={"name": "shop-renamed", "description": "renamed project"},
            )
            assert renamed.status_code == 200
            assert renamed.json()["name"] == "shop-renamed"

            repository_response = client.post(
                f"/api/v1/projects/{project_id}/repositories",
                headers=headers,
                json={
                    "name": "frontend",
                    "gitlab_project_id": "1",
                    "clone_url": "https://gitlab.example.com/shop/frontend.git",
                    "release_branch": "main",
                    "ignore": ["dist/**", "**/*.min.js"],
                },
            )
            assert repository_response.status_code == 201
            repository_id = repository_response.json()["id"]

            monkeypatch.setattr(api_main, "_enqueue_history_sync", lambda _id, _limit: True)
            monkeypatch.setattr(api_main, "_enqueue_mirror_sync", lambda _id: True)
            sync_job = client.post(
                f"/api/v1/repositories/{repository_id}/sync",
                headers=headers,
            )
            assert sync_job.status_code == 202
            assert sync_job.json()["kind"] == "mirror_sync"
            assert sync_job.json()["current_stage"] == "等待同步代码仓库"
            history_job = client.post(
                f"/api/v1/repositories/{repository_id}/history-sync",
                headers=headers,
            )
            assert history_job.status_code == 202
            assert history_job.json()["kind"] == "history_sync"
            assert history_job.json()["status"] == "queued"
            all_jobs = client.get(f"/api/v1/projects/{project_id}/jobs")
            assert all_jobs.status_code == 200
            job_ids = {item["id"] for item in all_jobs.json()}
            assert sync_job.json()["id"] in job_ids
            assert history_job.json()["id"] in job_ids
            assert client.get("/api/v1/jobs").status_code == 401
            global_jobs = client.get("/api/v1/jobs", headers=headers)
            assert global_jobs.status_code == 200
            assert {item["id"] for item in global_jobs.json()} == job_ids
            global_stream = client.get("/api/v1/jobs/stream?once=1", headers=headers)
            assert global_stream.status_code == 200
            global_data = next(
                line for line in global_stream.text.splitlines() if line.startswith("data:")
            )
            assert {item["id"] for item in json.loads(global_data[5:].strip())} == job_ids
            stream = client.get(f"/api/v1/projects/{project_id}/jobs/stream?once=1")
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            data_line = next(
                line for line in stream.text.splitlines() if line.startswith("data:")
            )
            payload = json.loads(data_line[5:].strip())
            assert {item["id"] for item in payload} == job_ids

            preview = client.get(
                f"/api/v1/repositories/{repository_id}/ignore-preview",
                params=[("paths", "dist/bundle.js"), ("paths", "src/app.ts")],
            )
            assert preview.status_code == 200
            assert preview.json()["excluded"] == ["dist/bundle.js"]
            assert preview.json()["included"] == ["src/app.ts"]

            initialization = client.post(
                f"/api/v1/initializations?project_id={project_id}",
                headers=headers,
                json={},
            )
            assert initialization.status_code == 201
            job_id = initialization.json()["id"]
            jobs = client.get(f"/api/v1/projects/{project_id}/initializations")
            assert jobs.status_code == 200
            assert jobs.json()[0]["id"] == job_id
            steps = client.get(f"/api/v1/jobs/{job_id}/steps")
            assert steps.status_code == 200
            assert len(steps.json()) == 4
            cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            deleted = client.delete(f"/api/v1/jobs/{job_id}", headers=headers)
            assert deleted.status_code == 204
            missing = client.get(f"/api/v1/jobs/{job_id}")
            assert missing.status_code == 404

            memory_graph = client.get(f"/api/v1/projects/{project_id}/graphs/memory")
            assert memory_graph.status_code == 200
            assert memory_graph.json()["nodes"] == []
            assert memory_graph.json()["edges"] == []

            memories = client.get(f"/api/v1/projects/{project_id}/memories")
            assert memories.status_code == 200
            assert memories.json()["items"] == []
            assert memories.json()["total"] == 0
            evidence = client.get(f"/api/v1/projects/{project_id}/evidence")
            assert evidence.status_code == 200
            assert evidence.json()["items"] == []
            dreams = client.get(f"/api/v1/projects/{project_id}/dreams")
            assert dreams.status_code == 200
            assert dreams.json()["items"] == []
    finally:
        app.dependency_overrides.clear()
