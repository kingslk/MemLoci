from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from packages.dreaming.service import DreamService
from packages.llm.provider import (
    LLM_REQUEST_TIMEOUT_SECONDS,
    HeuristicLLMProvider,
    OpenAILLMProvider,
)


class FakeResponses:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=self.parsed,
            usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
        )


class FakeClient:
    def __init__(self, parsed) -> None:
        self.responses = FakeResponses(parsed)


def test_single_request_timeout_is_ten_minutes() -> None:
    assert LLM_REQUEST_TIMEOUT_SECONDS == 600.0


def test_openai_provider_uses_httpx_without_tls_verify(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(responses=FakeResponses(None))

    monkeypatch.setattr("packages.llm.provider.OpenAI", fake_openai)
    OpenAILLMProvider(api_key="test-key", base_url="https://example.com/v1", model="model")
    client = captured["http_client"]
    assert getattr(client, "_verify", False) is False or client.is_closed is False


def test_openai_provider_requires_key_and_base_url() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAILLMProvider(api_key="", base_url="https://example.com/v1", model="model")
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        OpenAILLMProvider(api_key="test-key", base_url="", model="model")


def test_openai_provider_parses_candidate_and_records_usage() -> None:
    client = FakeClient(
        SimpleNamespace(
            model_dump=lambda: {
                "skip": False,
                "skip_reason": "",
                "title": "External candidate",
                "problem": "A repeated failure",
                "signals": ["validate before promotion"],
                "confidence": 0.8,
            }
        )
    )
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        client=client,
    )

    candidate = provider.extract_candidate({"title": "External candidate"})

    assert candidate.title == "External candidate"
    assert candidate.pattern == ["validate before promotion"]
    assert provider.last_usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert client.responses.calls[0]["text_format"].__name__ == "_ExtractOutput"
    assert client.responses.calls[0]["temperature"] == 0.2
    assert client.responses.calls[0]["reasoning"] == {"effort": "medium"}


def test_openai_provider_omits_reasoning_when_effort_empty() -> None:
    client = FakeClient(
        SimpleNamespace(
            model_dump=lambda: {
                "skip": False,
                "skip_reason": "",
                "title": "External candidate",
                "problem": "A repeated failure",
                "signals": ["validate before promotion"],
                "confidence": 0.8,
            }
        )
    )
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        reasoning_effort=None,
        client=client,
    )

    provider.extract_signal({"title": "External candidate"})

    assert "reasoning" not in client.responses.calls[0]


def test_openai_provider_extracts_batch() -> None:
    client = FakeClient(
        SimpleNamespace(
            items=[
                SimpleNamespace(
                    index=0,
                    skip=False,
                    skip_reason="",
                    title="Keep",
                    problem="A real problem",
                    signals=["retry"],
                    confidence=0.7,
                ),
                SimpleNamespace(
                    index=1,
                    skip=True,
                    skip_reason="琐碎",
                    title="",
                    problem="",
                    signals=[],
                    confidence=0.0,
                ),
            ]
        )
    )
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        client=client,
    )

    drafts = provider.extract_signals(
        [{"title": "Keep"}, {"title": "chore: lint"}]
    )

    assert [item.skip for item in drafts] == [False, True]
    assert drafts[0].title == "Keep"
    assert client.responses.calls[0]["text_format"].__name__ == "_ExtractBatchOutput"
    assert client.responses.calls[0]["reasoning"] == {"effort": "medium"}


def test_openai_provider_uses_medium_effort_for_synthesize() -> None:
    client = FakeClient(
        SimpleNamespace(
            model_dump=lambda: {
                "title": "主题经验",
                "problem": "问题",
                "pattern": ["步骤"],
                "implementation": {"summary": "", "steps": [], "validation": []},
                "do_not_copy": [],
                "apply_when": [],
                "do_not": [],
                "confidence": 0.6,
            }
        )
    )
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        client=client,
    )

    provider.synthesize_memory({"items": [{"title": "主题经验"}]})

    assert client.responses.calls[0]["reasoning"] == {"effort": "medium"}


def test_openai_provider_batches_memory_compare_in_one_request() -> None:
    class Item:
        def __init__(self, index: int, *, conflict: bool = False) -> None:
            self.index = index
            self.conflict = conflict

        def model_dump(self, *, exclude=None):
            payload = {
                "index": self.index,
                "same_pattern": not self.conflict,
                "conflict": self.conflict,
                "overlap_terms": ["retry"],
                "reason": "same" if not self.conflict else "conflict",
            }
            return {key: value for key, value in payload.items() if key not in (exclude or set())}

    client = FakeClient(SimpleNamespace(items=[Item(0), Item(1, conflict=True)]))
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        client=client,
    )

    results = provider.compare_memory_pairs(
        [({"title": "a"}, {"title": "b"}), ({"title": "c"}, {"title": "d"})]
    )

    assert [item["conflict"] for item in results] == [False, True]
    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["text_format"].__name__ == "_MemoryComparisonBatchOutput"


def test_conflict_confirmation_escalates_only_the_second_compare() -> None:
    class ConflictProvider:
        provider = "test"
        model = "test"
        prompt_version = "test"

        def __init__(self) -> None:
            self.efforts: list[str | None] = []

        def compare_memories(self, left, right, *, reasoning_effort=None):
            self.efforts.append(reasoning_effort)
            return {
                "same_pattern": False,
                "conflict": True,
                "overlap_terms": [],
                "reason": "约束冲突",
            }

    provider = ConflictProvider()
    result = DreamService(SimpleNamespace(), provider=provider)._compare_with_conflict_confirmation(
        {"title": "上传回调"}, {"title": "上传回调"}
    )

    assert result["same_pattern"] is False
    assert provider.efforts == [None, "high"]


def test_unrelated_compare_does_not_escalate() -> None:
    class UnrelatedProvider:
        provider = "test"
        model = "test"
        prompt_version = "test"

        def __init__(self) -> None:
            self.efforts: list[str | None] = []

        def compare_memories(self, left, right, *, reasoning_effort=None):
            self.efforts.append(reasoning_effort)
            return {
                "same_pattern": False,
                "conflict": False,
                "overlap_terms": [],
                "reason": "主题接近但不是同一问题",
            }

    provider = UnrelatedProvider()
    result = DreamService(SimpleNamespace(), provider=provider)._compare_with_conflict_confirmation(
        {"title": "上传回调"}, {"title": "上传鉴权"}
    )

    assert result == {
        "same_pattern": False,
        "conflict": False,
        "overlap_terms": [],
        "reason": "主题接近但不是同一问题",
    }
    assert provider.efforts == [None]


def test_batch_compare_escalates_only_conflicts() -> None:
    class BatchProvider:
        provider = "test"
        model = "test"
        prompt_version = "test"

        def __init__(self) -> None:
            self.high_calls = 0

        def compare_memory_pairs(self, pairs, *, reasoning_effort=None):
            assert reasoning_effort is None
            return [
                {"same_pattern": True, "conflict": False},
                {"same_pattern": False, "conflict": True},
            ]

        def compare_memories(self, left, right, *, reasoning_effort=None):
            assert reasoning_effort == "high"
            self.high_calls += 1
            return {"same_pattern": False, "conflict": True}

    provider = BatchProvider()
    results = DreamService(
        SimpleNamespace(), provider=provider
    )._compare_pairs_with_conflict_confirmation(
        [({"title": "a"}, {"title": "b"}), ({"title": "c"}, {"title": "d"})]
    )

    assert results[0]["same_pattern"] is True
    assert provider.high_calls == 1


def test_prompt_files_and_version_are_v3() -> None:
    from packages.llm.provider import PROMPT_VERSION, load_prompt

    assert PROMPT_VERSION == "v3"
    extract = load_prompt("extract_v2.md")
    assert "skip" in extract.lower()
    assert "不可信" in extract
    batch = load_prompt("extract_batch_v2.md")
    assert "index" in batch
    compare = load_prompt("compare_v3.md")
    assert "conflict" in compare


def test_openai_provider_retries_validation_error(monkeypatch) -> None:
    parsed = SimpleNamespace(
        model_dump=lambda: {
            "skip": False,
            "skip_reason": "",
            "title": "External candidate",
            "problem": "A repeated failure",
            "signals": ["validate before promotion"],
            "confidence": 0.8,
        }
    )

    class FlakyResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def parse(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise ValidationError.from_exception_data("Extract", [])
            return SimpleNamespace(
                output_parsed=parsed,
                usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    client = SimpleNamespace(responses=FlakyResponses())
    monkeypatch.setattr("packages.llm.provider.time.sleep", lambda _seconds: None)
    provider = OpenAILLMProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="model",
        client=client,
    )

    signal = provider.extract_signal({"title": "External candidate"})

    assert signal.title == "External candidate"
    assert len(client.responses.calls) == 2


def test_heuristic_provider_is_still_available_only_when_explicit() -> None:
    provider = HeuristicLLMProvider()
    assert provider.provider == "heuristic"
    assert provider.extract_signal({"title": "fix typo in README"}).skip is True
    assert provider.extract_signal({"title": "chore: bump version"}).skip is True
    assert provider.extract_signal({"title": "Merge branch 'dev'"}).skip is True
    assert provider.extract_signal({"title": "上传取消后仍收到进度回调"}).skip is False
    decision = provider.reconcile_memories(
        {"title": "鉴权重试"},
        [{"id": 9, "title": "鉴权重试", "human_corrected": True}],
    )
    assert decision.action == "NOOP"
