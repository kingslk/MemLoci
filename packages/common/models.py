"""MemLoci 的持久化领域模型。

v1 先用 PostgreSQL 的结构化字段、JSON 和可解释的关系表承载图数据；
图数据库不是事实源，所有关系都能从 GitLab、Evidence 或人工审计回溯。

表/字段说明写在 SQLAlchemy `comment=` 上，迁移同步为 PostgreSQL COMMENT。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="最后更新时间（UTC）"
    )


class Organization(TimestampMixin, Base):
    """租户边界。当前 v1 主要按 Project 工作，Organization 预留给多团队隔离。"""

    __tablename__ = "organizations"
    __table_args__ = {"comment": "租户。v1 工作单元是 Project，这张表预留多团队隔离，业务代码尚未使用。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, comment="租户名称")


class Project(TimestampMixin, Base):
    """跨 Repo 检索的候选池。Memory 属于 Project，不锁死在单个仓库。"""

    __tablename__ = "projects"
    __table_args__ = {"comment": "业务项目。跨仓库检索和记忆的最大自动召回范围。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键。MCP/API 里的 project 用这个数字 id 或精确名称。")
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), comment="所属租户。可空，v1 不强制。"
    )
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, comment="项目名称，全局唯一")
    description: Mapped[str] = mapped_column(Text, default="", comment="项目说明")


class Repository(TimestampMixin, Base):
    """GitLab 仓库在 MemLoci 中的配置。事实源仍是 GitLab，这里只存接入信息。"""

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_repository_project_name"),
        {"comment": "GitLab 仓库在 MemLoci 中的接入配置。代码事实源仍是远端仓库。"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属 Project"
    )
    name: Mapped[str] = mapped_column(String(200), comment="仓库名，同一 Project 内唯一，可作 MCP repo 提示")
    gitlab_project_id: Mapped[str] = mapped_column(String(100), index=True, comment="GitLab 数字项目 id")
    clone_url: Mapped[str] = mapped_column(String(2_000), comment="Git clone 地址")
    release_branch: Mapped[str] = mapped_column(String(255), default="main", comment="正式分支，只认这条线上的变更")
    active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否仍参与同步和召回")


class RepositoryIgnoreRule(Base):
    __tablename__ = "repository_ignore_rules"
    __table_args__ = {"comment": "仓库忽略规则。同步和代码索引时跳过匹配路径。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库"
    )
    pattern: Mapped[str] = mapped_column(String(500), comment="gitignore 风格路径模式")
    position: Mapped[int] = mapped_column(Integer, default=0, comment="规则顺序，数字越小越先匹配")


class RepositorySyncState(TimestampMixin, Base):
    __tablename__ = "repository_sync_state"
    __table_args__ = {"comment": "每个仓库一行的同步状态。镜像、远端 SHA 和最近一次成败。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), unique=True, comment="所属仓库，一对一"
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", comment="pending/syncing/ready/error")
    last_synced_sha: Mapped[str | None] = mapped_column(String(128), comment="本地镜像已同步到的 commit")
    last_remote_sha: Mapped[str | None] = mapped_column(String(128), comment="上次看到的远端 commit")
    error: Mapped[str | None] = mapped_column(Text, comment="最近一次失败原因")
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次同步成功时间"
    )


class ReleaseChange(TimestampMixin, Base):
    """正式分支上的一次幂等变更。MR Hook 与 Push Hook 必须合并为同一条记录。"""

    __tablename__ = "release_changes"
    __table_args__ = {"comment": "正式分支上的一次幂等变更。同一 change_key 的 MR/Push 必须合并成一行。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库"
    )
    before_sha: Mapped[str] = mapped_column(String(128), default="", comment="变更前 commit")
    after_sha: Mapped[str] = mapped_column(String(128), comment="变更后 commit")
    release_branch: Mapped[str] = mapped_column(String(255), comment="当时认定的正式分支")
    source_type: Mapped[str] = mapped_column(String(64), comment="来源：push / merge_request 等")
    source_event_id: Mapped[str | None] = mapped_column(String(255), comment="GitLab 事件 id，用于去重")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, comment="远端事件时间"
    )
    processing_status: Mapped[str] = mapped_column(
        String(32), default="pending", comment="pending/processing/done/failed"
    )
    change_key: Mapped[str] = mapped_column(
        String(500), unique=True, index=True, comment="幂等键。相同变更不得拆成两行。"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="原始事件摘要")


class Evidence(TimestampMixin, Base):
    """可追溯事实，不是结论。没有 Evidence 的 Candidate 不能晋升 Active。"""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("repository_id", "source_type", "source_id", name="uq_evidence_source"),
        {"comment": "可追溯事实，不是结论。没有证据的记忆不能启用。"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库"
    )
    release_change_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_changes.id", ondelete="SET NULL"), index=True, comment="来源变更，可空"
    )
    source_type: Mapped[str] = mapped_column(String(64), comment="commit / mr / cluster / external 等")
    source_id: Mapped[str] = mapped_column(String(255), comment="来源侧稳定 id，与 source_type 一起唯一")
    title: Mapped[str] = mapped_column(String(500), comment="事实标题")
    summary: Mapped[str] = mapped_column(Text, default="", comment="一两句事实摘要，不是做法")
    url: Mapped[str | None] = mapped_column(String(2_000), comment="GitLab 或其它原文链接")
    importance_score: Mapped[float] = mapped_column(Float, default=0.0, comment="抽取时的重要度，0–1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="diff、文件列表等原始材料")
    embedding: Mapped[list[float] | None] = mapped_column(
        JSON, comment="检索向量。换成 pgvector 时必须按 provider/model/维度整批重建。"
    )
    embedding_provider: Mapped[str] = mapped_column(String(100), default="hash", comment="向量提供方")
    embedding_model: Mapped[str] = mapped_column(String(200), default="hash-384", comment="向量模型名")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=384, comment="向量维度")
    embedding_version: Mapped[str] = mapped_column(String(100), default="v1", comment="向量版本，重建时比对")


class EvidenceFile(Base):
    __tablename__ = "evidence_files"
    __table_args__ = {"comment": "一条证据碰到的文件路径。图谱用它把证据连到当前快照文件。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    evidence_id: Mapped[int] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), index=True, comment="所属证据"
    )
    path: Mapped[str] = mapped_column(String(2_000), comment="变更后路径")
    old_path: Mapped[str | None] = mapped_column(String(2_000), comment="重命名前路径")
    change_type: Mapped[str] = mapped_column(String(32), default="modified", comment="added/modified/deleted/renamed")
    additions: Mapped[int] = mapped_column(Integer, default=0, comment="新增行数")
    deletions: Mapped[int] = mapped_column(Integer, default=0, comment="删除行数")


class CodeFile(TimestampMixin, Base):
    """当前 release 快照中的文件。历史 Evidence 不依赖这张表存活。"""

    __tablename__ = "code_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", "path", name="uq_code_file_version"),
        Index("ix_code_file_current_path", "repository_id", "path"),
        {"comment": "当前正式分支快照中的文件。历史证据不依赖这张表存活。"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库"
    )
    sha: Mapped[str] = mapped_column(String(128), comment="该快照对应的 commit")
    path: Mapped[str] = mapped_column(String(2_000), comment="仓库内相对路径")
    language: Mapped[str] = mapped_column(String(64), default="unknown", comment="语言标签")
    content_hash: Mapped[str] = mapped_column(String(128), comment="文件内容哈希，用于增量索引")
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否被忽略规则排除")
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", comment="pending/parsed/failed")
    embedding: Mapped[list[float] | None] = mapped_column(JSON, comment="文件级向量，当前默认 hash")
    embedding_provider: Mapped[str] = mapped_column(String(100), default="hash", comment="向量提供方")
    embedding_model: Mapped[str] = mapped_column(String(200), default="hash-384", comment="向量模型名")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=384, comment="向量维度")
    embedding_version: Mapped[str] = mapped_column(String(100), default="v1", comment="向量版本")


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    __table_args__ = {"comment": "从当前快照解析出的符号：函数、类型、注释锚点等。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    code_file_id: Mapped[int] = mapped_column(
        ForeignKey("code_files.id", ondelete="CASCADE"), index=True, comment="所在文件"
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库，冗余便于按仓查"
    )
    name: Mapped[str] = mapped_column(String(500), comment="短名")
    qualified_name: Mapped[str] = mapped_column(String(1_000), comment="带路径或限定的全名")
    kind: Mapped[str] = mapped_column(String(64), comment="function/class/comment 等")
    start_line: Mapped[int] = mapped_column(Integer, comment="起始行，1-based")
    end_line: Mapped[int] = mapped_column(Integer, comment="结束行，含")
    signature: Mapped[str] = mapped_column(Text, default="", comment="签名或摘录")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, comment="解析置信度")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="snippet 等附加信息")


class CodeRelation(Base):
    __tablename__ = "code_relations"
    __table_args__ = {"comment": "符号之间的静态关系。imports 常解析不到目标；calls 目前多在同文件内。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库"
    )
    source_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"), comment="起点符号"
    )
    target_symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="SET NULL"), comment="终点符号。解析失败时为空，只留 target_name。"
    )
    target_name: Mapped[str] = mapped_column(String(1_000), default="", comment="目标名字面量")
    relation_type: Mapped[str] = mapped_column(String(64), comment="imports / calls")
    confidence: Mapped[float] = mapped_column(Float, default=0.5, comment="关系置信度")
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为推断而非直接语法边")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="附加解析信息")


class ArchitectureEpoch(TimestampMixin, Base):
    __tablename__ = "architecture_epochs"
    __table_args__ = {"comment": "初始化时划分的架构阶段。给主题重建用，不是运行时图。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属项目"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="可选，落到单个仓库"
    )
    name: Mapped[str] = mapped_column(String(200), comment="阶段名")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="阶段开始")
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="阶段结束")
    technologies: Mapped[list[str]] = mapped_column(JSON, default=list, comment="该阶段主要技术栈")
    confidence: Mapped[float] = mapped_column(Float, default=0.5, comment="划分置信度")
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list, comment="支撑该阶段的证据 id")


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_topic_project_key"),
        {"comment": "项目内主题。记忆可挂到主题上，用于图谱归类。"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属项目"
    )
    key: Mapped[str] = mapped_column(String(255), comment="稳定键，同一项目内唯一")
    name: Mapped[str] = mapped_column(String(500), comment="展示名")
    description: Mapped[str] = mapped_column(Text, default="", comment="主题说明")


class Memory(TimestampMixin, Base):
    """可迁移经验。pattern 是做法，do_not_copy 防止把来源仓库架构当成目标要求。"""

    __tablename__ = "memories"
    __table_args__ = {"comment": "可迁移工程经验。pattern 是做法，implementation 只是当时链路，do_not_copy 防抄架构。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键。MCP 返回的 memory_id。")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属项目，召回池按这个圈"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"),
        index=True,
        comment="主来源仓库。可空表示项目级经验。",
    )
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), comment="可选主题"
    )
    title: Mapped[str] = mapped_column(String(500), comment="一句话经验名，不要用 commit 原句")
    type: Mapped[str] = mapped_column(String(32), default="semantic", comment="记忆类型，v1 主要是 semantic")
    status: Mapped[str] = mapped_column(
        String(32),
        default="candidate",
        index=True,
        comment="candidate/tentative/active/deprecated/archived/rejected。Agent 只召回 active 和 tentative。",
    )
    problem: Mapped[str] = mapped_column(Text, default="", comment="要解决的工程麻烦，不是变更摘要")
    pattern: Mapped[list[str]] = mapped_column(JSON, default=list, comment="换仓库也能照着做的步骤")
    implementation: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="来源仓库里的具体链路，仅供对照"
    )
    do_not_copy: Mapped[list[str]] = mapped_column(
        JSON, default=list, comment="来源仓库特有的目录/框架/命名，换项目不要抄"
    )
    apply_when: Mapped[list[str]] = mapped_column(JSON, default=list, comment="满足这些条件再启用")
    do_not: Mapped[list[str]] = mapped_column(JSON, default=list, comment="会扩大范围或套错架构时不要用。不参与检索打分。")
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="适用范围附加约束")
    confidence: Mapped[float] = mapped_column(
        Float, default=0.0, comment="展示用可信度。检索排序不再使用这个字段。"
    )
    origin_repositories: Mapped[list[int]] = mapped_column(JSON, default=list, comment="贡献过这条经验的仓库 id")
    embedding: Mapped[list[float] | None] = mapped_column(JSON, comment="记忆检索向量")
    embedding_provider: Mapped[str] = mapped_column(String(100), default="hash", comment="向量提供方")
    embedding_model: Mapped[str] = mapped_column(String(200), default="hash-384", comment="向量模型名")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=384, comment="向量维度")
    embedding_version: Mapped[str] = mapped_column(String(100), default="v1", comment="向量版本")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="人工纠错或状态变更后递增")


class MemoryEvidence(Base):
    __tablename__ = "memory_evidence"
    __table_args__ = {"comment": "记忆与证据的多对多。升 active 必须至少有一行。"}

    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True, comment="记忆"
    )
    evidence_id: Mapped[int] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True, comment="证据"
    )
    role: Mapped[str] = mapped_column(
        String(64), default="supports", comment="derived_from / supports。抽取落库用 derived_from。"
    )


class DreamRun(TimestampMixin, Base):
    """一次 Dreaming 的完整审计。失败也要留下 provider/model/输入输出，禁止静默改 Memory。"""

    __tablename__ = "dream_runs"
    __table_args__ = {"comment": "一次整理（Dreaming）的审计头。失败也要留下模型与输入输出。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属项目"
    )
    dream_type: Mapped[str] = mapped_column(
        String(32), comment="incremental / manual / genesis / full_validation"
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", comment="pending/running/succeeded/failed")
    input_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="本轮吃进去的证据/记忆 id")
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="产出摘要")
    provider: Mapped[str] = mapped_column(String(100), default="heuristic", comment="LLM 提供方")
    model: Mapped[str] = mapped_column(String(200), default="heuristic-v1", comment="模型名")
    prompt_version: Mapped[str] = mapped_column(String(100), default="v1", comment="提示词版本")
    token_count: Mapped[int] = mapped_column(Integer, default=0, comment="本轮消耗 token")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="耗时毫秒")
    error: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="结束时间")


class DreamChange(Base):
    __tablename__ = "dream_changes"
    __table_args__ = {"comment": "一次整理对单条记忆的增删改或冲突记录。可撤回。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    dream_run_id: Mapped[int] = mapped_column(
        ForeignKey("dream_runs.id", ondelete="CASCADE"), index=True, comment="所属整理轮次"
    )
    memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL"), index=True, comment="被改的记忆，删除后可空"
    )
    action: Mapped[str] = mapped_column(String(64), comment="create/update/merge/conflict/deprecate 等")
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="变更前快照")
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="变更后快照")
    reason: Mapped[str] = mapped_column(Text, comment="模型或规则给出的原因")
    evidence_ids: Mapped[list[int]] = mapped_column(JSON, default=list, comment="本条变更依据的证据")
    status: Mapped[str] = mapped_column(String(32), default="applied", comment="applied / reverted")
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="撤回时间")


class Job(TimestampMixin, Base):
    """可恢复工作流。checkpoint 在 PostgreSQL，Redis 只负责把任务投递给 Worker。"""

    __tablename__ = "jobs"
    __table_args__ = {"comment": "可恢复后台任务。检查点在 PostgreSQL，Redis 只负责投递。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="所属项目，可空"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="所属仓库，可空"
    )
    kind: Mapped[str] = mapped_column(String(64), comment="mirror_sync / history_sync / dream_* / full_initialization")
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True, comment="queued/running/succeeded/failed/cancelled 等"
    )
    current_stage: Mapped[str] = mapped_column(Text, default="", comment="当前阶段文案，给界面看")
    progress: Mapped[float] = mapped_column(Float, default=0.0, comment="0–1 进度")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="已重试次数")
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="恢复点")
    error: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="结束时间")


class JobStep(TimestampMixin, Base):
    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "repository_id",
            "stage",
            name="uq_job_step_job_repository_stage",
        ),
        {"comment": "任务按仓库拆开的步骤。一个 Job 下每个仓库每个 stage 一行。"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, comment="所属任务"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, comment="该步骤对应的仓库"
    )
    stage: Mapped[str] = mapped_column(String(64), comment="阶段键")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True, comment="步骤状态")
    progress: Mapped[float] = mapped_column(Float, default=0.0, comment="0–1 进度")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="已重试次数")
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="步骤恢复点")
    error: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="结束时间")


class AgentSession(TimestampMixin, Base):
    __tablename__ = "agent_sessions"
    __table_args__ = {"comment": "Agent 会话。记下已下发过的记忆 id，下一轮只压缩不删候选。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), comment="所属项目"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), comment="创建时的主仓。具名 session 不按仓隔离。"
    )
    session_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, comment="对外 session 键。anonymous 会带上 project 和 repo。"
    )
    seen_memories: Mapped[list[int]] = mapped_column(
        JSON, default=list, comment="本会话已完整下发过的 memory id，只用于压缩展示"
    )
    seen_symbols: Mapped[list[int]] = mapped_column(JSON, default=list, comment="已见过的符号，预留")
    seen_evidence: Mapped[list[int]] = mapped_column(JSON, default=list, comment="已见过的证据，预留")


class AgentQueryLog(TimestampMixin, Base):
    __tablename__ = "agent_query_logs"
    __table_args__ = {
        "comment": "memory_context 调用日志。只记提问、路由和分数，不记记忆正文。召回率要事后人工标注才有。"
    }

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    session_id: Mapped[str] = mapped_column(String(255), index=True, comment="调用方传入的 session_id")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="推断出的项目"
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), comment="当时的主仓"
    )
    tool_name: Mapped[str] = mapped_column(String(100), comment="工具名，目前是 memory_context")
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="task / files / symbols / 原始 project_ref repo_ref / prev_query_id"
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="recall_mode、仓权重、截止、每条四路分数"
    )
    token_budget: Mapped[int] = mapped_column(Integer, default=4_000, comment="调用方 token 预算")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, comment="服务端耗时")
    recall_mode: Mapped[str] = mapped_column(
        String(32), default="", index=True, comment="active / tentative_fallback / empty"
    )
    primary_switched: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="hinted 仓是否被换成别的主仓"
    )
    returned_count: Mapped[int] = mapped_column(Integer, default=0, comment="实际返回条数")


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"comment": "人工纠错和状态变更审计。人改过的记忆 Dreaming 不得覆盖。"}

    id: Mapped[int] = mapped_column(primary_key=True, comment="主键")
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, comment="所属项目"
    )
    actor: Mapped[str] = mapped_column(String(255), default="system", comment="操作者，admin/system 等")
    action: Mapped[str] = mapped_column(String(100), comment="memory_corrected / memory_status_changed 等")
    entity_type: Mapped[str] = mapped_column(String(100), comment="实体类型")
    entity_id: Mapped[str] = mapped_column(String(100), comment="实体 id，字符串以免跨表")
    reason: Mapped[str] = mapped_column(Text, default="", comment="原因，人工纠错必填")
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="变更前")
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="变更后")
