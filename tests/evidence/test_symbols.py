"""tree-sitter symbols: enclosing definition for a line and definitions by name (tasks 21 and 22, AC6)."""

import ast
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.evidence import symbols as symbols_module
from pr_evidence_reviewer.evidence.symbols import (
    SUPPORTED_EXTENSIONS,
    SymbolIndex,
    SymbolLimits,
    supports,
)
from tests.evidence.helpers import reader_for

PYTHON = b"""import os


class Uploader:
    @retry(3)
    def upload(self, data):
        send(data)
        return True

    def close(self):
        pass


def helper():
    return 1
"""

TYPESCRIPT = b"""export abstract class Cart {
  add(item: Item): void {
    this.items.push(item);
  }
}
export interface Item {
  id: string;
}
export type Price = number;
export enum Currency {
  USD,
}
export const total = (cart: Cart): Price => {
  return 0;
};
function* ids() {
  yield 1;
}
"""

TSX = b"""export function Button(props: Props) {
  return <button>{props.label}</button>;
}
class Panel extends React.Component {
  render() {
    return <div />;
  }
}
"""

JAVASCRIPT = b"""class Client {
  send(data) {
    return fetch(data);
  }
}
const retry = function (fn) {
  return fn();
};
function upload(data) {
  return new Client().send(data);
}
"""

GO = b"""package upload

type Client struct {
\tretries int
}

func (c *Client) Send(data []byte) error {
\treturn nil
}

func New() *Client {
\treturn &Client{}
}
"""

JAVA = b"""package com.acme;

public class Uploader {
    private final int retries;

    public Uploader(int retries) {
        this.retries = retries;
    }

    public boolean upload(byte[] data) {
        return true;
    }

    interface Listener {
        void done();
    }

    enum State { IDLE, BUSY }

    record Result(int code) {}
}
"""

CSHARP = b"""namespace Shop
{
    public struct Point { public int X; }

    public class Uploader
    {
        public Uploader() { }

        public bool Upload(byte[] data)
        {
            return true;
        }
    }

    public record Receipt(int Code);

    public interface ISender { void Send(); }

    public enum State { Idle }
}
"""

FILES = {
    "src/upload.py": PYTHON,
    "web/cart.ts": TYPESCRIPT,
    "web/Button.tsx": TSX,
    "web/client.js": JAVASCRIPT,
    "pkg/upload/client.go": GO,
    "src/main/java/com/acme/Uploader.java": JAVA,
    "src/Shop/Uploader.cs": CSHARP,
    "lib/upload.rb": b"class Uploader\n  def upload(data)\n    true\n  end\nend\n",
    "windows.py": b"class Win:\r\n    def run(self):\r\n        return 1\r\n",
}


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> SymbolIndex:
    return SymbolIndex(reader_for(tmp_path_factory.mktemp("cache"), FILES))


def _enclosing(index: SymbolIndex, path: str, line: int) -> tuple[str, str, int, int] | None:
    lookup = index.enclosing(path, line)
    assert lookup.skipped == ()
    if not lookup.symbols:
        return None
    [symbol] = lookup.symbols
    assert symbol.path == path
    return symbol.qualified_name, symbol.kind, symbol.start_line, symbol.end_line


@pytest.mark.parametrize(
    ("path", "line", "expected"),
    [
        ("src/upload.py", 7, ("Uploader.upload", "method", 5, 8)),
        ("src/upload.py", 5, ("Uploader.upload", "method", 5, 8)),
        ("src/upload.py", 9, ("Uploader", "class", 4, 11)),
        ("src/upload.py", 15, ("helper", "function", 14, 15)),
        ("web/cart.ts", 3, ("Cart.add", "method", 2, 4)),
        ("web/cart.ts", 7, ("Item", "interface", 6, 8)),
        ("web/cart.ts", 9, ("Price", "type", 9, 9)),
        ("web/cart.ts", 11, ("Currency", "enum", 10, 12)),
        ("web/cart.ts", 14, ("total", "function", 13, 15)),
        ("web/cart.ts", 17, ("ids", "function", 16, 18)),
        ("web/Button.tsx", 2, ("Button", "function", 1, 3)),
        ("web/Button.tsx", 6, ("Panel.render", "method", 5, 7)),
        ("web/client.js", 3, ("Client.send", "method", 2, 4)),
        ("web/client.js", 7, ("retry", "function", 6, 8)),
        ("web/client.js", 10, ("upload", "function", 9, 11)),
        ("pkg/upload/client.go", 4, ("Client", "type", 3, 5)),
        ("pkg/upload/client.go", 8, ("Client.Send", "method", 7, 9)),
        ("pkg/upload/client.go", 12, ("New", "function", 11, 13)),
        ("src/main/java/com/acme/Uploader.java", 7, ("Uploader.Uploader", "constructor", 6, 8)),
        ("src/main/java/com/acme/Uploader.java", 11, ("Uploader.upload", "method", 10, 12)),
        ("src/main/java/com/acme/Uploader.java", 15, ("Uploader.Listener.done", "method", 15, 15)),
        ("src/main/java/com/acme/Uploader.java", 18, ("Uploader.State", "enum", 18, 18)),
        ("src/main/java/com/acme/Uploader.java", 20, ("Uploader.Result", "record", 20, 20)),
        ("src/main/java/com/acme/Uploader.java", 4, ("Uploader", "class", 3, 21)),
        ("src/Shop/Uploader.cs", 11, ("Uploader.Upload", "method", 9, 12)),
        ("src/Shop/Uploader.cs", 7, ("Uploader.Uploader", "constructor", 7, 7)),
        ("src/Shop/Uploader.cs", 3, ("Point", "struct", 3, 3)),
        ("src/Shop/Uploader.cs", 15, ("Receipt", "record", 15, 15)),
        ("src/Shop/Uploader.cs", 17, ("ISender.Send", "method", 17, 17)),
        ("src/Shop/Uploader.cs", 19, ("State", "enum", 19, 19)),
        ("windows.py", 3, ("Win.run", "method", 2, 3)),
    ],
)
def test_a_line_maps_to_its_innermost_enclosing_definition(
    index: SymbolIndex, path: str, line: int, expected: tuple[str, str, int, int]
) -> None:
    assert _enclosing(index, path, line) == expected


@pytest.mark.parametrize(("path", "line"), [("src/upload.py", 1), ("src/upload.py", 999)])
def test_a_line_outside_every_definition_has_no_enclosing_symbol(
    index: SymbolIndex, path: str, line: int
) -> None:
    assert _enclosing(index, path, line) is None


def test_the_symbol_name_is_the_last_part_of_the_qualified_name(index: SymbolIndex) -> None:
    [symbol] = index.enclosing("pkg/upload/client.go", 8).symbols

    assert symbol.name == "Send"
    assert symbol.qualified_name == "Client.Send"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("upload", [("src/main/java/com/acme/Uploader.java", 10), ("src/upload.py", 5),
                    ("web/client.js", 9)]),
        ("Send", [("pkg/upload/client.go", 7), ("src/Shop/Uploader.cs", 17)]),
        ("Price", [("web/cart.ts", 9)]),
        ("Uploader", [("src/Shop/Uploader.cs", 5), ("src/Shop/Uploader.cs", 7),
                      ("src/main/java/com/acme/Uploader.java", 3),
                      ("src/main/java/com/acme/Uploader.java", 6), ("src/upload.py", 4)]),
        ("nothing_has_this_name", []),
    ],
)  # fmt: skip
def test_a_name_maps_to_its_definitions_in_path_order(
    index: SymbolIndex, name: str, expected: list[tuple[str, int]]
) -> None:
    lookup = index.definitions(name)

    assert [(symbol.path, symbol.start_line) for symbol in lookup.symbols] == expected
    assert all(symbol.name == name for symbol in lookup.symbols)
    assert lookup.skipped == ()
    assert not lookup.truncated


def test_several_names_are_looked_up_in_one_pass(index: SymbolIndex) -> None:
    found = index.find_definitions(["Price", "New", "Price"])

    assert list(found) == ["Price", "New"]
    assert [symbol.qualified_name for symbol in found["New"].symbols] == ["New"]


def test_an_unsupported_language_returns_nothing_without_error(index: SymbolIndex) -> None:
    lookup = index.enclosing("lib/upload.rb", 2)

    assert lookup.symbols == ()
    assert [(skip.path, skip.reason) for skip in lookup.skipped] == [
        ("lib/upload.rb", "unsupported_language")
    ]
    assert not supports("lib/upload.rb")


@pytest.mark.parametrize("path", ["missing.py", "../etc/passwd.py", "C:/x.py"])
def test_an_unknown_or_unsafe_path_returns_nothing_with_a_reason(
    index: SymbolIndex, path: str
) -> None:
    lookup = index.enclosing(path, 1)

    assert lookup.symbols == ()
    assert [skip.reason for skip in lookup.skipped] == ["not_readable"]


def test_a_file_with_a_syntax_error_returns_nothing_with_a_reason(tmp_path: Path) -> None:
    index = SymbolIndex(reader_for(tmp_path, {"broken.py": b"def broken(:\n    pass\n"}))

    lookup = index.enclosing("broken.py", 2)

    assert lookup.symbols == ()
    assert [(skip.path, skip.reason) for skip in lookup.skipped] == [("broken.py", "parse_error")]


def test_definitions_parse_at_most_the_file_cap_and_say_so(tmp_path: Path) -> None:
    files = {f"m{n:02}.py": b"def target():\n    pass\n" for n in range(32)}
    index = SymbolIndex(reader_for(tmp_path, files), limits=SymbolLimits(max_definition_files=30))

    lookup = index.definitions("target")

    assert len(lookup.symbols) == 30
    assert lookup.symbols[-1].path == "m29.py"
    assert lookup.truncated


def test_definitions_look_in_changed_files_first(tmp_path: Path) -> None:
    files = {f"m{n:02}.py": b"def target():\n    pass\n" for n in range(5)}
    reader: CheckoutReader = reader_for(tmp_path, files)
    index = SymbolIndex(
        reader, changed_paths=["m04.py"], limits=SymbolLimits(max_definition_files=2)
    )

    lookup = index.definitions("target")

    assert [symbol.path for symbol in lookup.symbols] == ["m04.py", "m00.py"]


def test_definitions_skip_generated_files_unless_changed(tmp_path: Path) -> None:
    files = {
        "api/service_pb2.py": b"def target():\n    pass\n",
        "dist/x.js": b"function target() {}\n",
    }

    unchanged = SymbolIndex(reader_for(tmp_path / "a", files)).definitions("target")
    changed = SymbolIndex(
        reader_for(tmp_path / "b", files), changed_paths=["dist/x.js"]
    ).definitions("target")

    assert unchanged.symbols == ()
    assert [symbol.path for symbol in changed.symbols] == ["dist/x.js"]


def test_every_grammar_loads_and_parses() -> None:
    """Smoke test for the native grammar wheels; runs on every CI operating system."""
    from pr_evidence_reviewer.evidence.symbols import parse_ok

    samples = {
        ".py": "def f():\n    pass\n",
        ".pyi": "def f() -> int: ...\n",
        ".js": "function f() {}\n",
        ".jsx": "const A = () => <div />;\n",
        ".mjs": "export function f() {}\n",
        ".cjs": "module.exports = function f() {};\n",
        ".ts": "function f(): number { return 1; }\n",
        ".mts": "export const f = (): number => 1;\n",
        ".cts": "export function f(): void {}\n",
        ".tsx": "const A = (): JSX.Element => <div />;\n",
        ".go": "package p\nfunc F() {}\n",
        ".java": "class A { void f() {} }\n",
        ".cs": "class A { void F() {} }\n",
    }

    assert set(samples) == set(SUPPORTED_EXTENSIONS)
    for extension, source in samples.items():
        assert parse_ok(extension, source), extension


def test_symbol_code_never_reads_point_fields_by_name() -> None:
    """Regression: `Point.row` corrupts memory in tree-sitter 0.26.0 (a crash at teardown), so points
    are unpacked instead."""
    source = Path(symbols_module.__file__).read_text(encoding="utf-8")
    attributes = {
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
    }

    assert attributes.isdisjoint({"row", "column"})
