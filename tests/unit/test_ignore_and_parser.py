from packages.code_intelligence.parser import parse_source
from packages.gitlab.ignore import IgnoreMatcher


def test_ignore_last_rule_and_nested_paths() -> None:
    matcher = IgnoreMatcher(["dist/**", "**/*.min.js", "!dist/keep.js"])

    assert matcher.matches("dist/bundle.js")
    assert not matcher.matches("dist/keep.js")
    assert matcher.matches("src/vendor/app.min.js")
    assert not matcher.matches("src/app.ts")


def test_ignore_directory_patterns_match_the_same_at_any_depth() -> None:
    paths = [
        "release/bundle.js",
        "web/release/bundle.js",
        "sdk/packages/app/release/js/chunk.js",
    ]
    for pattern in ("release/**", "**/release/**", "**/release/**/*"):
        matcher = IgnoreMatcher([pattern], include_defaults=False)
        assert all(matcher.matches(path) for path in paths)

    root_only = IgnoreMatcher(["/release/**"], include_defaults=False)
    assert root_only.matches("release/bundle.js")
    assert not root_only.matches("web/release/bundle.js")


def test_ignore_skips_images_by_default_and_allows_override() -> None:
    matcher = IgnoreMatcher([])
    assert matcher.matches("apps/packages/app/promo/src/images/coupon.png")
    assert matcher.matches("assets/logo.SVG")
    assert matcher.matches("photo.jpg")
    assert not matcher.matches("src/app.ts")

    allow_svg = IgnoreMatcher(["!**/*.svg"])
    assert allow_svg.matches("assets/logo.png")
    assert not allow_svg.matches("assets/icon.svg")


def test_ignore_skips_nested_release_and_next_by_default() -> None:
    matcher = IgnoreMatcher([])
    assert matcher.matches("web/packages/app/release/tool/index.js")
    assert matcher.matches("web/release/.next/cache/chunk.js")
    assert matcher.matches("release/bundle.js")
    assert matcher.matches("apps/packages/component/dist/index.js")
    assert matcher.matches("node_modules/lodash/index.js")
    assert matcher.matches("build/app.min.js")
    assert not matcher.matches("web/packages/service/tool/yuanshen/page.tsx")


def test_parser_returns_symbols_and_marks_inferred_calls() -> None:
    result = parse_source(
        "src/upload.py",
        """
from services.client import send

def send():
    return True

def upload():
    return send()
""",
    )

    assert result.language == "python"
    assert {symbol.name for symbol in result.symbols} >= {"upload"}
    assert any(relation.relation_type == "imports" for relation in result.relations)
    assert all(relation.confidence < 1 for relation in result.relations if relation.is_inferred)
    upload = next(symbol for symbol in result.symbols if symbol.name == "upload")
    assert upload.end_line >= upload.start_line
    assert "send" in upload.snippet or "return" in "\n".join(
        line for line in upload.snippet.splitlines()
    )


def test_parser_extracts_typed_ts_const_and_clips_calls() -> None:
    result = parse_source(
        "src/components/Verify/index.tsx",
        """
export const Verify: FC<Props> = ({ value }) => {
  onChange(sanitizeRealname(value))
  return null
}

const sanitizeRealname = (value: string) => value.replace(/x/g, '')

const Other = () => {
  leftover()
}
""",
    )
    names = {symbol.name for symbol in result.symbols}
    assert {"Verify", "sanitizeRealname", "Other"} <= names
    verify = next(symbol for symbol in result.symbols if symbol.name == "Verify")
    assert verify.end_line < next(symbol for symbol in result.symbols if symbol.name == "Other").start_line
    calls = [
        relation.target_name
        for relation in result.relations
        if relation.source_name == "Verify" and relation.relation_type == "calls"
    ]
    assert "sanitizeRealname" in calls
    assert "leftover" not in calls


def test_parser_indexes_swift_and_chinese_todo_comments() -> None:
    result = parse_source(
        "Sources/Web/WKInput.swift",
        """
class InputBridge {
  func focusInput() {
    // TODO: autofocus 在ios上是无效的
  }
}
""",
    )
    assert result.language == "swift"
    names = {symbol.name for symbol in result.symbols}
    assert "InputBridge" in names
    assert "focusInput" in names
    assert any(symbol.kind == "comment" and "autofocus" in symbol.name for symbol in result.symbols)
