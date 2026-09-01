"""统一 Ignore 规则，所有分析入口共用这一实现。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 图片对 Code Graph / Embedding 没有用，默认排除；仓库规则写在后面，可用 ! 放行。
DEFAULT_IGNORE_PATTERNS = (
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.bmp",
    "**/*.ico",
    "**/*.svg",
    "**/*.avif",
    "**/*.tif",
    "**/*.tiff",
    "**/*.heic",
    "**/*.heif",
    "**/*.jfif",
    "**/*.apng",
    "**/release/**",
    "**/.next/**",
    "**/dist/**",
    "**/node_modules/**",
    "**/*.min.js",
)


def _compile_pattern(pattern: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    """把 Ignore 模式编成正则；除 `/` 开头外，都匹配任意目录深度。"""
    pattern = pattern.strip().replace("\\", "/")
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]
    pieces: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            index += 2
            if index < len(pattern) and pattern[index] == "/":
                pieces.append("(?:.*/)?")
                index += 1
            else:
                pieces.append(".*")
            continue
        if char == "*":
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        else:
            pieces.append(re.escape(char))
        index += 1
    expression = "".join(pieces)
    if not anchored and not pattern.startswith("**/"):
        expression = rf"(?:.*/)?{expression}"
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(rf"^{expression}(?:/.*)?$", flags)


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool
    matcher: re.Pattern[str]


class IgnoreMatcher:
    """按 Gitignore 风格的最后匹配规则判断路径。"""

    def __init__(
        self,
        patterns: list[str] | tuple[str, ...],
        *,
        include_defaults: bool = True,
    ) -> None:
        rules: list[IgnoreRule] = []
        if include_defaults:
            for pattern in DEFAULT_IGNORE_PATTERNS:
                rules.append(
                    IgnoreRule(pattern, False, _compile_pattern(pattern, ignore_case=True))
                )
        for raw in patterns:
            pattern = raw.strip()
            if not pattern or pattern.startswith("#"):
                continue
            negated = pattern.startswith("!")
            if negated:
                pattern = pattern[1:]
            rules.append(IgnoreRule(pattern, negated, _compile_pattern(pattern)))
        self.rules = tuple(rules)

    def matches(self, path: str) -> bool:
        # 后写的规则覆盖先写的；`!` 用于把被忽略目录里的例外文件重新纳入分析。
        normalized = path.strip().lstrip("./").replace("\\", "/")
        ignored = False
        for rule in self.rules:
            if rule.matcher.match(normalized):
                ignored = not rule.negated
        return ignored

    def filter_paths(self, paths: list[str]) -> tuple[list[str], list[str]]:
        included: list[str] = []
        excluded: list[str] = []
        for path in paths:
            (excluded if self.matches(path) else included).append(path)
        return included, excluded
