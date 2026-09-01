"""运行时配置。

配置集中在这里，业务代码不直接读取环境变量，避免 Secret 在日志和响应中泄漏。
"""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有进程共用的配置。

    这里的 GitLab 地址、GitLab Token 和 Webhook Secret 是 MemLoci 的实例级配置；
    某个 Repo 的 project ID、clone URL、release branch 和 Ignore 规则属于数据库中的
    Repository 配置，不能通过这一组环境变量替代。
    """

    # 应用与 HTTP 管理认证
    app_name: str = "MemLoci"
    app_env: str = "development"
    admin_token: str = "change-me"
    # 远程 MCP / Agent 只读接口。留空则回退到 ADMIN_TOKEN。
    mcp_token: str = ""

    # 持久化与异步消息。本地和测试都使用 PostgreSQL；Dramatiq Worker 才会连接 Redis。
    database_url: str = "postgresql+psycopg://memloci:memloci@127.0.0.1:5432/memloci"
    redis_url: str = "redis://localhost:6379/0"

    # GitLab 实例级凭证。GitLabClient 会把 base_url 转为 {base_url}/api/v4。
    gitlab_base_url: str = "https://gitlab.example.com"
    gitlab_token: str = ""
    gitlab_webhook_secret: str = ""
    # 内网自签证书会验不过；需要校验证书时设 GITLAB_SSL_VERIFY=true。
    gitlab_ssl_verify: bool = False
    mirror_root: Path = Path("./mirrors")
    # 只限制 GitLab Webhook 的 HTTP JSON 体积，不是仓库或 Release 大小。
    # 正式分支的 diff / 历史由 Mirror 拉 Git，不走这条上限。
    webhook_max_bytes: int = Field(default=16_777_216, ge=1_024)

    # LLM 默认走外部 OpenAI-compatible API；Key/URL 为空时由 Provider 明确报错。
    # 测试可以显式把 llm_provider 改成 heuristic，禁止生产环境静默降级。
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    # 推理强度：none / minimal / low / medium / high / xhigh / max。留空不下发。
    llm_reasoning_effort: str | None = "medium"
    # 单次 responses.parse 超时。这不是整段 Job 的上限。
    llm_request_timeout_seconds: float = Field(default=600.0, ge=30.0, le=3_600.0)
    # 正式分支只产出 Candidate；Worker 在本地时区每天夜间统一整理一次。
    auto_dream_enabled: bool = False
    auto_dream_hour: int = Field(default=3, ge=0, le=23)
    auto_dream_timezone: str = "UTC"
    openai_api_key: str = ""
    openai_base_url: str = ""
    # 自签证书常见于内网网关；GitLab 已关校验，模型客户端默认同样关闭。
    llm_ssl_verify: bool = False

    # Embedding 当前是本地确定性 Hash Provider，不是已安装的语义模型。
    embedding_provider: str = "hash"
    embedding_model: str = "hash-384"
    embedding_dimensions: int = Field(default=384, ge=8, le=4096)

    # 日志与浏览器跨域来源
    log_level: str = "INFO"
    web_origin: str = "http://localhost:5173"

    # 记忆检索。含义见 .env / .env.example 同一组注释。改完重启进程。
    recall_top_k: int = Field(default=4, ge=1, le=20)
    recall_keyword_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    recall_vector_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    recall_active_bonus: float = Field(default=0.1, ge=0.0, le=1.0)
    recall_title_blend: float = Field(default=0.35, ge=0.0, le=1.0)
    recall_distinctive_rerank: bool = True
    recall_distinctive_bonus: float = Field(default=0.12, ge=0.0, le=1.0)
    recall_pool_size: int = Field(default=12, ge=1, le=50)
    recall_signal_floor: float = Field(default=0.03, ge=0.0, le=2.0)
    recall_signal_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    recall_hit_cap: int = Field(default=6, ge=1, le=20)
    # 「不是 X」且记忆里有 X 时，关键词分乘这个系数。1=不罚。
    recall_negative_penalty: float = Field(default=0.35, ge=0.0, le=1.0)
    # 澄清句压上一轮 Top1 的融合分。1=不压。
    recall_previous_top_penalty: float = Field(default=0.45, ge=0.0, le=1.0)
    # 仓加分 = min + span * (亲和度/最高亲和度)。hint 仓保底见 hint_weight_floor。
    recall_repo_weight_min: float = Field(default=0.04, ge=0.0, le=1.0)
    recall_repo_weight_span: float = Field(default=0.12, ge=0.0, le=1.0)
    # 传入的 repo 至少有这么高的亲和度，避免完全没声。
    recall_hint_affinity_floor: float = Field(default=0.35, ge=0.0, le=1.0)
    # 题面命中仓名时：base + span * 命中比例。
    recall_affinity_hit_base: float = Field(default=0.55, ge=0.0, le=1.0)
    recall_affinity_hit_span: float = Field(default=0.45, ge=0.0, le=1.0)
    # 传了 repo 时主仓加分不低于此值。其它仓照样能按用词分进来。
    recall_hint_weight_floor: float = Field(default=0.16, ge=0.0, le=1.0)
    # true：传了 repo 就当主仓标签，题面碰到别的仓名也不改这个标签。false：按仓名亲和度改主仓标签。
    recall_keep_hint_primary: bool = True
    # true：只改 scss/css 这种只剩扩展名的问法直接 empty。
    recall_generic_only_empty: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def empty_reasoning_effort(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("llm_reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str | None) -> str | None:
        allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(
                "LLM_REASONING_EFFORT 必须是 none/minimal/low/medium/high/xhigh/max，或留空不下发"
            )
        return normalized

    @field_validator("auto_dream_timezone")
    @classmethod
    def validate_auto_dream_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("AUTO_DREAM_TIMEZONE 必须是有效的 IANA 时区") from exc
        return value

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError(
                "DATABASE_URL 必须是 PostgreSQL，例如 "
                "postgresql+psycopg://memloci:memloci@127.0.0.1:5432/memloci"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """返回进程内共享配置；测试可通过 `cache_clear` 后重新加载。"""

    settings = Settings()
    settings.mirror_root.mkdir(parents=True, exist_ok=True)
    return settings
