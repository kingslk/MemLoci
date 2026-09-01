"""渐进式图谱查询；图数据仍以关系模型和 Evidence 为事实源。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.models import (
    CodeFile,
    CodeRelation,
    CodeSymbol,
    Evidence,
    EvidenceFile,
    Memory,
    MemoryEvidence,
    Repository,
    Topic,
)


class GraphService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def code(
        self, repository_id: int, *, limit: int = 200, prefix: str = ""
    ) -> dict[str, object]:
        """按目录一层一层展开，不再按路径字母序切前 80 个文件。"""

        prefix = prefix.strip("/")
        paths = list(
            self.db.execute(
                select(CodeFile.id, CodeFile.path, CodeFile.language).where(
                    CodeFile.repository_id == repository_id
                )
            ).all()
        )
        file_total = len(paths)
        child_dirs: dict[str, int] = {}
        files: list[tuple[int, str, str]] = []
        prefix_slash = f"{prefix}/" if prefix else ""
        for file_id, path, language in paths:
            if prefix and path != prefix and not path.startswith(prefix_slash):
                continue
            relative = path[len(prefix_slash) :] if prefix else path
            if not relative:
                continue
            if "/" in relative:
                first = relative.split("/", 1)[0]
                child = f"{prefix_slash}{first}" if prefix else first
                child_dirs[child] = child_dirs.get(child, 0) + 1
            else:
                files.append((file_id, path, language))

        nodes: list[dict[str, object]] = []
        edges: list[dict[str, object]] = []
        root_id = f"dir:{repository_id}:{prefix}" if prefix else f"dir:{repository_id}:"
        nodes.append(
            {
                "id": root_id,
                "label": prefix.rsplit("/", 1)[-1] if prefix else "仓库根",
                "kind": "code",
                "subtype": "directory",
                "path": prefix,
                "summary": "当前目录",
                "repository_id": repository_id,
            }
        )
        for directory, count in sorted(child_dirs.items()):
            node_id = f"dir:{repository_id}:{directory}"
            nodes.append(
                {
                    "id": node_id,
                    "label": directory.rsplit("/", 1)[-1],
                    "kind": "code",
                    "subtype": "directory",
                    "path": directory,
                    "summary": f"{count} 个文件",
                    "repository_id": repository_id,
                }
            )
            edges.append(
                {
                    "id": f"contains-dir:{directory}",
                    "source": root_id,
                    "target": node_id,
                    "type": "contains",
                    "confidence": 1.0,
                }
            )
        visible_files = files[: max(limit, 1)]
        for file_id, path, language in visible_files:
            node_id = f"code:{file_id}"
            nodes.append(
                {
                    "id": node_id,
                    "label": path.rsplit("/", 1)[-1],
                    "kind": "code",
                    "subtype": "file",
                    "path": path,
                    "summary": language,
                    "repository_id": repository_id,
                }
            )
            edges.append(
                {
                    "id": f"contains:{file_id}",
                    "source": root_id,
                    "target": node_id,
                    "type": "contains",
                    "confidence": 1.0,
                }
            )
        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "prefix": prefix,
                "parent": prefix.rsplit("/", 1)[0] if "/" in prefix else "",
                "file_total": file_total,
                "dir_count": len(child_dirs),
                "file_count": len(files),
                "truncated": len(files) > len(visible_files),
            },
        }

    def code_file(self, repository_id: int, file_id: int) -> dict[str, object]:
        code_file = self.db.get(CodeFile, file_id)
        if code_file is None or code_file.repository_id != repository_id:
            return {"nodes": [], "edges": [], "meta": {"kind": "code_file", "file_id": file_id}}
        symbols = list(
            self.db.scalars(
                select(CodeSymbol).where(
                    CodeSymbol.repository_id == repository_id,
                    CodeSymbol.code_file_id == file_id,
                )
            ).all()
        )
        symbol_ids = {item.id for item in symbols}
        relations = (
            list(
                self.db.scalars(
                    select(CodeRelation).where(
                        CodeRelation.repository_id == repository_id,
                        (CodeRelation.source_symbol_id.in_(symbol_ids))
                        | (CodeRelation.target_symbol_id.in_(symbol_ids)),
                    )
                ).all()
            )
            if symbol_ids
            else []
        )
        neighbor_ids = {
            item.target_symbol_id
            for item in relations
            if item.target_symbol_id and item.target_symbol_id not in symbol_ids
        } | {
            item.source_symbol_id
            for item in relations
            if item.source_symbol_id not in symbol_ids
        }
        neighbors = (
            list(self.db.scalars(select(CodeSymbol).where(CodeSymbol.id.in_(neighbor_ids))).all())
            if neighbor_ids
            else []
        )
        file_node = {
            "id": f"code:{code_file.id}",
            "label": code_file.path.rsplit("/", 1)[-1],
            "kind": "code",
            "subtype": "file",
            "path": code_file.path,
            "summary": code_file.language,
            "repository_id": repository_id,
        }
        nodes: list[dict[str, object]] = [file_node]
        edges: list[dict[str, object]] = []
        for symbol in [*symbols, *neighbors]:
            nodes.append(
                {
                    "id": f"symbol:{symbol.id}",
                    "label": symbol.name,
                    "kind": "code",
                    "subtype": "symbol",
                    "path": code_file.path if symbol.code_file_id == file_id else None,
                    "summary": symbol.kind,
                    "repository_id": repository_id,
                }
            )
            if symbol.code_file_id == file_id:
                edges.append(
                    {
                        "id": f"contains-symbol:{symbol.id}",
                        "source": file_node["id"],
                        "target": f"symbol:{symbol.id}",
                        "type": "contains",
                        "confidence": 1.0,
                    }
                )
        unresolved: set[str] = set()
        for relation in relations:
            if relation.target_symbol_id:
                target = f"symbol:{relation.target_symbol_id}"
            else:
                target = f"unresolved:{repository_id}:{relation.target_name}"
                if target not in unresolved:
                    unresolved.add(target)
                    nodes.append(
                        {
                            "id": target,
                            "label": relation.target_name,
                            "kind": "code",
                            "subtype": "unresolved",
                            "summary": "未能解析到符号",
                            "repository_id": repository_id,
                        }
                    )
            edges.append(
                {
                    "id": f"relation:{relation.id}",
                    "source": f"symbol:{relation.source_symbol_id}",
                    "target": target,
                    "type": relation.relation_type,
                    "confidence": relation.confidence,
                }
            )
        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "kind": "code_file",
                "file_id": file_id,
                "path": code_file.path,
                "symbol_count": len(symbols),
            },
        }

    def memory(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        limit: int = 200,
    ) -> dict[str, list[dict[str, object]]]:
        memories = self._memories(project_id, repository_id, limit)
        topic_ids = {memory.topic_id for memory in memories if memory.topic_id}
        topics = (
            list(self.db.scalars(select(Topic).where(Topic.id.in_(topic_ids))).all())
            if topic_ids
            else []
        )
        nodes: list[dict[str, object]] = [
            {
                "id": f"topic:{topic.id}",
                "label": topic.name,
                "kind": "topic",
                "summary": topic.description,
            }
            for topic in topics
        ]
        nodes.extend(
            {
                "id": f"memory:{memory.id}",
                "label": memory.title,
                "kind": "memory",
                "status": memory.status,
                "confidence": memory.confidence,
                "repository_id": memory.repository_id,
                "topic": next(
                    (topic.name for topic in topics if topic.id == memory.topic_id),
                    None,
                ),
                "summary": memory.problem,
            }
            for memory in memories
        )
        edges: list[dict[str, object]] = [
            {
                "id": f"belongs:{memory.id}",
                "source": f"memory:{memory.id}",
                "target": f"topic:{memory.topic_id}",
                "type": "belongs_to",
                "confidence": 1.0,
            }
            for memory in memories
            if memory.topic_id
        ]
        return {"nodes": nodes, "edges": edges, "meta": {"kind": "memory"}}

    def combined(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        limit: int = 200,
        focus: str | None = None,
        hops: int = 2,
    ) -> dict[str, list[dict[str, object]]]:
        """默认是记忆的邻域：主题、证据、相关文件。不再倒全部提交。"""
        memories = self._memories(project_id, repository_id, limit)
        if focus:
            memories = self._focus_memories(memories, focus) or memories[:1]
        memory_ids = {memory.id for memory in memories}
        topic_ids = {memory.topic_id for memory in memories if memory.topic_id}
        topics = (
            list(self.db.scalars(select(Topic).where(Topic.id.in_(topic_ids))).all())
            if topic_ids
            else []
        )
        links = (
            list(
                self.db.scalars(
                    select(MemoryEvidence).where(MemoryEvidence.memory_id.in_(memory_ids))
                ).all()
            )
            if memory_ids
            else []
        )
        evidence_ids = {item.evidence_id for item in links}
        evidence = (
            list(self.db.scalars(select(Evidence).where(Evidence.id.in_(evidence_ids))).all())
            if evidence_ids
            else []
        )
        evidence_files = (
            list(
                self.db.scalars(
                    select(EvidenceFile).where(EvidenceFile.evidence_id.in_(evidence_ids))
                ).all()
            )
            if evidence_ids
            else []
        )
        paths = {item.path for item in evidence_files if item.path}
        repositories = self._repositories(project_id, repository_id)
        repository_ids = [repository.id for repository in repositories]
        files = []
        if repository_ids:
            statement = select(CodeFile).where(CodeFile.repository_id.in_(repository_ids))
            if paths:
                statement = statement.where(CodeFile.path.in_(paths))
            files = list(self.db.scalars(statement.order_by(CodeFile.path).limit(40)).all())
        nodes: list[dict[str, object]] = [
            {
                "id": f"topic:{topic.id}",
                "label": topic.name,
                "kind": "topic",
                "summary": topic.description,
            }
            for topic in topics
        ]
        nodes.extend(
            {
                "id": f"memory:{memory.id}",
                "label": memory.title,
                "kind": "memory",
                "status": memory.status,
                "confidence": memory.confidence,
                "repository_id": memory.repository_id,
                "summary": memory.problem,
            }
            for memory in memories
        )
        nodes.extend(
            {
                "id": f"evidence:{item.id}",
                "label": item.title,
                "kind": "evidence",
                "confidence": item.importance_score,
                "repository_id": item.repository_id,
                "summary": item.summary,
            }
            for item in evidence
        )
        nodes.extend(
            {
                "id": f"code:{item.id}",
                "label": item.path.rsplit("/", 1)[-1],
                "kind": "code",
                "subtype": "file",
                "path": item.path,
                "repository_id": item.repository_id,
                "summary": item.language,
            }
            for item in files
        )
        path_to_file = {item.path: item for item in files}
        edges: list[dict[str, object]] = []
        missing_paths: set[str] = set()
        for file_row in evidence_files:
            if file_row.path in path_to_file or file_row.path in missing_paths:
                continue
            missing_paths.add(file_row.path)
            nodes.append(
                {
                    "id": f"missing:{file_row.path}",
                    "label": file_row.path.rsplit("/", 1)[-1],
                    "kind": "code",
                    "subtype": "missing",
                    "path": file_row.path,
                    "summary": "路径已变",
                    "repository_id": None,
                }
            )
        edges.extend(
            {
                "id": f"belongs:{memory.id}",
                "source": f"memory:{memory.id}",
                "target": f"topic:{memory.topic_id}",
                "type": "belongs_to",
                "confidence": 1.0,
            }
            for memory in memories
            if memory.topic_id
        )
        edges.extend(
            {
                "id": f"memory-evidence:{item.memory_id}:{item.evidence_id}",
                "source": f"memory:{item.memory_id}",
                "target": f"evidence:{item.evidence_id}",
                "type": item.role,
                "confidence": 1.0,
            }
            for item in links
        )
        edges.extend(
            {
                "id": f"evidence-file:{file_row.id}",
                "source": f"evidence:{file_row.evidence_id}",
                "target": (
                    f"code:{path_to_file[file_row.path].id}"
                    if file_row.path in path_to_file
                    else f"missing:{file_row.path}"
                ),
                "type": "touches",
                "confidence": 1.0 if file_row.path in path_to_file else 0.3,
            }
            for file_row in evidence_files
            if file_row.path in path_to_file or file_row.path in missing_paths
        )
        _ = hops
        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {"kind": "combined", "focus": focus, "hops": hops},
        }

    def _focus_memories(self, memories: list[Memory], focus: str) -> list[Memory]:
        kind, _, raw_id = focus.partition(":")
        if not raw_id.isdigit():
            return []
        focus_id = int(raw_id)
        if kind == "memory":
            return [item for item in memories if item.id == focus_id]
        if kind == "topic":
            return [item for item in memories if item.topic_id == focus_id]
        return []

    def _repositories(self, project_id: int, repository_id: int | None) -> list[Repository]:
        statement = select(Repository).where(Repository.project_id == project_id)
        if repository_id is not None:
            statement = statement.where(Repository.id == repository_id)
        return list(self.db.scalars(statement.order_by(Repository.id)).all())

    def _memories(self, project_id: int, repository_id: int | None, limit: int) -> list[Memory]:
        statement = select(Memory).where(Memory.project_id == project_id)
        if repository_id is not None:
            statement = statement.where(
                (Memory.repository_id == repository_id) | Memory.repository_id.is_(None)
            )
        return list(
            self.db.scalars(statement.order_by(Memory.updated_at.desc()).limit(limit)).all()
        )
