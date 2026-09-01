"""LLM Provider 的稳定 Domain Schema。

默认 Provider 调用外部 OpenAI-compatible API；测试必须显式选择 heuristic。
所有外部调用都经过同一组方法，调用前必须完成 Ignore、敏感数据和 Token Budget 检查。
提示词按 extract → synthesize → reconcile 拆步，版本写在 DreamRun 上。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.common.config import Settings, get_settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSION = "v3"
SKIP_MARKERS = (
    "typo",
    "readme",
    "lint",
    "format",
    "prettier",
    "lockfile",
    "pnpm-lock",
    "package-lock",
    "yarn.lock",
    "chore",
    "docs:",
    "style:",
    "wip",
    "dependabot",
    "bump version",
    "merge branch",
    "merge remote",
    "错字",
    "格式化",
)
EXTRACT_BATCH_SIZE = 12
EXTRACT_CONCURRENCY = 3
LLM_MAX_ATTEMPTS = 3
LLM_RETRY_BACKOFF_SECONDS = 1.5
LLM_REQUEST_TIMEOUT_SECONDS = 600.0


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class ExtractDraft:
    skip: bool
    skip_reason: str = ""
    title: str = ""
    problem: str = ""
    signals: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(frozen=True)
class CandidateDraft:
    title: str
    problem: str
    pattern: list[str]
    implementation: dict[str, Any]
    do_not_copy: list[str]
    apply_when: list[str]
    do_not: list[str]
    confidence: float


@dataclass(frozen=True)
class ReconcileDecision:
    action: Literal["ADD", "UPDATE", "SUPERSEDE", "NOOP"]
    target_memory_id: int | None
    reason: str


class LLMProvider:
    provider = "base"
    model = "base"
    prompt_version = PROMPT_VERSION

    def extract_signal(self, evidence: dict[str, Any]) -> ExtractDraft:
        raise NotImplementedError

    def extract_signals(self, items: list[dict[str, Any]]) -> list[ExtractDraft]:
        return [self.extract_signal(item) for item in items]

    def extract_candidate(self, evidence: dict[str, Any]) -> CandidateDraft:
        """兼容旧调用：skip 时仍返回低置信草稿，由调用方决定是否落库。"""
        signal = self.extract_signal(evidence)
        if signal.skip:
            return CandidateDraft(
                title=str(evidence.get("title") or "跳过"),
                problem=signal.skip_reason or "琐碎变更，不形成经验",
                pattern=[],
                implementation={"skipped": True, "reason": signal.skip_reason},
                do_not_copy=[],
                apply_when=[],
                do_not=[],
                confidence=0.0,
            )
        return CandidateDraft(
            title=signal.title,
            problem=signal.problem,
            pattern=signal.signals,
            implementation={"summary": "", "steps": [], "validation": []},
            do_not_copy=[],
            apply_when=[],
            do_not=[],
            confidence=signal.confidence,
        )

    def synthesize_memory(self, cluster: dict[str, Any]) -> CandidateDraft:
        raise NotImplementedError

    def reconcile_memories(
        self, draft: dict[str, Any], existing: list[dict[str, Any]]
    ) -> ReconcileDecision:
        raise NotImplementedError

    def compare_memories(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def compare_memory_pairs(
        self,
        pairs: list[tuple[dict[str, Any], dict[str, Any]]],
        *,
        reasoning_effort: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self.compare_memories(left, right, reasoning_effort=reasoning_effort)
            for left, right in pairs
        ]


class _ImplementationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    steps: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)


class _ExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip: bool
    skip_reason: str = ""
    title: str = ""
    problem: str = ""
    signals: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class _ExtractBatchItem(_ExtractOutput):
    index: int


class _ExtractBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[_ExtractBatchItem] = Field(default_factory=list)


class _CandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    problem: str
    pattern: list[str]
    implementation: _ImplementationOutput
    do_not_copy: list[str]
    apply_when: list[str]
    do_not: list[str]
    confidence: float


class _ReconcileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ADD", "UPDATE", "SUPERSEDE", "NOOP"]
    target_memory_id: int | None = None
    reason: str


class _MemoryComparisonOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    same_pattern: bool
    conflict: bool
    overlap_terms: list[str]
    reason: str


class _MemoryComparisonBatchItem(_MemoryComparisonOutput):
    index: int


class _MemoryComparisonBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[_MemoryComparisonBatchItem] = Field(default_factory=list)


class OpenAILLMProvider(LLMProvider):
    """通过 OpenAI Responses API 生成严格符合 Schema 的 MemLoci 结果。

    这是默认 Provider。为了避免把私有代码或凭证混入请求，调用方只应传入已经
    过滤后的 Evidence/Memory 摘要；本类不会自动读取仓库文件，也不会回退到本地模型。
    """

    provider = "openai"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str | None = "medium",
        timeout: float = LLM_REQUEST_TIMEOUT_SECONDS,
        verify: bool = False,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("LLM_PROVIDER=openai 时必须配置 OPENAI_API_KEY")
        if not base_url:
            raise ValueError("LLM_PROVIDER=openai 时必须配置 OPENAI_BASE_URL")
        if not model:
            raise ValueError("LLM_PROVIDER=openai 时必须配置 LLM_MODEL")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            http_client=httpx.Client(verify=verify, timeout=timeout),
        )
        self.last_usage: dict[str, int] = {}

    def extract_signal(self, evidence: dict[str, Any]) -> ExtractDraft:
        parsed = self._parse(
            _ExtractOutput,
            instructions=load_prompt("extract_v2.md"),
            payload={"evidence": evidence},
        )
        return ExtractDraft(**parsed.model_dump())

    def extract_signals(self, items: list[dict[str, Any]]) -> list[ExtractDraft]:
        if not items:
            return []
        if len(items) == 1:
            return [self.extract_signal(items[0])]
        parsed = self._parse(
            _ExtractBatchOutput,
            instructions=load_prompt("extract_batch_v2.md"),
            payload={
                "items": [
                    {"index": index, "evidence": evidence}
                    for index, evidence in enumerate(items)
                ]
            },
        )
        by_index = {item.index: item for item in parsed.items}
        if len(by_index) != len(items):
            return [self.extract_signal(item) for item in items]
        drafts: list[ExtractDraft] = []
        for index, evidence in enumerate(items):
            item = by_index[index]
            drafts.append(
                ExtractDraft(
                    skip=item.skip,
                    skip_reason=item.skip_reason,
                    title=item.title or str(evidence.get("title") or ""),
                    problem=item.problem,
                    signals=item.signals,
                    confidence=item.confidence,
                )
            )
        return drafts

    def extract_candidate(self, evidence: dict[str, Any]) -> CandidateDraft:
        return super().extract_candidate(evidence)

    def synthesize_memory(self, cluster: dict[str, Any]) -> CandidateDraft:
        parsed = self._parse(
            _CandidateOutput,
            instructions=load_prompt("synthesize_v2.md"),
            payload={"cluster": cluster},
        )
        return CandidateDraft(**parsed.model_dump())

    def reconcile_memories(
        self, draft: dict[str, Any], existing: list[dict[str, Any]]
    ) -> ReconcileDecision:
        parsed = self._parse(
            _ReconcileOutput,
            instructions=load_prompt("reconcile_v2.md"),
            payload={"draft": draft, "existing": existing},
        )
        return ReconcileDecision(**parsed.model_dump())

    def compare_memories(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        parsed = self._parse(
            _MemoryComparisonOutput,
            instructions=load_prompt("compare_v3.md"),
            payload={"left": left, "right": right},
            reasoning_effort=reasoning_effort,
        )
        return parsed.model_dump()

    def compare_memory_pairs(
        self,
        pairs: list[tuple[dict[str, Any], dict[str, Any]]],
        *,
        reasoning_effort: str | None = None,
    ) -> list[dict[str, Any]]:
        if len(pairs) <= 1:
            return super().compare_memory_pairs(pairs, reasoning_effort=reasoning_effort)
        parsed = self._parse(
            _MemoryComparisonBatchOutput,
            instructions=load_prompt("compare_batch_v3.md"),
            payload={
                "items": [
                    {"index": index, "left": left, "right": right}
                    for index, (left, right) in enumerate(pairs)
                ]
            },
            reasoning_effort=reasoning_effort,
        )
        by_index = {item.index: item for item in parsed.items}
        if len(by_index) != len(pairs):
            return super().compare_memory_pairs(pairs, reasoning_effort=reasoning_effort)
        return [
            by_index[index].model_dump(exclude={"index"}) for index in range(len(pairs))
        ]

    def _parse(
        self,
        output_type: type[BaseModel],
        *,
        instructions: str,
        payload: dict[str, Any],
        reasoning_effort: str | None = None,
    ) -> BaseModel:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": json.dumps(payload, ensure_ascii=False, default=str),
            "text_format": output_type,
            "temperature": 0.2,
        }
        effort = self.reasoning_effort if reasoning_effort is None else reasoning_effort
        if effort:
            request["reasoning"] = {"effort": effort}
        last_error: Exception | None = None
        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            try:
                response = self.client.responses.parse(**request)
                parsed = response.output_parsed
                if parsed is None:
                    raise RuntimeError("外部 LLM 未返回符合 Schema 的结果")
                self._record_usage(response)
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt >= LLM_MAX_ATTEMPTS or not _is_retryable_llm_error(exc):
                    break
                time.sleep(LLM_RETRY_BACKOFF_SECONDS * attempt)
        assert last_error is not None
        detail = _openai_error_detail(last_error)
        suffix = f": {detail}" if detail else ""
        kind = last_error.__class__.__name__
        if isinstance(last_error, (httpx.ConnectError, httpx.ConnectTimeout)):
            raise RuntimeError(f"连不上 LLM 网关: {kind}{suffix}") from last_error
        raise RuntimeError(f"外部 LLM 调用失败: {kind}{suffix}") from last_error

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            self.last_usage = {}
            return
        self.last_usage = {
            key: int(value)
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if (value := getattr(usage, key, None)) is not None
        }


class HeuristicLLMProvider(LLMProvider):
    provider = "heuristic"
    model = "heuristic-v1"
    prompt_version = PROMPT_VERSION

    def extract_signal(self, evidence: dict[str, Any]) -> ExtractDraft:
        title = str(evidence.get("title") or "")
        summary = str(evidence.get("summary") or "")
        blob = f"{title} {summary} {' '.join(_file_paths(evidence))}".lower()
        if any(marker in blob for marker in SKIP_MARKERS):
            return ExtractDraft(skip=True, skip_reason="琐碎或文档变更，不形成经验")
        if not title.strip():
            return ExtractDraft(skip=True, skip_reason="缺少标题，看不出决策")
        return ExtractDraft(
            skip=False,
            title=title,
            problem=f"历史变化表明：{title}",
            signals=[
                "先从真实变更和验证结果提炼问题，再决定是否迁移。",
                "优先复用已被 Evidence 支持的行为，不复制来源仓库的目录结构。",
            ],
            confidence=min(0.9, max(0.2, float(evidence.get("importance_score") or 0.5))),
        )

    def extract_candidate(self, evidence: dict[str, Any]) -> CandidateDraft:
        signal = self.extract_signal(evidence)
        if signal.skip:
            return super().extract_candidate(evidence)
        files = list(evidence.get("changed_files") or [])
        return CandidateDraft(
            title=signal.title,
            problem=signal.problem,
            pattern=signal.signals,
            implementation={
                "repository_id": evidence.get("repository_id"),
                "source": evidence.get("source_type"),
                "files": files[:20],
                "summary": "",
                "steps": [],
                "validation": [],
            },
            do_not_copy=[
                "不要复制来源 Repo 的完整目录或分层。",
                "不要把历史实现当成当前 Repo 的强制架构。",
            ],
            apply_when=[f"当前任务与历史变更主题“{signal.title}”相关。"],
            do_not=["不要因此扩大当前用户任务的目标范围。"],
            confidence=signal.confidence,
        )

    def synthesize_memory(self, cluster: dict[str, Any]) -> CandidateDraft:
        items = list(cluster.get("items") or cluster.get("memories") or [])
        first = items[0] if items else {}
        title = str(first.get("title") or cluster.get("topic") or "主题经验")
        problems = [str(item.get("problem") or "") for item in items if item.get("problem")]
        patterns: list[str] = []
        for item in items:
            patterns.extend(str(part) for part in item.get("pattern") or item.get("signals") or [])
        if not patterns:
            patterns = [
                "先从真实变更和验证结果提炼问题，再决定是否迁移。",
                "优先复用已被 Evidence 支持的行为，不复制来源仓库的目录结构。",
            ]
        do_not_copy = []
        for item in items:
            do_not_copy.extend(str(part) for part in item.get("do_not_copy") or [])
            impl = item.get("implementation") or {}
            if impl.get("summary"):
                do_not_copy.append(str(impl["summary"]))
        if not do_not_copy:
            do_not_copy = ["不要把来源仓库的目录或框架当成目标仓库要求。"]
        return CandidateDraft(
            title=title[:80],
            problem=problems[0] if problems else f"需要处理与「{title}」相关的工程麻烦。",
            pattern=list(dict.fromkeys(patterns))[:5],
            implementation={
                "summary": str((first.get("implementation") or {}).get("summary") or ""),
                "steps": list((first.get("implementation") or {}).get("steps") or []),
                "validation": list((first.get("implementation") or {}).get("validation") or []),
            },
            do_not_copy=list(dict.fromkeys(do_not_copy))[:6],
            apply_when=list(
                dict.fromkeys(
                    part
                    for item in items
                    for part in (item.get("apply_when") or [f"当前任务与「{title}」相关。"])
                )
            )[:4],
            do_not=["不要因此扩大当前用户任务的目标范围。"],
            confidence=min(
                0.9,
                max(
                    (float(item.get("confidence") or 0.6) for item in items),
                    default=0.6,
                ),
            ),
        )

    def reconcile_memories(
        self, draft: dict[str, Any], existing: list[dict[str, Any]]
    ) -> ReconcileDecision:
        if any(item.get("human_corrected") for item in existing):
            return ReconcileDecision("NOOP", None, "已有人工改过的记忆，不覆盖")
        if not existing:
            return ReconcileDecision("ADD", None, "主题下还没有可对照的记忆")
        keeper = existing[0]
        return ReconcileDecision(
            "UPDATE",
            int(keeper["id"]) if keeper.get("id") is not None else None,
            "同一主题，用合成稿更新未人工改过的草稿",
        )

    def compare_memories(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        left_terms = set(" ".join(left.get("pattern", [])).lower().split())
        right_terms = set(" ".join(right.get("pattern", [])).lower().split())
        overlap = sorted(left_terms & right_terms)
        return {
            "same_pattern": len(overlap) >= 2,
            "conflict": False,
            "overlap_terms": overlap[:20],
            "reason": "共享多个 Pattern 词项" if len(overlap) >= 2 else "缺少足够共同 Pattern",
        }


def _file_paths(evidence: dict[str, Any]) -> list[str]:
    return [str(item.get("path") or "") for item in evidence.get("changed_files") or []]


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """按配置创建 LLM；只有显式选择 heuristic 才使用本地实现。"""

    current = settings or get_settings()
    if current.llm_provider == "openai":
        return OpenAILLMProvider(
            api_key=current.openai_api_key,
            base_url=current.openai_base_url,
            model=current.llm_model,
            reasoning_effort=current.llm_reasoning_effort,
            timeout=current.llm_request_timeout_seconds,
            verify=current.llm_ssl_verify,
        )
    if current.llm_provider == "heuristic":
        return HeuristicLLMProvider()
    raise ValueError(f"不支持的 LLM_PROVIDER: {current.llm_provider}")


def _is_retryable_llm_error(exc: Exception) -> bool:
    """瞬时网络/限流/结构化输出校验失败值得再试；鉴权和参数错误立刻失败。"""

    if isinstance(exc, ValidationError):
        return True
    name = exc.__class__.__name__
    if name in {
        "ValidationError",
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "LengthFinishReasonError",
        "InternalServerError",
    }:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in {408, 409, 429, 500, 502, 503, 504}:
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in ("timeout", "rate limit", "overloaded", "temporar", "429", "503", "schema")
    )


def _openai_error_detail(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    message = str(error.get("message") or "").replace("\n", " ").strip()
    return message[:300]
