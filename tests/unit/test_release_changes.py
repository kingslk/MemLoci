from packages.common.models import Project, Repository
from packages.gitlab.release_changes import normalize_event, persist_release_change


def test_push_is_idempotent(db) -> None:
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
    payload = {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "before": "a" * 40,
        "after": "b" * 40,
        "checkout_sha": "b" * 40,
    }
    normalized = normalize_event(repository, payload, event_type="Push Hook", event_id="event-1")
    assert normalized is not None

    first, created_first = persist_release_change(db, repository, normalized)
    second, created_second = persist_release_change(db, repository, normalized)

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_non_release_branch_is_ignored(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    repository = Repository(
        project_id=project.id,
        name="backend",
        gitlab_project_id="2",
        clone_url="https://gitlab.example.com/shop/backend.git",
        release_branch="production",
    )
    db.add(repository)
    db.flush()

    normalized = normalize_event(
        repository,
        {
            "object_kind": "push",
            "ref": "refs/heads/feature/test",
            "before": "a" * 40,
            "after": "b" * 40,
        },
        event_type="Push Hook",
    )
    assert normalized is None


def test_merge_request_and_push_for_same_commit_share_release_change(db) -> None:
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
    after_sha = "d" * 40
    mr = normalize_event(
        repository,
        {
            "object_kind": "merge_request",
            "object_attributes": {
                "target_branch": "main",
                "state": "merged",
                "action": "merge",
                "merge_commit_sha": after_sha,
                "oldrev": "c" * 40,
            },
        },
        event_type="Merge Request Hook",
    )
    push = normalize_event(
        repository,
        {
            "object_kind": "push",
            "ref": "refs/heads/main",
            "before": "c" * 40,
            "after": after_sha,
        },
        event_type="Push Hook",
    )
    assert mr is not None and push is not None

    first, first_created = persist_release_change(db, repository, mr)
    second, second_created = persist_release_change(db, repository, push)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert "mr_merge" in first.payload["source_types"]
    assert "direct_push" in first.payload["source_types"]
