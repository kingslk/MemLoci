"""MemLoci Core API。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.mcp.server import MCPHttpApp, create_session_manager
from apps.mcp.server import mcp as mcp_server
from packages.code_intelligence.service import CodeIndexer, CodeQueryService
from packages.common.audit import record_audit
from packages.common.config import get_settings
from packages.common.db import SessionLocal, get_db
from packages.common.jobs import reclaim_orphaned_jobs
from packages.common.models import (
    DreamRun,
    Job,
    JobStep,
    Memory,
    Project,
    ReleaseChange,
    Repository,
    RepositoryIgnoreRule,
    RepositorySyncState,
)
from packages.common.schemas import (
    AgentContextRequest,
    CodeSearchRequest,
    DreamCreate,
    DreamDetailRead,
    DreamRead,
    EvidenceDetailRead,
    GraphRead,
    IgnorePreviewRead,
    JobRead,
    JobStepRead,
    MemoryBatchCorrection,
    MemoryCorrection,
    MemoryRead,
    Page,
    ProjectCreate,
    ProjectRead,
    ReleaseChangeRead,
    RepositoryCreate,
    RepositoryRead,
    TopicRead,
)
from packages.common.security import (
    RequireMCPToken,
    require_admin,
    require_mcp,
    safe_exception_message,
)
from packages.dreaming.service import DreamService
from packages.evidence.service import EvidenceService
from packages.gitlab.client import GitLabClient
from packages.gitlab.ignore import IgnoreMatcher
from packages.gitlab.mirror import RepositoryMirror
from packages.gitlab.release_changes import normalize_event, persist_release_change
from packages.gitlab.sync import RepositorySyncService
from packages.gitlab.webhook import (
    WebhookError,
    event_id_from_headers,
    parse_payload,
)
from packages.graph.service import GraphService
from packages.initialization.service import InitializationService
from packages.memory.service import MemoryService
from packages.retrieval.service import RetrievalService


class CodeIndexRequest(BaseModel):
    sha: str = Field(min_length=7, max_length=128)
    files: dict[str, str] | None = None


class CodeContextRequest(BaseModel):
    repository_id: int
    symbol: str = Field(min_length=1)


class CodeTraceRequest(BaseModel):
    repository_id: int
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    max_depth: int = Field(default=4, ge=1, le=8)


class InitializeRequest(BaseModel):
    repository_id: int | None = None


def create_app() -> FastAPI:
    settings = get_settings()
    mcp_http = MCPHttpApp()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with SessionLocal() as db:
            reclaim_orphaned_jobs(db, interrupt_running=False)
        if mcp_server is None:
            yield
            return
        manager = create_session_manager()
        mcp_http.bind(manager)
        try:
            async with manager.run():
                yield
        finally:
            mcp_http.unbind()

    application = FastAPI(
        title="MemLoci Core API",
        version="0.1.0",
        description="GitLab 代码、Evidence、Memory、Dreaming 与 Agent Serving API",
        lifespan=lifespan,
    )
    if mcp_server is not None:
        application.mount("/mcp", RequireMCPToken(mcp_http))
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready(db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(status_code=503, detail="数据库不可用") from None
        return {
            "status": "ok",
            "database": "ok",
            "redis": "not_checked",
            "gitlab": "checked_on_demand",
        }

    @application.post(
        "/api/v1/projects",
        response_model=ProjectRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
        project = Project(name=payload.name, description=payload.description)
        db.add(project)
        try:
            db.commit()
            db.refresh(project)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Project 名称已存在") from None
        record_audit(
            db,
            action="project_created",
            entity_type="project",
            entity_id=project.id,
            project_id=project.id,
            actor="admin",
        )
        db.commit()
        return project

    @application.get("/api/v1/projects", response_model=list[ProjectRead])
    def list_projects(db: Session = Depends(get_db)) -> list[Project]:
        return list(db.scalars(select(Project).order_by(Project.name)).all())

    @application.get("/api/v1/projects/{project_id}", response_model=ProjectRead)
    def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
        return _required(db.get(Project, project_id), "Project 不存在")

    @application.put(
        "/api/v1/projects/{project_id}",
        response_model=ProjectRead,
        dependencies=[Depends(require_admin)],
    )
    def update_project(
        project_id: int, payload: ProjectCreate, db: Session = Depends(get_db)
    ) -> Project:
        project = _required(db.get(Project, project_id), "Project 不存在")
        before = {"name": project.name, "description": project.description}
        project.name = payload.name
        project.description = payload.description
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Project 名称已存在") from None
        record_audit(
            db,
            action="project_updated",
            entity_type="project",
            entity_id=project.id,
            project_id=project.id,
            actor="admin",
            before=before,
            after={"name": project.name, "description": project.description},
        )
        db.commit()
        db.refresh(project)
        return project

    @application.delete(
        "/api/v1/projects/{project_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    def delete_project(project_id: int, db: Session = Depends(get_db)) -> None:
        project = _required(db.get(Project, project_id), "Project 不存在")
        db.delete(project)
        db.commit()

    @application.post(
        "/api/v1/projects/{project_id}/repositories",
        response_model=RepositoryRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_repository(
        project_id: int, payload: RepositoryCreate, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        _validate_clone_url(payload.clone_url)
        repository = Repository(
            project_id=project_id,
            name=payload.name,
            gitlab_project_id=payload.gitlab_project_id,
            clone_url=payload.clone_url,
            release_branch=payload.release_branch,
        )
        db.add(repository)
        try:
            db.flush()
            for position, pattern in enumerate(payload.ignore):
                db.add(
                    RepositoryIgnoreRule(
                        repository_id=repository.id, pattern=pattern, position=position
                    )
                )
            db.add(RepositorySyncState(repository_id=repository.id))
            record_audit(
                db,
                action="repository_created",
                entity_type="repository",
                entity_id=repository.id,
                project_id=project_id,
                actor="admin",
                after={
                    "release_branch": repository.release_branch,
                    "ignore_count": len(payload.ignore),
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Repository 名称已存在") from None
        return _repository_payload(repository, db)

    @application.get(
        "/api/v1/projects/{project_id}/repositories", response_model=list[RepositoryRead]
    )
    def list_repositories(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
        _required(db.get(Project, project_id), "Project 不存在")
        repositories = db.scalars(
            select(Repository).where(Repository.project_id == project_id).order_by(Repository.name)
        ).all()
        return [_repository_payload(item, db) for item in repositories]

    @application.get("/api/v1/repositories/{repository_id}", response_model=RepositoryRead)
    def get_repository(repository_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        return _repository_payload(
            _required(db.get(Repository, repository_id), "Repository 不存在"), db
        )

    @application.put(
        "/api/v1/repositories/{repository_id}",
        response_model=RepositoryRead,
        dependencies=[Depends(require_admin)],
    )
    def update_repository(
        repository_id: int, payload: RepositoryCreate, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        _validate_clone_url(payload.clone_url)
        repository.name = payload.name
        repository.gitlab_project_id = payload.gitlab_project_id
        repository.clone_url = payload.clone_url
        repository.release_branch = payload.release_branch
        db.query(RepositoryIgnoreRule).filter(
            RepositoryIgnoreRule.repository_id == repository.id
        ).delete()
        for position, pattern in enumerate(payload.ignore):
            db.add(
                RepositoryIgnoreRule(
                    repository_id=repository.id, pattern=pattern, position=position
                )
            )
        record_audit(
            db,
            action="repository_updated",
            entity_type="repository",
            entity_id=repository.id,
            project_id=repository.project_id,
            actor="admin",
            after={
                "release_branch": repository.release_branch,
                "ignore_count": len(payload.ignore),
            },
        )
        db.commit()
        return _repository_payload(repository, db)

    @application.delete(
        "/api/v1/repositories/{repository_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    def delete_repository(repository_id: int, db: Session = Depends(get_db)) -> None:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        db.delete(repository)
        db.commit()

    @application.get(
        "/api/v1/repositories/{repository_id}/ignore-preview", response_model=IgnorePreviewRead
    )
    def ignore_preview(
        repository_id: int,
        sha: str = Query(default="working-tree"),
        paths: list[str] = Query(default=[]),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        patterns = _ignore_patterns(repository.id, db)
        included, excluded = IgnoreMatcher(patterns).filter_paths(paths)
        return {"sha": sha, "included": included, "excluded": excluded}

    @application.post(
        "/api/v1/repositories/{repository_id}/connection-test",
        dependencies=[Depends(require_admin)],
    )
    def connection_test(repository_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        _required(db.get(Repository, repository_id), "Repository 不存在")
        settings = get_settings()
        with GitLabClient(
            settings.gitlab_base_url,
            settings.gitlab_token,
            verify=settings.gitlab_ssl_verify,
        ) as client:
            try:
                return client.connection_test()
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=safe_exception_message(exc)) from None

    @application.post(
        "/api/v1/repositories/{repository_id}/sync",
        response_model=JobRead,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    def sync_repository(repository_id: int, db: Session = Depends(get_db)) -> Job:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        job = Job(
            project_id=repository.project_id,
            repository_id=repository.id,
            kind="mirror_sync",
            status="queued",
            current_stage="等待同步代码仓库",
            checkpoint={},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if not _enqueue_mirror_sync(job.id):
            job.status = "failed"
            job.error = "后台任务队列不可用，请检查 Redis 和 Worker"
            db.commit()
        return job

    @application.post(
        "/api/v1/repositories/{repository_id}/reconcile",
        dependencies=[Depends(require_admin)],
    )
    def reconcile_repository(repository_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        settings = get_settings()
        with GitLabClient(
            settings.gitlab_base_url,
            settings.gitlab_token,
            verify=settings.gitlab_ssl_verify,
        ) as client:
            try:
                change_id, created = RepositorySyncService(
                    db,
                    client,
                    RepositoryMirror(
                        settings.mirror_root,
                        token=settings.gitlab_token,
                        ssl_verify=settings.gitlab_ssl_verify,
                    ),
                ).reconcile(repository)
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=safe_exception_message(exc)) from None
        if created and change_id:
            _enqueue_release_change(change_id)
        return {"release_change_id": change_id, "created": created}

    @application.post(
        "/api/v1/repositories/{repository_id}/history-sync",
        response_model=JobRead,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    def history_sync(
        repository_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        db: Session = Depends(get_db),
    ) -> Job:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        job = Job(
            project_id=repository.project_id,
            repository_id=repository.id,
            kind="history_sync",
            status="queued",
            current_stage="等待后台执行",
            checkpoint={"limit": limit},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if not _enqueue_history_sync(job.id, limit):
            job.status = "failed"
            job.error = "后台任务队列不可用，请检查 Redis 和 Worker"
            db.commit()
        return job

    @application.post("/api/v1/webhooks/gitlab/{repository_id}")
    async def gitlab_webhook(
        repository_id: int, request: Request, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        """快速校验并落库 ReleaseChange，重分析交给 Worker，避免 GitLab 重试超时。"""
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        settings = get_settings()
        if int(request.headers.get("content-length", "0") or 0) > settings.webhook_max_bytes:
            raise HTTPException(status_code=413, detail="Webhook Payload 过大")
        raw_body = await request.body()
        if len(raw_body) > settings.webhook_max_bytes:
            raise HTTPException(status_code=413, detail="Webhook Payload 过大")
        try:
            payload = parse_payload(
                raw_body,
                configured_secret=settings.gitlab_webhook_secret,
                received_secret=request.headers.get("X-Gitlab-Token"),
                expected_project_id=repository.gitlab_project_id,
            )
            normalized = normalize_event(
                repository,
                payload,
                event_type=request.headers.get(
                    "X-Gitlab-Event", str(payload.get("object_kind", ""))
                ),
                event_id=event_id_from_headers(dict(request.headers)),
            )
        except WebhookError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
        if normalized is None:
            return {
                "accepted": True,
                "release_change_id": None,
                "reason": "非 release branch 或无法确定正式变化",
            }
        change, created = persist_release_change(db, repository, normalized)
        db.commit()
        if created:
            _enqueue_release_change(change.id)
        return {"accepted": True, "release_change_id": change.id, "created": created}

    @application.get(
        "/api/v1/repositories/{repository_id}/release-changes",
        response_model=list[ReleaseChangeRead],
    )
    def list_release_changes(
        repository_id: int, db: Session = Depends(get_db)
    ) -> list[ReleaseChange]:
        _required(db.get(Repository, repository_id), "Repository 不存在")
        return list(
            db.scalars(
                select(ReleaseChange)
                .where(ReleaseChange.repository_id == repository_id)
                .order_by(ReleaseChange.occurred_at.desc())
            ).all()
        )

    @application.get("/api/v1/projects/{project_id}/evidence", response_model=Page)
    def list_evidence(
        project_id: int,
        repository_id: int | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        offset = (page - 1) * page_size
        service = EvidenceService(db)
        items = service.list_project(
            project_id, repository_id=repository_id, offset=offset, limit=page_size
        )
        return {
            "items": [
                {
                    "id": item.id,
                    "repository_id": item.repository_id,
                    "release_change_id": item.release_change_id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "title": item.title,
                    "summary": item.summary,
                    "importance_score": item.importance_score,
                    "payload": item.payload,
                }
                for item in items
            ],
            "total": service.count_project(project_id, repository_id=repository_id),
            "page": page,
            "page_size": page_size,
        }

    @application.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDetailRead)
    def get_evidence_detail(evidence_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return EvidenceService(db).detail(evidence_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get("/api/v1/projects/{project_id}/change-stories")
    def list_change_stories(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
        _required(db.get(Project, project_id), "Project 不存在")
        return EvidenceService(db).change_stories(project_id)

    @application.post(
        "/api/v1/repositories/{repository_id}/code/index",
        dependencies=[Depends(require_admin)],
    )
    def index_code(
        repository_id: int, payload: CodeIndexRequest, db: Session = Depends(get_db)
    ) -> dict[str, int]:
        repository = _required(db.get(Repository, repository_id), "Repository 不存在")
        return CodeIndexer(db).index_snapshot(
            repository,
            sha=payload.sha,
            files=payload.files,
            ignore_patterns=_ignore_patterns(repository.id, db),
        )

    @application.post("/api/v1/code/search")
    def code_search(
        payload: CodeSearchRequest, db: Session = Depends(get_db)
    ) -> dict[str, object]:
        return CodeQueryService(db).search(payload.repository_id, payload.query, payload.limit)

    @application.post("/api/v1/code/context")
    def code_context(
        payload: CodeContextRequest, db: Session = Depends(get_db)
    ) -> dict[str, object]:
        return CodeQueryService(db).context(payload.repository_id, payload.symbol)

    @application.post("/api/v1/code/trace")
    def code_trace(payload: CodeTraceRequest, db: Session = Depends(get_db)) -> dict[str, object]:
        return CodeQueryService(db).trace(
            payload.repository_id, payload.source, payload.target, payload.max_depth
        )

    @application.get("/api/v1/repositories/{repository_id}/code/graph", response_model=GraphRead)
    def code_graph(
        repository_id: int,
        limit: int = Query(default=200, ge=1, le=2_000),
        prefix: str | None = None,
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Repository, repository_id), "Repository 不存在")
        return GraphService(db).code(repository_id, limit=limit, prefix=prefix or "")

    @application.get("/api/v1/projects/{project_id}/graphs/{graph_kind}", response_model=GraphRead)
    def project_graph(
        project_id: int,
        graph_kind: str,
        repository_id: int | None = None,
        limit: int = Query(default=200, ge=1, le=2_000),
        focus: str | None = None,
        prefix: str | None = None,
        file_id: int | None = None,
        hops: int = Query(default=2, ge=1, le=3),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        if graph_kind not in {"code", "memory", "combined", "neighborhood"}:
            raise HTTPException(
                status_code=400,
                detail="Graph 类型必须是 code、memory、combined 或 neighborhood",
            )
        if graph_kind == "code":
            if repository_id is None:
                raise HTTPException(status_code=400, detail="Code Graph 需要 repository_id")
            repository = _required(db.get(Repository, repository_id), "Repository 不存在")
            if repository.project_id != project_id:
                raise HTTPException(status_code=400, detail="Repository 不属于 Project")
            if file_id is not None:
                return GraphService(db).code_file(repository_id, file_id)
            return GraphService(db).code(repository_id, limit=limit, prefix=prefix or "")
        if repository_id is not None:
            repository = _required(db.get(Repository, repository_id), "Repository 不存在")
            if repository.project_id != project_id:
                raise HTTPException(status_code=400, detail="Repository 不属于 Project")
        if graph_kind == "memory":
            return GraphService(db).memory(project_id, repository_id=repository_id, limit=limit)
        return GraphService(db).combined(
            project_id,
            repository_id=repository_id,
            limit=limit,
            focus=focus,
            hops=hops,
        )

    @application.get("/api/v1/projects/{project_id}/memories", response_model=Page)
    def list_memories(
        project_id: int,
        repository_id: int | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        topic_id: int | None = None,
        q: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        service = MemoryService(db)
        offset = (page - 1) * page_size
        return {
            "items": service.list_items(
                project_id,
                repository_id=repository_id,
                status=status_filter,
                topic_id=topic_id,
                q=q,
                offset=offset,
                limit=page_size,
            ),
            "total": service.count(
                project_id,
                repository_id=repository_id,
                status=status_filter,
                topic_id=topic_id,
                q=q,
            ),
            "page": page,
            "page_size": page_size,
        }

    @application.get("/api/v1/projects/{project_id}/topics", response_model=list[TopicRead])
    def list_topics(project_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
        _required(db.get(Project, project_id), "Project 不存在")
        return MemoryService(db).list_topics(project_id)

    @application.post(
        "/api/v1/memories/batch-correct",
        dependencies=[Depends(require_admin)],
    )
    def batch_correct_memories(
        payload: MemoryBatchCorrection, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        results = MemoryService(db).batch_correct(
            payload.memory_ids,
            {"status": payload.status},
            reason=payload.reason,
        )
        db.commit()
        return {"results": results}

    @application.get("/api/v1/projects/{project_id}/query-logs", response_model=Page)
    def list_query_logs(
        project_id: int,
        recall_mode: str | None = None,
        primary_switched: bool | None = None,
        session_id: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        items, total = RetrievalService(db).list_query_logs(
            project_id,
            recall_mode=recall_mode,
            primary_switched=primary_switched,
            session_id=session_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @application.delete(
        "/api/v1/projects/{project_id}/query-logs",
        dependencies=[Depends(require_admin)],
    )
    def clear_query_logs(project_id: int, db: Session = Depends(get_db)) -> dict[str, int]:
        _required(db.get(Project, project_id), "Project 不存在")
        deleted = RetrievalService(db).clear_query_logs(project_id)
        db.commit()
        return {"deleted": deleted}

    @application.get("/api/v1/memories/{memory_id}", response_model=dict[str, Any])
    def memory_detail(memory_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return MemoryService(db).detail(memory_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.patch(
        "/api/v1/memories/{memory_id}",
        response_model=MemoryRead,
        dependencies=[Depends(require_admin)],
    )
    def correct_memory(
        memory_id: int, payload: MemoryCorrection, db: Session = Depends(get_db)
    ) -> Memory:
        changes = payload.model_dump(exclude={"reason"}, exclude_none=True)
        try:
            memory = MemoryService(db).correct(memory_id, changes, reason=payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        db.commit()
        return memory

    @application.post(
        "/api/v1/dreams",
        response_model=JobRead,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    def create_dream(payload: DreamCreate, db: Session = Depends(get_db)) -> Job:
        _required(db.get(Project, payload.project_id), "Project 不存在")
        job = Job(
            project_id=payload.project_id,
            kind=f"dream_{payload.dream_type}",
            status="queued",
            current_stage="等待整理记忆",
            checkpoint={"dream_type": payload.dream_type},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if not _enqueue_dream(job.id):
            job.status = "failed"
            job.error = "后台任务队列不可用，请检查 Redis 和 Worker"
            db.commit()
        return job

    @application.get("/api/v1/projects/{project_id}/dreams", response_model=Page)
    def list_dreams(
        project_id: int,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _required(db.get(Project, project_id), "Project 不存在")
        statement = select(DreamRun).where(DreamRun.project_id == project_id)
        total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        items = list(
            db.scalars(
                statement.order_by(DreamRun.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return {
            "items": [DreamRead.model_validate(item).model_dump() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @application.get("/api/v1/dreams/{dream_id}", response_model=DreamDetailRead)
    def dream_detail(dream_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return DreamService(db).detail(dream_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get(
        "/api/v1/projects/{project_id}/initializations",
        response_model=list[JobRead],
    )
    def list_initializations(project_id: int, db: Session = Depends(get_db)) -> list[Job]:
        _required(db.get(Project, project_id), "Project 不存在")
        return InitializationService(db).progress(project_id)

    @application.get("/api/v1/projects/{project_id}/jobs", response_model=list[JobRead])
    def list_jobs(project_id: int, db: Session = Depends(get_db)) -> list[Job]:
        _required(db.get(Project, project_id), "Project 不存在")
        return _project_jobs(db, project_id)

    @application.get("/api/v1/projects/{project_id}/jobs/stream")
    async def stream_jobs(
        project_id: int,
        request: Request,
        once: bool = False,
    ) -> StreamingResponse:
        # SSE 给前端推 Job 进度；Worker 在别的进程写库，所以每次快照都开新 Session。
        with SessionLocal() as db:
            _required(db.get(Project, project_id), "Project 不存在")

        async def events():
            last = ""
            while True:
                if await request.is_disconnected():
                    break
                payload = _job_stream_payload(project_id)
                if payload != last:
                    yield f"event: jobs\ndata: {payload}\n\n"
                    last = payload
                if once:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get(
        "/api/v1/jobs",
        response_model=list[JobRead],
        dependencies=[Depends(require_admin)],
    )
    def list_all_jobs(db: Session = Depends(get_db)) -> list[Job]:
        return _all_jobs(db)

    @application.get(
        "/api/v1/jobs/stream",
        dependencies=[Depends(require_admin)],
    )
    async def stream_all_jobs(request: Request, once: bool = False) -> StreamingResponse:
        async def events():
            last = ""
            while True:
                if await request.is_disconnected():
                    break
                payload = _job_stream_payload()
                if payload != last:
                    yield f"event: jobs\ndata: {payload}\n\n"
                    last = payload
                if once:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post(
        "/api/v1/dream-changes/{change_id}/revert",
        dependencies=[Depends(require_admin)],
    )
    def revert_dream_change(
        change_id: int, reason: str = Query(min_length=1), db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        try:
            change = DreamService(db).revert_change(change_id, reason=reason)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        db.commit()
        return {"id": change.id, "status": change.status}

    @application.post(
        "/api/v1/initializations",
        response_model=JobRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_initialization(
        payload: InitializeRequest, project_id: int, db: Session = Depends(get_db)
    ) -> Job:
        _required(db.get(Project, project_id), "Project 不存在")
        if payload.repository_id:
            repository = _required(db.get(Repository, payload.repository_id), "Repository 不存在")
            if repository.project_id != project_id:
                raise HTTPException(status_code=400, detail="Repository 不属于 Project")
        job = InitializationService(db).create(project_id, payload.repository_id)
        _enqueue_initialization(job.id)
        return job

    @application.get("/api/v1/jobs/{job_id}", response_model=JobRead)
    def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
        try:
            return InitializationService(db).get(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get("/api/v1/jobs/{job_id}/steps", response_model=list[JobStepRead])
    def get_job_steps(job_id: int, db: Session = Depends(get_db)) -> list[JobStep]:
        try:
            return InitializationService(db).steps(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.post(
        "/api/v1/jobs/{job_id}/run", response_model=JobRead, dependencies=[Depends(require_admin)]
    )
    def run_job(job_id: int, db: Session = Depends(get_db)) -> Job:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job 不存在")
        if job.status not in {"queued", "paused"}:
            raise HTTPException(status_code=400, detail="只有排队中或已暂停 Job 可以继续")
        job.status = "queued"
        db.commit()
        enqueued = _enqueue_job(job)
        if not enqueued:
            job.status = "failed"
            job.error = "后台任务队列不可用，请检查 Redis 和 Worker"
            db.commit()
        return job

    @application.post(
        "/api/v1/jobs/{job_id}/pause", response_model=JobRead, dependencies=[Depends(require_admin)]
    )
    def pause_job(job_id: int, db: Session = Depends(get_db)) -> Job:
        return InitializationService(db).pause(job_id)

    @application.post(
        "/api/v1/jobs/{job_id}/cancel",
        response_model=JobRead,
        dependencies=[Depends(require_admin)],
    )
    def cancel_job(job_id: int, db: Session = Depends(get_db)) -> Job:
        return InitializationService(db).request_cancel(job_id)

    @application.post(
        "/api/v1/jobs/{job_id}/retry", response_model=JobRead, dependencies=[Depends(require_admin)]
    )
    def retry_job(job_id: int, db: Session = Depends(get_db)) -> Job:
        try:
            job = InitializationService(db).retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not _enqueue_job(job):
            job.status = "failed"
            job.error = "后台任务队列不可用，请检查 Redis 和 Worker"
            db.commit()
        return job

    @application.delete(
        "/api/v1/jobs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    def delete_job(job_id: int, db: Session = Depends(get_db)) -> None:
        try:
            InitializationService(db).delete(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @application.post("/api/v1/agent/memory-context", dependencies=[Depends(require_mcp)])
    def agent_memory_context(
        payload: AgentContextRequest, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        try:
            return RetrievalService(db).memory_context(
                project_ref=payload.project,
                repository_ref=payload.repo,
                task=payload.task,
                files=payload.files,
                symbols=payload.symbols,
                session_id=payload.session_id,
                token_budget=payload.token_budget,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get(
        "/api/v1/agent/memory-expand/{memory_id}",
        dependencies=[Depends(require_mcp)],
    )
    def agent_memory_expand(memory_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return RetrievalService(db).memory_expand(memory_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get(
        "/api/v1/agent/evidence-open/{evidence_id}",
        dependencies=[Depends(require_mcp)],
    )
    def agent_evidence_open(evidence_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return RetrievalService(db).evidence_open(evidence_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @application.get(
        "/api/v1/agent/memory-compare/{left_id}/{right_id}",
        dependencies=[Depends(require_mcp)],
    )
    def agent_memory_compare(
        left_id: int, right_id: int, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        try:
            return RetrievalService(db).memory_compare(left_id, right_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    return application


def _required(value: Any, message: str) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail=message)
    return value


def _validate_clone_url(clone_url: str) -> None:
    parsed = urlparse(clone_url)
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="clone_url 不允许包含认证信息")
    if parsed.scheme not in {"http", "https", "ssh", "git"}:
        raise HTTPException(status_code=400, detail="clone_url 必须使用受支持的 Git URL")


def _ignore_patterns(repository_id: int, db: Session) -> list[str]:
    return [
        item.pattern
        for item in db.scalars(
            select(RepositoryIgnoreRule)
            .where(RepositoryIgnoreRule.repository_id == repository_id)
            .order_by(RepositoryIgnoreRule.position)
        ).all()
    ]


def _repository_payload(repository: Repository, db: Session) -> dict[str, Any]:
    sync_state = db.scalar(
        select(RepositorySyncState).where(RepositorySyncState.repository_id == repository.id)
    )
    return {
        "id": repository.id,
        "project_id": repository.project_id,
        "name": repository.name,
        "gitlab_project_id": repository.gitlab_project_id,
        "clone_url": repository.clone_url,
        "release_branch": repository.release_branch,
        "active": repository.active,
        "ignore": _ignore_patterns(repository.id, db),
        "sync_status": sync_state.status if sync_state else "pending",
        "last_synced_sha": sync_state.last_synced_sha if sync_state else None,
        "last_remote_sha": sync_state.last_remote_sha if sync_state else None,
        "sync_error": sync_state.error if sync_state else None,
        "created_at": repository.created_at,
        "updated_at": repository.updated_at,
    }


def _enqueue_release_change(change_id: int) -> None:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import process_release_change

        configure_broker()
        process_release_change.send(change_id)
    except Exception:
        # API 已持久化 ReleaseChange；无 Redis 时由人工/重试任务补投，不丢事实。
        return


def _enqueue_history_sync(job_id: int, limit: int) -> bool:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import run_history_sync

        configure_broker()
        run_history_sync.send(job_id, limit)
        return True
    except Exception:
        return False


def _enqueue_memory_polish(job_id: int) -> bool:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import run_memory_polish

        configure_broker()
        run_memory_polish.send(job_id)
        return True
    except Exception:
        return False


def _enqueue_dream(job_id: int) -> bool:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import run_dream

        configure_broker()
        run_dream.send(job_id)
        return True
    except Exception:
        return False


def _enqueue_initialization(job_id: int) -> bool:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import run_initialization

        configure_broker()
        run_initialization.send(job_id)
        return True
    except Exception:
        return False


def _enqueue_mirror_sync(job_id: int) -> bool:
    try:
        from apps.worker.main import configure_broker
        from apps.worker.tasks import run_mirror_sync

        configure_broker()
        run_mirror_sync.send(job_id)
        return True
    except Exception:
        return False


def _enqueue_job(job: Job) -> bool:
    if job.kind == "memory_polish":
        return _enqueue_memory_polish(job.id)
    if job.kind.startswith("dream_"):
        return _enqueue_dream(job.id)
    if job.kind == "full_initialization":
        return _enqueue_initialization(job.id)
    if job.kind == "mirror_sync":
        return _enqueue_mirror_sync(job.id)
    if job.kind == "history_sync":
        return _enqueue_history_sync(job.id, int((job.checkpoint or {}).get("limit", 100)))
    return False


def _project_jobs(db: Session, project_id: int) -> list[Job]:
    return list(
        db.scalars(
            select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
        ).all()
    )


def _all_jobs(db: Session) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.created_at.desc())).all())


def _job_stream_payload(project_id: int | None = None) -> str:
    with SessionLocal() as db:
        jobs = _project_jobs(db, project_id) if project_id is not None else _all_jobs(db)
        payload = [JobRead.model_validate(job).model_dump() for job in jobs]
    return json.dumps(payload, ensure_ascii=False, default=str)


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "apps.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.app_env != "production",
    )
