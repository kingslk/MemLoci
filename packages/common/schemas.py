"""HTTP、MCP 共用的稳定输入输出 Schema。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectRead(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    gitlab_project_id: str = Field(min_length=1, max_length=100)
    clone_url: str = Field(min_length=1, max_length=2_000)
    release_branch: str = Field(default="main", min_length=1, max_length=255)
    ignore: list[str] = Field(default_factory=list)


class RepositoryRead(RepositoryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    active: bool
    sync_status: str = "unknown"
    last_synced_sha: str | None = None
    last_remote_sha: str | None = None
    sync_error: str | None = None
    created_at: datetime
    updated_at: datetime


class IgnorePreviewRead(BaseModel):
    sha: str
    included: list[str]
    excluded: list[str]


class ReleaseChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    before_sha: str
    after_sha: str
    release_branch: str
    source_type: str
    processing_status: str
    change_key: str
    occurred_at: datetime


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    repository_id: int | None
    topic_id: int | None
    title: str
    type: str
    status: str
    problem: str
    pattern: list[str]
    implementation: dict[str, Any]
    do_not_copy: list[str]
    apply_when: list[str]
    do_not: list[str]
    scope: dict[str, Any]
    confidence: float
    origin_repositories: list[int]
    version: int


class MemoryListItem(MemoryRead):
    evidence_count: int = 0
    repository_name: str | None = None
    topic_name: str | None = None
    updated_at: str | None = None


class TopicRead(BaseModel):
    id: int
    name: str
    key: str


class MemoryCorrection(BaseModel):
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    title: str | None = None
    problem: str | None = None
    pattern: list[str] | None = None
    do_not_copy: list[str] | None = None
    apply_when: list[str] | None = None
    do_not: list[str] | None = None
    implementation: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    reason: str = Field(min_length=1)


class MemoryBatchCorrection(BaseModel):
    memory_ids: list[int] = Field(min_length=1, max_length=100)
    status: str
    reason: str = Field(min_length=1)


class DreamCreate(BaseModel):
    project_id: int
    dream_type: str = Field(
        default="manual", pattern="^(incremental|manual|genesis|full_validation)$"
    )


class DreamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    dream_type: str
    status: str
    output_summary: dict[str, Any]
    error: str | None


class DreamChangeRead(BaseModel):
    id: int
    memory_id: int | None
    action: str
    reason: str
    status: str
    before: dict[str, Any]
    after: dict[str, Any]
    evidence_ids: list[int]


class DreamDetailRead(DreamRead):
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    duration_ms: int | None = None
    changes: list[DreamChangeRead] = Field(default_factory=list)


class JobCreate(BaseModel):
    project_id: int | None = None
    repository_id: int | None = None
    kind: str = Field(min_length=1, max_length=64)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    repository_id: int | None
    kind: str
    status: str
    current_stage: str
    progress: float
    retry_count: int
    checkpoint: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    repository_id: int | None
    stage: str
    status: str
    progress: float
    retry_count: int
    checkpoint: dict[str, Any]
    error: str | None


class CodeSearchRequest(BaseModel):
    project_id: int
    repository_id: int | None = None
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class AgentContextRequest(BaseModel):
    """Agent 查询入参。

    token_budget 限制返回体积；session_id 用于同会话去重，避免反复塞同一条 Memory。
    """

    project: str | int = ""
    repo: str | int = ""
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    task: str = Field(min_length=1)
    session_id: str = "anonymous"
    token_budget: int = Field(default=4_000, ge=256, le=32_000)


class GraphRead(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    meta: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class EvidenceListItem(BaseModel):
    id: int
    repository_id: int
    release_change_id: int | None = None
    source_type: str
    source_id: str
    title: str
    summary: str
    importance_score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceDetailRead(EvidenceListItem):
    diff: str = ""
    files: list[Any] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)
