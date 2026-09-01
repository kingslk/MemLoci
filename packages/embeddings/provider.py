"""可重建的本地 Embedding Provider。

v1 默认使用确定性的 hash 向量保证开发和测试可运行；生产可替换为外部 Provider，
但必须继续保存 provider、model、维度和版本元数据。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingMetadata:
    provider: str
    model: str
    dimensions: int
    version: str = "v1"


class HashEmbeddingProvider:
    def __init__(self, dimensions: int = 384, model: str = "hash-384") -> None:
        self.metadata = EmbeddingMetadata("hash", model, dimensions)

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        return [self._embed(document) for document in documents]

    def embed_query(self, query: str) -> list[float]:
        return self._embed(query)

    def _embed(self, text: str) -> list[float]:
        # 确定性哈希：相同 token 落在相同维度，便于测试；同义改写不会靠近，不能当生产语义检索。
        values = [0.0] * self.metadata.dimensions
        tokens = [token for token in text.lower().split() if token]
        if not tokens:
            return values
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in range(0, len(digest), 4):
                bucket = int.from_bytes(digest[offset : offset + 4], "big") % len(values)
                values[bucket] += 1.0 if digest[offset] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)
