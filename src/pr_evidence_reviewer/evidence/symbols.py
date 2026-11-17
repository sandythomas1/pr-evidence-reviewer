"""Functions, classes and other definitions found with tree-sitter, for Python, JavaScript/TypeScript,
Go, Java and C#. This is the only module that imports tree-sitter.

Two operations: `enclosing(path, line)` gives the innermost definition around a line (a changed
hunk's function), and `definitions(name)` gives where a name is defined. Both read through the
checkout reader and are capped the same way (threats T6, C9): a file over 512 KiB, with a line over
5 000 characters, or whose syntax tree has more than 200 000 nodes is not parsed. Those files, files
that don't parse cleanly and other languages return no symbols and a skip reason, so the caller falls
back to text search and reports the gap. Nothing in the repository is run.

Definitions come from a node-type table per language, not query files. Line numbers count `\\n` line
breaks, the same as `split_lines`, so they match the lines shown to the model.

A `SymbolIndex` caches parsed files and is not thread-safe: use one per packet build.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache

import tree_sitter
import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

from pr_evidence_reviewer.checkout.archive import ManifestFile
from pr_evidence_reviewer.checkout.paths import normalize_repo_path
from pr_evidence_reviewer.checkout.reader import CheckoutReader, UnsafePathError
from pr_evidence_reviewer.checkout.text import split_lines
from pr_evidence_reviewer.evidence.diff import is_low_value

KIB = 1024

_GRAMMARS: dict[str, Callable[[], object]] = {
    "python": tree_sitter_python.language,
    "javascript": tree_sitter_javascript.language,
    "typescript": tree_sitter_typescript.language_typescript,
    "tsx": tree_sitter_typescript.language_tsx,
    "go": tree_sitter_go.language,
    "java": tree_sitter_java.language,
    "csharp": tree_sitter_c_sharp.language,
}
_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
}
SUPPORTED_EXTENSIONS = tuple(_LANGUAGE_BY_EXTENSION)

_JS_TS_DEFINITIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "variable_declarator": "function",  # only when bound to a function, see _definition
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
_JVM_DEFINITIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
}
# Node type -> symbol kind, per language.
_DEFINITIONS: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": _JS_TS_DEFINITIONS,
    "typescript": _JS_TS_DEFINITIONS,
    "tsx": _JS_TS_DEFINITIONS,
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
    "java": _JVM_DEFINITIONS,
    "csharp": {**_JVM_DEFINITIONS, "struct_declaration": "struct"},
}
_FUNCTION_VALUES = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function"}
)
_CONTAINERS = frozenset({"class", "interface", "struct", "record", "enum", "type"})


@dataclass(frozen=True)
class SymbolLimits:
    max_file_bytes: int = 512 * KIB
    max_line_chars: int = 5_000
    max_nodes: int = 200_000
    max_definition_files: int = 30


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SymbolSkip:
    """A file whose symbols weren't read. Reasons: unsupported_language, not_readable, too_large,
    long_line, too_many_nodes, parse_error."""

    path: str
    reason: str


@dataclass(frozen=True)
class SymbolLookup:
    symbols: tuple[Symbol, ...] = ()
    skipped: tuple[SymbolSkip, ...] = ()
    # True when more files hold the name than the file cap allowed to be parsed.
    truncated: bool = False


@dataclass
class _Parsed:
    symbols: tuple[Symbol, ...] = ()
    skip: SymbolSkip | None = None


@dataclass
class _NameSearch:
    files: list[str] = field(default_factory=list)
    truncated: bool = False


def supports(path: str) -> bool:
    return _language_for(path) is not None


def parse_ok(extension: str, source: str) -> bool:
    """Whether `source` parses without errors as the language for `extension` (grammar smoke test)."""
    language = _LANGUAGE_BY_EXTENSION[extension]
    tree = tree_sitter.Parser(_language(language)).parse(source.encode("utf-8"))
    return not tree.root_node.has_error


class SymbolIndex:
    def __init__(
        self,
        reader: CheckoutReader,
        *,
        changed_paths: Iterable[str] = (),
        limits: SymbolLimits = SymbolLimits(),
    ) -> None:
        self._reader = reader
        self._changed = {safe for path in changed_paths if (safe := normalize_repo_path(path))}
        self._limits = limits
        self._parsed: dict[str, _Parsed] = {}
        self._parsers: dict[str, tree_sitter.Parser] = {}

    def enclosing(self, path: str, line: int) -> SymbolLookup:
        """The innermost definition containing `line`, with its qualified name (`Class.method`)."""
        parsed = self._parse_path(path)
        if parsed.skip is not None:
            return SymbolLookup(skipped=(parsed.skip,))
        containing = [s for s in parsed.symbols if s.start_line <= line <= s.end_line]
        return SymbolLookup(symbols=tuple(containing[-1:]))

    def definitions(self, name: str) -> SymbolLookup:
        return self.find_definitions([name])[name]

    def find_definitions(self, names: Sequence[str]) -> dict[str, SymbolLookup]:
        """Definitions of each name, from the first files (changed first, then by path) that contain
        it, up to the file cap per name. One pass over the files for all names."""
        searches = {name: _NameSearch() for name in dict.fromkeys(names) if name}
        cap = self._limits.max_definition_files
        for path, text in self._reader.iter_text(self._definition_files()):
            for name, search in searches.items():
                if name not in text:
                    continue
                if len(search.files) >= cap:
                    search.truncated = True
                else:
                    search.files.append(path)
        return {name: self._lookup(name, search) for name, search in searches.items()}

    def _lookup(self, name: str, search: _NameSearch) -> SymbolLookup:
        symbols: list[Symbol] = []
        skipped: list[SymbolSkip] = []
        for path in search.files:
            parsed = self._parse_path(path)
            if parsed.skip is not None:
                skipped.append(parsed.skip)
            symbols.extend(symbol for symbol in parsed.symbols if symbol.name == name)
        return SymbolLookup(tuple(symbols), tuple(skipped), search.truncated)

    def _definition_files(self) -> list[str]:
        files = [
            entry.path
            for entry in self._reader.files()
            if entry.kind == "text"
            and supports(entry.path)
            and (entry.path in self._changed or not is_low_value(entry.path))
        ]
        return sorted(files, key=lambda path: (path not in self._changed, path))

    def _parse_path(self, path: str) -> _Parsed:
        if path not in self._parsed:
            self._parsed[path] = self._parse_uncached(path)
        return self._parsed[path]

    def _parse_uncached(self, path: str) -> _Parsed:
        language = _language_for(path)
        if language is None:
            return _skip(path, "unsupported_language")
        entry = self._text_entry(path)
        if entry is None:
            return _skip(path, "not_readable")
        if entry.size > self._limits.max_file_bytes:
            return _skip(path, "too_large")
        text = self._reader.text(path)
        if text is None:
            return _skip(path, "not_readable")
        if any(len(line) > self._limits.max_line_chars for line in split_lines(text)):
            return _skip(path, "long_line")
        tree = self._parser(language).parse(text.encode("utf-8"))
        if tree.root_node.has_error:
            return _skip(path, "parse_error")
        symbols = _walk(tree, path, _DEFINITIONS[language], self._limits.max_nodes)
        if symbols is None:
            return _skip(path, "too_many_nodes")
        return _Parsed(symbols=symbols)

    def _text_entry(self, path: str) -> ManifestFile | None:
        try:
            entry = self._reader.file(path)
        except UnsafePathError:
            return None
        return entry if entry is not None and entry.kind == "text" else None

    def _parser(self, language: str) -> tree_sitter.Parser:
        if language not in self._parsers:
            self._parsers[language] = tree_sitter.Parser(_language(language))
        return self._parsers[language]


def _walk(
    tree: tree_sitter.Tree, path: str, table: dict[str, str], max_nodes: int
) -> tuple[Symbol, ...] | None:
    """Every definition in preorder, or None if the tree has more than `max_nodes` nodes.

    An iterative cursor walk: no recursion, so a deeply nested file can't exhaust the stack."""
    cursor = tree.walk()
    open_symbols: list[tuple[int, Symbol]] = []  # (cursor depth, symbol) of enclosing definitions
    found: list[Symbol] = []
    visited = 0
    while True:
        visited += 1
        if visited > max_nodes:
            return None
        depth = cursor.depth
        while open_symbols and open_symbols[-1][0] >= depth:
            open_symbols.pop()
        node = cursor.node
        if node is not None:
            parent = open_symbols[-1][1] if open_symbols else None
            symbol = _definition(node, table, path, parent)
            if symbol is not None:
                found.append(symbol)
                open_symbols.append((depth, symbol))
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return tuple(found)


def _definition(
    node: tree_sitter.Node, table: dict[str, str], path: str, parent: Symbol | None
) -> Symbol | None:
    kind = table.get(node.type)
    if kind is None:
        return None
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUES:
            return None
    name = _name(node)
    if name is None:
        return None
    if kind == "function" and parent is not None and parent.kind in _CONTAINERS:
        kind = "method"
    owner = _go_receiver(node) if node.type == "method_declaration" and parent is None else None
    prefix = parent.qualified_name if parent is not None else owner
    span = (
        node.parent
        if node.parent is not None and node.parent.type == "decorated_definition"
        else node
    )
    return Symbol(
        name=name,
        qualified_name=f"{prefix}.{name}" if prefix else name,
        kind=kind,
        path=path,
        start_line=_start_row(span) + 1,
        end_line=_end_line(span),
    )


def _name(node: tree_sitter.Node) -> str | None:
    name = node.child_by_field_name("name")
    if name is None or name.text is None or "identifier" not in name.type:
        return None
    return name.text.decode("utf-8")


def _go_receiver(node: tree_sitter.Node) -> str | None:
    """The receiver's type name for a Go method (`func (c *Client) Send` -> `Client`)."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    pending = [receiver]
    while pending:
        current = pending.pop(0)
        if current.type == "type_identifier" and current.text is not None:
            return current.text.decode("utf-8")
        pending.extend(current.children)
    return None


def _start_row(node: tree_sitter.Node) -> int:
    """0-based first row. Unpacks the point: `Point.row` corrupts memory in tree-sitter 0.26.0."""
    row, _column = node.start_point
    return row


def _end_line(node: tree_sitter.Node) -> int:
    """1-based last line. A node that ends right after a line break ends on the line before."""
    row, column = node.end_point
    if column == 0 and row > _start_row(node):
        return row
    return row + 1


def _skip(path: str, reason: str) -> _Parsed:
    return _Parsed(skip=SymbolSkip(path, reason))


def _language_for(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return _LANGUAGE_BY_EXTENSION.get(name[dot:]) if dot > 0 else None


@cache
def _language(name: str) -> tree_sitter.Language:
    return tree_sitter.Language(_GRAMMARS[name]())
