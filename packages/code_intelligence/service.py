"""Code Graph 构建与增量快照服务。"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.code_intelligence.parser import ParseResult, parse_source
from packages.common.models import CodeFile, CodeRelation, CodeSymbol, Repository
from packages.embeddings.provider import HashEmbeddingProvider
from packages.gitlab.ignore import IgnoreMatcher
from packages.gitlab.mirror import RepositoryMirror


class CodeIndexer:
    def __init__(
        self,
        db: Session,
        mirror: RepositoryMirror | None = None,
        embeddings: HashEmbeddingProvider | None = None,
    ) -> None:
        self.db = db
        self.mirror = mirror
        self.embeddings = embeddings or HashEmbeddingProvider()

    def index_snapshot(
        self,
        repository: Repository,
        *,
        sha: str,
        files: dict[str, str] | None = None,
        ignore_patterns: list[str] | None = None,
    ) -> dict[str, int]:
        """重建当前 release branch 快照；历史 Evidence 不受影响。"""

        matcher = IgnoreMatcher(ignore_patterns or [])
        ignored = 0
        if files is None:
            if self.mirror is None:
                raise ValueError("没有 Mirror，无法读取仓库快照")
            paths = self.mirror.list_files(repository.id, sha)
            files = {}
            for path in paths:
                if matcher.matches(path):
                    ignored += 1
                    continue
                content = self.mirror.read_file(repository.id, sha, path)
                if content is None:
                    ignored += 1
                    continue
                files[path] = content

        # 当前快照整表重建：Code Graph 只描述“现在怎么工作”。
        # 历史事实走 Evidence，不能混在同一套节点里。
        self.db.execute(delete(CodeRelation).where(CodeRelation.repository_id == repository.id))
        self.db.execute(delete(CodeSymbol).where(CodeSymbol.repository_id == repository.id))
        self.db.execute(delete(CodeFile).where(CodeFile.repository_id == repository.id))

        indexed = 0
        symbol_count = 0
        for path, content in files.items():
            if matcher.matches(path):
                # Ignore 必须发生在 parse/embedding 之前，避免密钥、构建产物进入向量和 LLM。
                ignored += 1
                continue
            result = parse_source(path, content)
            code_file = CodeFile(
                repository_id=repository.id,
                sha=sha,
                path=path,
                language=result.language,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                ignored=False,
                parse_status="failed" if result.parse_error else "indexed",
                embedding=self.embeddings.embed_query(f"{path}\n{content}"),
                embedding_provider=self.embeddings.metadata.provider,
                embedding_model=self.embeddings.metadata.model,
                embedding_dimensions=self.embeddings.metadata.dimensions,
                embedding_version=self.embeddings.metadata.version,
            )
            self.db.add(code_file)
            self.db.flush()
            symbols = self._save_symbols(repository.id, code_file.id, result)
            symbol_count += len(symbols)
            self._save_relations(repository.id, symbols, result)
            indexed += 1
        self._resolve_relations(repository.id)
        self.db.commit()
        return {"indexed_files": indexed, "ignored_files": ignored, "symbols": symbol_count}

    def incremental_update(
        self,
        repository: Repository,
        *,
        sha: str,
        changed_files: list[dict[str, str]],
        ignore_patterns: list[str] | None = None,
    ) -> dict[str, int]:
        """只重建变更文件及其直接关系，删除和重命名不会留下旧 Symbol。"""

        if self.mirror is None:
            raise ValueError("没有 Mirror，无法执行增量更新")
        matcher = IgnoreMatcher(ignore_patterns or [])
        indexed = 0
        deleted = 0
        ignored = 0
        symbol_count = 0
        for change in changed_files:
            paths = [change.get("path", "")]
            if change.get("old_path") and change["old_path"] != change.get("path"):
                paths.insert(0, change["old_path"])
            for path in filter(None, paths):
                old_files = self.db.scalars(
                    select(CodeFile).where(
                        CodeFile.repository_id == repository.id,
                        CodeFile.path == path,
                    )
                ).all()
                old_symbol_ids = list(
                    self.db.scalars(
                        select(CodeSymbol.id).where(
                            CodeSymbol.repository_id == repository.id,
                            CodeSymbol.code_file_id.in_([item.id for item in old_files]),
                        )
                    ).all()
                )
                if old_symbol_ids:
                    self.db.execute(
                        delete(CodeRelation).where(
                            (CodeRelation.source_symbol_id.in_(old_symbol_ids))
                            | (CodeRelation.target_symbol_id.in_(old_symbol_ids))
                        )
                    )
                self.db.execute(delete(CodeSymbol).where(CodeSymbol.id.in_(old_symbol_ids)))
                self.db.execute(
                    delete(CodeFile).where(CodeFile.id.in_([item.id for item in old_files]))
                )
                if path == change.get("path") and str(change.get("status", "")).startswith("D"):
                    deleted += 1

            path = change.get("path", "")
            if not path or str(change.get("status", "")).startswith("D"):
                continue
            if matcher.matches(path):
                ignored += 1
                continue
            content = self.mirror.read_file(repository.id, sha, path)
            if content is None:
                ignored += 1
                continue
            result = parse_source(path, content)
            code_file = CodeFile(
                repository_id=repository.id,
                sha=sha,
                path=path,
                language=result.language,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                ignored=False,
                parse_status="failed" if result.parse_error else "indexed",
                embedding=self.embeddings.embed_query(f"{path}\n{content}"),
                embedding_provider=self.embeddings.metadata.provider,
                embedding_model=self.embeddings.metadata.model,
                embedding_dimensions=self.embeddings.metadata.dimensions,
                embedding_version=self.embeddings.metadata.version,
            )
            self.db.add(code_file)
            self.db.flush()
            symbols = self._save_symbols(repository.id, code_file.id, result)
            self._save_relations(repository.id, symbols, result)
            indexed += 1
            symbol_count += len(symbols)
        self._resolve_relations(repository.id)
        self.db.commit()
        return {
            "indexed_files": indexed,
            "deleted_files": deleted,
            "ignored_files": ignored,
            "symbols": symbol_count,
        }

    def _save_symbols(
        self, repository_id: int, code_file_id: int, result: ParseResult
    ) -> dict[str, int]:
        symbol_ids: dict[str, int] = {}
        for spec in result.symbols:
            symbol = CodeSymbol(
                code_file_id=code_file_id,
                repository_id=repository_id,
                name=spec.name,
                qualified_name=spec.qualified_name,
                kind=spec.kind,
                start_line=spec.start_line,
                end_line=spec.end_line,
                signature=spec.signature,
                confidence=spec.confidence,
                metadata_json={"snippet": spec.snippet} if spec.snippet else {},
            )
            self.db.add(symbol)
            self.db.flush()
            symbol_ids[spec.name] = symbol.id
        return symbol_ids

    def _save_relations(
        self, repository_id: int, symbol_ids: dict[str, int], result: ParseResult
    ) -> None:
        if not symbol_ids:
            return
        file_symbol_ids = set(symbol_ids.values())
        all_symbols = self.db.scalars(
            select(CodeSymbol).where(CodeSymbol.repository_id == repository_id)
        ).all()
        same_file = {
            symbol.name: symbol.id for symbol in all_symbols if symbol.id in file_symbol_ids
        }
        target_by_name = {symbol.name: symbol.id for symbol in all_symbols}
        source_default = next(iter(symbol_ids.values()))
        seen: set[tuple[int, str, str]] = set()
        for relation in result.relations:
            source_id = symbol_ids.get(relation.source_name, source_default)
            target_id = same_file.get(relation.target_name) or target_by_name.get(
                relation.target_name
            )
            key = (source_id, relation.target_name, relation.relation_type)
            if key in seen:
                continue
            seen.add(key)
            self.db.add(
                CodeRelation(
                    repository_id=repository_id,
                    source_symbol_id=source_id,
                    target_symbol_id=target_id,
                    target_name=relation.target_name,
                    relation_type=relation.relation_type,
                    confidence=relation.confidence,
                    is_inferred=relation.is_inferred,
                )
            )

    def _resolve_relations(self, repository_id: int) -> None:
        symbols = self.db.scalars(
            select(CodeSymbol).where(CodeSymbol.repository_id == repository_id)
        ).all()
        target_by_name = {symbol.name: symbol.id for symbol in symbols}
        unresolved = self.db.scalars(
            select(CodeRelation).where(
                CodeRelation.repository_id == repository_id,
                CodeRelation.target_symbol_id.is_(None),
            )
        ).all()
        for relation in unresolved:
            target_id = target_by_name.get(relation.target_name)
            if target_id:
                relation.target_symbol_id = target_id


SEARCH_HINT = (
    "code_search 按路径/符号 token 匹配，hash embedding 只是弱补充，不是语义搜索。"
    "请改用短路径或符号名；自然语言请用 memory_context。"
)


def query_tokens(query: str) -> list[str]:
    """拆成路径片段、标识符和中文 ≥2 字整词；中文只追加 3-gram，避免 2-gram 误伤。"""
    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        item = token.strip().lower()
        if len(item) < 2 or item in seen:
            return
        seen.add(item)
        tokens.append(item)

    for raw in re.findall(r"[a-zA-Z0-9_./-]+|[\u4e00-\u9fff]+", query):
        if re.fullmatch(r"[a-zA-Z0-9_./-]+", raw):
            add(raw)
            for part in re.split(r"[/._-]", raw):
                add(part)
            continue
        add(raw)
        if len(raw) >= 4:
            add(raw[:2])
        if len(raw) >= 3:
            for index in range(len(raw) - 2):
                add(raw[index : index + 3])
    return tokens


def _token_path_score(tokens: list[str], path: str) -> float:
    haystack = path.lower()
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    return 1.0 if hits else 0.0


def _token_symbol_score(tokens: list[str], name: str, qualified_name: str) -> float:
    lowered = name.lower()
    qualified = qualified_name.lower()
    for token in tokens:
        if token == lowered:
            return 0.95
        if token in lowered or token in qualified:
            return 0.75
    return 0.0


class CodeQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search(self, repository_id: int, query: str, limit: int = 10) -> dict[str, object]:
        """路径/符号 token 匹配优先；hash embedding 只作弱补充。"""
        tokens = query_tokens(query)
        files = self.db.scalars(
            select(CodeFile).where(
                CodeFile.repository_id == repository_id,
                CodeFile.ignored.is_(False),
            )
        ).all()
        symbols = self.db.scalars(
            select(CodeSymbol).where(CodeSymbol.repository_id == repository_id)
        ).all()
        file_by_id = {item.id: item for item in files}
        results: list[dict[str, object]] = []
        for code_file in files:
            path_score = _token_path_score(tokens, code_file.path)
            if path_score:
                results.append(
                    self._file_result(
                        code_file,
                        score=path_score,
                        reason="路径 token 匹配",
                    )
                )
        for symbol in symbols:
            score = _token_symbol_score(tokens, symbol.name, symbol.qualified_name)
            if not score:
                continue
            code_file = file_by_id.get(symbol.code_file_id)
            results.append(
                {
                    "kind": "symbol",
                    "symbol_id": symbol.id,
                    "name": symbol.name,
                    "qualified_name": symbol.qualified_name,
                    "symbol_kind": symbol.kind,
                    "file_id": symbol.code_file_id,
                    "path": code_file.path if code_file else "",
                    "score": score,
                    "reason": "Symbol 名称匹配"
                    if symbol.kind != "comment"
                    else "注释文本匹配",
                }
            )
        results.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
        hits = results[:limit]
        snapshot_sha = files[0].sha if files else None
        return {
            "results": hits,
            "count": len(hits),
            "snapshot_sha": snapshot_sha,
            "hint": None if hits else SEARCH_HINT,
        }

    def context(self, repository_id: int, symbol_name: str) -> dict[str, object]:
        matches = self._lookup_symbols(repository_id, symbol_name)
        if not matches:
            return {"found": False, "symbol": None, "relations": [], "candidates": []}
        if len(matches) > 1:
            return {
                "found": False,
                "ambiguous": True,
                "symbol": None,
                "relations": [],
                "candidates": [self._symbol_payload(item) for item in matches[:8]],
                "hint": "同名符号请改用 qualified_name 或 path::name",
            }
        symbol = matches[0]
        relations = self.db.scalars(
            select(CodeRelation).where(
                CodeRelation.repository_id == repository_id,
                (CodeRelation.source_symbol_id == symbol.id)
                | (CodeRelation.target_symbol_id == symbol.id),
            )
        ).all()
        seen: set[tuple[str, str | None, str]] = set()
        compact_relations: list[dict[str, object]] = []
        for relation in relations:
            key = (relation.relation_type, relation.target_name, relation.target_symbol_id)
            if key in seen:
                continue
            seen.add(key)
            compact_relations.append(
                {
                    "id": relation.id,
                    "type": relation.relation_type,
                    "target_name": relation.target_name,
                    "target_symbol_id": relation.target_symbol_id,
                    "confidence": relation.confidence,
                    "is_inferred": relation.is_inferred,
                }
            )
        return {
            "found": True,
            "symbol": self._symbol_payload(symbol),
            "relations": compact_relations,
            "candidates": [],
        }

    def trace(
        self, repository_id: int, source_name: str, target_name: str, max_depth: int = 4
    ) -> dict[str, object]:
        sources = self._lookup_symbols(repository_id, source_name)
        targets = self._lookup_symbols(repository_id, target_name)
        if not sources or not targets:
            return {"found": False, "paths": [], "uncertainty": "symbol_not_found"}
        target_ids = {item.id for item in targets}

        relations = self.db.scalars(
            select(CodeRelation).where(
                CodeRelation.repository_id == repository_id,
                CodeRelation.target_symbol_id.is_not(None),
            )
        ).all()
        adjacency: dict[int, list[CodeRelation]] = {}
        for relation in relations:
            adjacency.setdefault(relation.source_symbol_id, []).append(relation)

        paths: list[list[dict[str, object]]] = []
        queue: list[tuple[int, list[dict[str, object]], int]] = [
            (source.id, [], 0) for source in sources
        ]
        visited: set[tuple[int, int]] = set()
        while queue and len(paths) < 10:
            current_id, path, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for relation in adjacency.get(current_id, []):
                next_path = path + [
                    {
                        "relation": relation.relation_type,
                        "target_name": relation.target_name,
                        "confidence": relation.confidence,
                        "inferred": relation.is_inferred,
                    }
                ]
                if relation.target_symbol_id in target_ids:
                    paths.append(next_path)
                    continue
                state = (relation.target_symbol_id or -1, depth + 1)
                if state not in visited:
                    visited.add(state)
                    queue.append((relation.target_symbol_id or -1, next_path, depth + 1))
        uncertainty = (
            "contains_inferred_relation"
            if any(item.get("inferred") for path in paths for item in path)
            else "static_only"
        )
        return {"found": bool(paths), "paths": paths, "uncertainty": uncertainty}

    def _lookup_symbols(self, repository_id: int, symbol_name: str) -> list[CodeSymbol]:
        return list(
            self.db.scalars(
                select(CodeSymbol).where(
                    CodeSymbol.repository_id == repository_id,
                    (CodeSymbol.name == symbol_name) | (CodeSymbol.qualified_name == symbol_name),
                )
            ).all()
        )

    def _symbol_payload(self, symbol: CodeSymbol) -> dict[str, object]:
        code_file = self.db.get(CodeFile, symbol.code_file_id)
        metadata = symbol.metadata_json or {}
        return {
            "id": symbol.id,
            "name": symbol.name,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind,
            "file_id": symbol.code_file_id,
            "path": code_file.path if code_file else "",
            "lines": [symbol.start_line, symbol.end_line],
            "signature": symbol.signature,
            "snippet": metadata.get("snippet") or symbol.signature,
            "confidence": symbol.confidence,
        }

    def graph(self, repository_id: int, *, limit: int = 200) -> dict[str, list[dict[str, object]]]:
        symbols = self.db.scalars(
            select(CodeSymbol)
            .where(CodeSymbol.repository_id == repository_id)
            .order_by(CodeSymbol.id)
            .limit(limit)
        ).all()
        symbol_ids = {symbol.id for symbol in symbols}
        relations = self.db.scalars(
            select(CodeRelation).where(
                CodeRelation.repository_id == repository_id,
                CodeRelation.source_symbol_id.in_(symbol_ids),
            )
        ).all()
        return {
            "nodes": [
                {
                    "id": f"symbol:{symbol.id}",
                    "label": symbol.name,
                    "kind": symbol.kind,
                    "confidence": symbol.confidence,
                }
                for symbol in symbols
            ],
            "edges": [
                {
                    "id": f"relation:{relation.id}",
                    "source": f"symbol:{relation.source_symbol_id}",
                    "target": (
                        f"symbol:{relation.target_symbol_id}"
                        if relation.target_symbol_id
                        else f"name:{relation.target_name}"
                    ),
                    "type": relation.relation_type,
                    "confidence": relation.confidence,
                    "inferred": relation.is_inferred,
                }
                for relation in relations
            ],
        }

    @staticmethod
    def _file_result(code_file: CodeFile, *, score: float, reason: str) -> dict[str, object]:
        return {
            "kind": "file",
            "file_id": code_file.id,
            "path": code_file.path,
            "language": code_file.language,
            "sha": code_file.sha,
            "score": score,
            "reason": reason,
        }
