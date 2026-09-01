"""首批代码解析器。

tree-sitter 负责语法可解析性；符号抽取保持统一、可解释的最小模型。
语言未安装或语法节点不支持时只降级为文本/正则结果，不伪造精确调用关系。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".swift": "swift",
    ".m": "objc",
    ".mm": "objc",
    ".h": "objc",
}

TREE_SITTER_LANGUAGE = {
    "objc": "objc",
}


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    confidence: float = 1.0
    snippet: str = ""


@dataclass(frozen=True)
class RelationSpec:
    source_name: str
    target_name: str
    relation_type: str
    confidence: float
    is_inferred: bool = False


@dataclass
class ParseResult:
    path: str
    language: str
    symbols: list[SymbolSpec] = field(default_factory=list)
    relations: list[RelationSpec] = field(default_factory=list)
    backend: str = "regex"
    parse_error: str | None = None


def infer_language(path: str) -> str:
    return LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower(), "unknown")


def parse_source(path: str, content: str) -> ParseResult:
    """先用 tree-sitter 验证语法，再用统一规则抽符号。解析失败时降级，不伪造调用边。"""
    language = infer_language(path)
    if language == "unknown":
        result = ParseResult(path=path, language=language, backend="text")
        result.symbols = _with_ranges(path, content, _extract_comment_symbols(content))
        return result

    backend, parse_error = _validate_with_tree_sitter(language, content)
    result = ParseResult(path=path, language=language, backend=backend, parse_error=parse_error)
    symbols = _extract_symbols(language, content)
    symbols.extend(_extract_comment_symbols(content))
    result.symbols = _with_ranges(path, content, symbols)
    result.relations = _extract_relations(language, content, result.symbols)
    return result


def _validate_with_tree_sitter(language: str, content: str) -> tuple[str, str | None]:
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(TREE_SITTER_LANGUAGE.get(language, language))
        tree = parser.parse(content.encode("utf-8"))
        if tree.root_node.has_error:
            return "tree-sitter", "语法树包含错误节点；关系只按低置信度规则抽取"
        return "tree-sitter", None
    except Exception as exc:  # 解析器安装或语言包差异不能阻塞文本索引
        return "regex", f"tree-sitter unavailable: {exc.__class__.__name__}"


def _extract_symbols(language: str, content: str) -> list[SymbolSpec]:
    lines = content.splitlines()
    patterns: list[tuple[str, str]] = []
    if language == "python":
        patterns = [
            ("function", r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*(\(.*)$"),
            ("class", r"^\s*class\s+([A-Za-z_]\w*)\s*(?:\([^)]*\))?:"),
        ]
    elif language in {"javascript", "typescript"}:
        patterns = [
            (
                "function",
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(\(.*)$",
            ),
            ("class", r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"),
            (
                "function",
                r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function\b|forwardRef\b)",
            ),
            (
                "component",
                r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let)\s+([A-Z][A-Za-z0-9_$]*)\s*(?::[^=]+)?=",
            ),
        ]
    elif language == "go":
        patterns = [
            ("function", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*(\(.*)$"),
            ("type", r"^\s*type\s+([A-Za-z_]\w*)\s+struct"),
        ]
    elif language == "java":
        patterns = [
            ("class", r"^\s*(?:public\s+)?class\s+([A-Za-z_]\w*)"),
            (
                "function",
                r"^\s*(?:public|private|protected|static|\s)+[\w<>,\[\]]+\s+([A-Za-z_]\w*)\s*\(",
            ),
        ]
    elif language == "swift":
        patterns = [
            (
                "class",
                r"^\s*(?:(?:public|private|internal|open|fileprivate|final)\s+)*class\s+([A-Za-z_]\w*)",
            ),
            (
                "type",
                r"^\s*(?:(?:public|private|internal|open|fileprivate)\s+)*struct\s+([A-Za-z_]\w*)",
            ),
            (
                "function",
                r"^\s*(?:@objc\s+)?(?:(?:public|private|internal|open|fileprivate|override|static|class)\s+)*func\s+([A-Za-z_]\w*)",
            ),
        ]
    elif language == "objc":
        patterns = [
            ("class", r"^@interface\s+([A-Za-z_]\w*)"),
            ("function", r"^[-+]\s*\([^;]*?\)\s*([A-Za-z_]\w*)"),
        ]

    symbols: list[SymbolSpec] = []
    for line_number, line in enumerate(lines, start=1):
        for kind, pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            name = match.group(1)
            symbols.append(
                SymbolSpec(
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    start_line=line_number,
                    end_line=line_number,
                    signature=line.strip(),
                    confidence=0.95 if kind != "component" else 0.85,
                )
            )
            break
    return symbols


_COMMENT_LINE = re.compile(r"^\s*(?://|#|--)\s*(.+)$")
_NOTE_MARK = re.compile(r"\b(TODO|FIXME|HACK|NOTE|XXX)\b", re.I)
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _extract_comment_symbols(content: str) -> list[SymbolSpec]:
    """把带 TODO/中文的注释送进文本索引，低置信，不参与调用关系。"""
    symbols: list[SymbolSpec] = []
    seen: set[str] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = _COMMENT_LINE.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        if len(text) < 4:
            continue
        if not _NOTE_MARK.search(text) and not _CJK.search(text):
            continue
        name = text[:80]
        if name in seen:
            continue
        seen.add(name)
        symbols.append(
            SymbolSpec(
                name=name,
                qualified_name=name,
                kind="comment",
                start_line=line_number,
                end_line=line_number,
                signature=line.strip(),
                confidence=0.35,
                snippet=text[:240],
            )
        )
    return symbols


def _with_ranges(path: str, content: str, symbols: list[SymbolSpec]) -> list[SymbolSpec]:
    lines = content.splitlines()
    line_count = len(lines)
    code_symbols = [item for item in symbols if item.kind != "comment"]
    comments = [item for item in symbols if item.kind == "comment"]
    ordered = sorted(code_symbols, key=lambda item: (item.start_line, item.name))
    closed: list[SymbolSpec] = []
    for index, spec in enumerate(ordered):
        end_line = line_count or spec.start_line
        if index + 1 < len(ordered):
            end_line = max(spec.start_line, ordered[index + 1].start_line - 1)
        snippet = "\n".join(lines[spec.start_line - 1 : min(end_line, spec.start_line + 4)])
        closed.append(
            replace(
                spec,
                end_line=end_line,
                qualified_name=f"{path}::{spec.name}",
                snippet=snippet[:500],
            )
        )
    return [
        replace(item, qualified_name=f"{path}::{item.name}") if not item.qualified_name.startswith(path) else item
        for item in [*closed, *comments]
    ]


def _extract_relations(
    language: str, content: str, symbols: list[SymbolSpec]
) -> list[RelationSpec]:
    relations: list[RelationSpec] = []
    callable_symbols = [item for item in symbols if item.kind != "comment"]
    symbol_names = {symbol.name for symbol in callable_symbols}
    import_patterns = {
        "python": [r"^\s*import\s+([A-Za-z_][\w.]*)", r"^\s*from\s+([A-Za-z_][\w.]*)\s+import"],
        "javascript": [r"^\s*import\s+.*?\s+from\s+[\"']([^\"']+)", r"^\s*import\s+[\"']([^\"']+)"],
        "typescript": [r"^\s*import\s+.*?\s+from\s+[\"']([^\"']+)", r"^\s*import\s+[\"']([^\"']+)"],
        "go": [r"^\s*import\s+[\"']([^\"']+)"],
        "java": [r"^\s*import\s+([\w.]+)"],
        "swift": [r"^\s*import\s+([A-Za-z_]\w*)"],
        "objc": [r"^#import\s+[\"'<]([^\"'>]+)[\"'>]"],
    }
    seen: set[tuple[str, str, str]] = set()

    def add(spec: RelationSpec) -> None:
        key = (spec.source_name, spec.target_name, spec.relation_type)
        if key in seen:
            return
        seen.add(key)
        relations.append(spec)

    for line in content.splitlines():
        for pattern in import_patterns.get(language, []):
            match = re.search(pattern, line)
            if match:
                add(
                    RelationSpec(
                        source_name=callable_symbols[0].name if callable_symbols else "<file>",
                        target_name=match.group(1),
                        relation_type="imports",
                        confidence=0.9,
                    )
                )
                break

    call_keywords = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "def",
        "function",
        "print",
        "guard",
        "class",
        "struct",
    }
    lines = content.splitlines()
    for symbol in callable_symbols:
        body = "\n".join(lines[symbol.start_line - 1 : symbol.end_line])
        for target in re.findall(r"\b([A-Za-z_$]\w*)\s*\(", body):
            if target in call_keywords or target == symbol.name or target not in symbol_names:
                continue
            add(
                RelationSpec(
                    source_name=symbol.name,
                    target_name=target,
                    relation_type="calls",
                    confidence=0.55,
                    is_inferred=True,
                )
            )
    return relations
