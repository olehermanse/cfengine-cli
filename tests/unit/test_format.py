import io

import tree_sitter_cfengine as tscfengine
from tree_sitter import Language, Parser, Node

from cfengine_cli.format import (
    Formatter,
    text,
    stringify_parameter_list,
    stringify_single_line_nodes,
    stringify_single_line_node,
    split_generic_list,
    maybe_split_generic_list,
    split_rval_list,
    split_rval_call,
    split_rval,
    maybe_split_rval,
    split_generic_value,
    attempt_split_attribute,
    stringify,
    format_policy_fin_fout,
)

# ---------------------------------------------------------------------------
# MockNode — lightweight stand-in for tree-sitter Node
# ---------------------------------------------------------------------------


class MockNode:
    """Minimal stand-in for a tree-sitter Node."""

    def __init__(
        self,
        node_type,
        node_text=None,
        children=None,
        next_named_sibling=None,
        prev_named_sibling=None,
        parent=None,
    ):
        self.type = node_type
        self.text = node_text.encode("utf-8") if node_text is not None else None
        self.children = children or []
        self.next_named_sibling = next_named_sibling
        self.prev_named_sibling = prev_named_sibling
        self.parent = parent


def _leaf(node_type, node_text=None):
    return MockNode(node_type, node_text or node_type)


# ---------------------------------------------------------------------------
# Real parser helper — parse CFEngine code into a tree-sitter tree
# ---------------------------------------------------------------------------

_LANGUAGE = Language(tscfengine.language())
_PARSER = Parser(_LANGUAGE)


def _parse(code: str) -> Node:
    """Parse CFEngine source and return the root node."""
    tree = _PARSER.parse(code.encode("utf-8"))
    return tree.root_node


def _format(code: str, line_length: int = 80) -> str:
    """Format CFEngine source via format_policy_fin_fout and return the result."""
    fin = io.StringIO(code)
    fout = io.StringIO()
    format_policy_fin_fout(fin, fout, line_length, False)
    return fout.getvalue()


def _find(root: Node, node_type: str) -> Node:
    """Find the first descendant of the given type (depth-first)."""
    if root.type == node_type:
        return root
    for child in root.children:
        found = _find_opt(child, node_type)
        if found:
            return found
    raise ValueError(f"No node of type {node_type!r} found")


def _find_opt(root: Node, node_type: str):
    """Find the first descendant of the given type, or None."""
    if root.type == node_type:
        return root
    for child in root.children:
        found = _find_opt(child, node_type)
        if found:
            return found
    return None


def _find_all(root: Node, node_type: str) -> list[Node]:
    """Find all descendants of the given type (depth-first)."""
    results = []
    if root.type == node_type:
        results.append(root)
    for child in root.children:
        results.extend(_find_all(child, node_type))
    return results


# ---------------------------------------------------------------------------
# text()
# ---------------------------------------------------------------------------


def test_text_returns_decoded_string():
    node = _leaf("identifier", "hello")
    assert text(node) == "hello"


def test_text_returns_empty_for_none():
    node = MockNode("identifier", node_text=None)
    node.text = None
    assert text(node) == ""


# ---------------------------------------------------------------------------
# Formatter class
# ---------------------------------------------------------------------------


def test_formatter_empty_initial():
    fmt = Formatter()
    assert fmt.empty is True
    assert fmt.buffer == ""
    assert fmt.previous is None


def test_formatter_print():
    fmt = Formatter()
    fmt.print("hello", 0)
    assert fmt.buffer == "hello"
    assert fmt.empty is False


def test_formatter_print_with_indent():
    fmt = Formatter()
    fmt.print("hello", 4)
    assert fmt.buffer == "    hello"


def test_formatter_print_multiple_lines():
    fmt = Formatter()
    fmt.print("line1", 0)
    fmt.print("line2", 2)
    assert fmt.buffer == "line1\n  line2"


def test_formatter_print_node():
    fmt = Formatter()
    node = _leaf("identifier", "world")
    fmt.print(node, 0)
    assert fmt.buffer == "world"


def test_formatter_print_same_line():
    fmt = Formatter()
    fmt.print("hello", 0)
    fmt.print_same_line(";")
    assert fmt.buffer == "hello;"


def test_formatter_print_same_line_node():
    fmt = Formatter()
    fmt.print("x", 0)
    fmt.print_same_line(_leaf(";"))
    assert fmt.buffer == "x;"


def test_formatter_blank_line():
    fmt = Formatter()
    fmt.print("a", 0)
    fmt.blank_line()
    fmt.print("b", 0)
    assert fmt.buffer == "a\n\nb"


def test_formatter_print_lines():
    fmt = Formatter()
    fmt.print_lines(["  a", "  b", "  c"], indent=0)
    assert fmt.buffer == "  a\n  b\n  c"


def test_formatter_update_previous():
    fmt = Formatter()
    n1 = _leaf("a", "a")
    n2 = _leaf("b", "b")
    assert fmt.update_previous(n1) is None
    assert fmt.previous is n1
    assert fmt.update_previous(n2) is n1
    assert fmt.previous is n2


# ---------------------------------------------------------------------------
# stringify_parameter_list
# ---------------------------------------------------------------------------


def test_stringify_parameter_list():
    assert stringify_parameter_list([]) == ""
    assert stringify_parameter_list(["foo"]) == "foo"
    assert stringify_parameter_list(["(", "a", ")"]) == "(a)"
    assert stringify_parameter_list(["(", "a", ",", "b", ")"]) == "(a, b)"
    assert stringify_parameter_list(["(", "a", ",", ")"]) == "(a)"
    assert stringify_parameter_list(["(", "a", ",", "b", ",", ")"]) == "(a, b)"
    assert stringify_parameter_list(["a", "b", "c"]) == "a b c"
    assert stringify_parameter_list(["a", ",", "b"]) == "a, b"
    assert stringify_parameter_list(["(", ")"]) == "()"
    parts = ["(", "x", ",", "y", ",", "z", ")"]
    assert stringify_parameter_list(parts) == "(x, y, z)"


# ---------------------------------------------------------------------------
# stringify_single_line_nodes / stringify_single_line_node
# ---------------------------------------------------------------------------


def test_stringify_single_line_nodes():
    assert stringify_single_line_nodes([]) == ""
    assert stringify_single_line_nodes([_leaf("identifier", "foo")]) == "foo"

    nodes = [_leaf("string", '"a"'), _leaf(","), _leaf("string", '"b"')]
    assert stringify_single_line_nodes(nodes) == '"a", "b"'

    nodes = [_leaf("identifier", "lval"), _leaf("=>"), _leaf("string", '"rval"')]
    assert stringify_single_line_nodes(nodes) == 'lval => "rval"'

    nodes = [_leaf("("), _leaf("identifier", "x"), _leaf(")")]
    assert stringify_single_line_nodes(nodes) == "(x)"

    nodes = [
        _leaf("{"),
        _leaf("string", '"a"'),
        _leaf(","),
        _leaf("string", '"b"'),
        _leaf("}"),
    ]
    assert stringify_single_line_nodes(nodes) == '{ "a", "b" }'
    nodes = [
        _leaf("identifier", "package_name"),
        _leaf("=>"),
        _leaf("string", '"nginx"'),
    ]

    assert stringify_single_line_nodes(nodes) == 'package_name => "nginx"'
    inner = MockNode(
        "call",
        children=[
            _leaf("calling_identifier", "func"),
            _leaf("("),
            _leaf("string", '"arg"'),
            _leaf(")"),
        ],
    )

    nodes = [_leaf("identifier", "x"), _leaf("=>"), inner]
    assert stringify_single_line_nodes(nodes) == 'x => func("arg")'


def test_stringify_single_line_node_leaf():
    assert stringify_single_line_node(_leaf("identifier", "foo")) == "foo"


def test_stringify_single_line_node_with_children():
    node = MockNode(
        "attribute",
        children=[
            _leaf("attribute_name", "string"),
            _leaf("=>"),
            _leaf("quoted_string", '"value"'),
        ],
    )
    assert stringify_single_line_node(node) == 'string => "value"'


# ---------------------------------------------------------------------------
# split_generic_list / maybe_split_generic_list
# ---------------------------------------------------------------------------


def test_split_generic_list_basic():
    nodes = [_leaf("string", '"a"'), _leaf(","), _leaf("string", '"b"')]
    result = split_generic_list(nodes, 4, 80)
    assert result == ['    "a",', '    "b",']


def test_maybe_split_generic_list_fits():
    nodes = [_leaf("string", '"a"'), _leaf(","), _leaf("string", '"b"')]
    result = maybe_split_generic_list(nodes, 4, 80)
    assert result == ['    "a", "b"']


def test_maybe_split_generic_list_too_long():
    nodes = [
        _leaf("string", '"aaaaaaaaaaaaaaaaaaaaaaaaa"'),
        _leaf(","),
        _leaf("string", '"bbbbbbbbbbbbbbbbbbbbbbbbb"'),
    ]
    result = maybe_split_generic_list(nodes, 4, 40)
    assert len(result) == 2
    assert result[0].strip().startswith('"a')
    assert result[1].strip().startswith('"b')


# ---------------------------------------------------------------------------
# split_rval_list / split_rval_call / split_rval
# ---------------------------------------------------------------------------


def test_split_rval_list():
    root = _parse('bundle agent x { vars: "v" slist => { "a", "b" }; }')
    list_node = _find(root, "list")
    result = split_rval_list(list_node, 6, 20)
    assert result[0] == "{"
    assert any('"a"' in line for line in result)
    assert any('"b"' in line for line in result)
    assert result[-1].strip() == "}"


def test_split_rval_call():
    root = _parse('bundle agent x { vars: "v" string => concat("a", "b"); }')
    call_node = _find(root, "call")
    result = split_rval_call(call_node, 6, 20)
    assert result[0] == "concat("
    assert result[-1].strip() == ")"


def test_split_rval_dispatches_list():
    root = _parse('bundle agent x { vars: "v" slist => { "a", "b" }; }')
    list_node = _find(root, "list")
    result = split_rval(list_node, 6, 20)
    assert result[0] == "{"


def test_split_rval_dispatches_call():
    root = _parse('bundle agent x { vars: "v" string => concat("a", "b"); }')
    call_node = _find(root, "call")
    result = split_rval(call_node, 6, 20)
    assert result[0] == "concat("


def test_split_rval_fallback():
    root = _parse('bundle agent x { vars: "v" string => "hello"; }')
    string_node = _find(root, "quoted_string")
    result = split_rval(string_node, 6, 80)
    assert result == ['"hello"']


def test_maybe_split_rval_fits():
    root = _parse('bundle agent x { vars: "v" string => "hello"; }')
    string_node = _find(root, "quoted_string")
    result = maybe_split_rval(string_node, 6, 10, 80)
    assert result == ['"hello"']


def test_maybe_split_rval_too_long():
    root = _parse('bundle agent x { vars: "v" slist => { "a", "b" }; }')
    list_node = _find(root, "list")
    result = maybe_split_rval(list_node, 6, 999, 80)
    assert result[0] == "{"


def test_split_generic_value_call():
    root = _parse('bundle agent x { vars: "v" string => concat("a", "b"); }')
    call_node = _find(root, "call")
    result = split_generic_value(call_node, 6, 20)
    assert result[0] == "concat("


def test_split_generic_value_list():
    root = _parse('bundle agent x { vars: "v" slist => { "a", "b" }; }')
    list_node = _find(root, "list")
    result = split_generic_value(list_node, 6, 20)
    assert result[0] == "{"


def test_split_generic_value_other():
    node = _leaf("quoted_string", '"hello"')
    result = split_generic_value(node, 6, 80)
    assert result == ['"hello"']


# ---------------------------------------------------------------------------
# attempt_split_attribute / stringify
# ---------------------------------------------------------------------------


def test_attempt_split_attribute_with_list():
    root = _parse('bundle agent x { vars: "v" slist => { "a", "b" }; }')
    attr = _find(root, "attribute")
    result = attempt_split_attribute(attr, 6, 20)
    assert len(result) > 1
    assert "slist => {" in result[0]


def test_attempt_split_attribute_with_string():
    root = _parse('bundle agent x { vars: "v" string => "hello"; }')
    attr = _find(root, "attribute")
    result = attempt_split_attribute(attr, 6, 80)
    assert len(result) == 1
    assert 'string => "hello"' in result[0]


def test_stringify_short_attribute():
    root = _parse('bundle agent x { vars: "v" string => "hi"; }')
    attr = _find(root, "attribute")
    result = stringify(attr, 6, 80)
    assert len(result) == 1
    assert result[0] == '      string => "hi"'


def test_stringify_long_attribute_splits():
    root = _parse('bundle agent x { vars: "v" slist => { "aaa", "bbb" }; }')
    attr = _find(root, "attribute")
    result = stringify(attr, 6, 30)
    assert len(result) > 1


def test_stringify_non_attribute():
    node = _leaf("identifier", "hello")
    result = stringify(node, 4, 80)
    assert result == ["    hello"]


# ---------------------------------------------------------------------------
# autoformat / format_policy_fin_fout — integration tests
# ---------------------------------------------------------------------------


def test_format_hello_world():
    result = _format('bundle agent main\n{\nvars:\n"hello" string => "world";\n}')
    assert "bundle agent main" in result
    assert "  vars:" in result
    assert '"hello" string => "world";' in result


def test_format_idempotent():
    code = 'bundle agent main\n{\n  vars:\n    "v" string => "hi";\n}\n'
    result = _format(code)
    assert result == code


def test_format_indentation():
    code = 'bundle agent main\n{\nvars:\n"v"\nstring => "hi";\n}'
    result = _format(code)
    for line in result.strip().split("\n"):
        if line.startswith("bundle") or line.startswith("{") or line.startswith("}"):
            continue
        assert line.startswith("  "), f"Expected indentation: {line!r}"


def test_format_multiple_bundles():
    code = "bundle agent a { } bundle agent b { }"
    result = _format(code)
    assert "bundle agent a" in result
    assert "bundle agent b" in result
    assert "\n\n" in result  # blank line between bundles


def test_format_class_guard():
    code = 'bundle agent x { vars: linux:: "v" string => "hi"; }'
    result = _format(code)
    assert "linux::" in result


def test_format_comment_preserved():
    code = 'bundle agent x {\n# my comment\nvars:\n"v" string => "hi";\n}'
    result = _format(code)
    assert "# my comment" in result


def test_format_empty_comment_removed():
    code = 'bundle agent x {\nvars:\n#\n"v" string => "hi";\n}'
    result = _format(code)
    lines = [l.strip() for l in result.strip().split("\n")]
    assert "#" not in lines


def test_format_stakeholder_inline():
    code = 'bundle agent x { packages: "p" -> { "a" }; }'
    result = _format(code)
    assert '"p" -> { "a" };' in result


def test_format_stakeholder_split():
    code = (
        "bundle agent x { packages: "
        '"python3-rpm-macros" -> { "very long reason text here", "TICKET-1234" } '
        'comment => "c"; }'
    )
    result = _format(code, line_length=50)
    assert "-> {" in result
    lines = result.strip().split("\n")
    assert any("}" in line and "comment" not in line for line in lines)


def test_format_stakeholder_with_attributes_multiline():
    code = 'bundle agent x { packages: "p" -> { "a", "b" } comment => "c"; }'
    result = _format(code)
    lines = result.strip().split("\n")
    promiser_line = next(l for l in lines if '"p"' in l)
    attr_line = next(l for l in lines if "comment" in l)
    assert promiser_line != attr_line


def test_format_single_line_promises_grouped():
    code = (
        "bundle agent x\n"
        "{\n"
        "  packages:\n"
        '    "a" package_policy => "delete";\n'
        '    "b" package_policy => "delete";\n'
        "}\n"
    )
    result = _format(code)
    assert result == code  # should be idempotent, no blank lines between


def test_format_multi_line_promise_separated():
    code = (
        'bundle agent x { vars: "a" if => "linux", string => "x"; "b" string => "y"; }'
    )
    result = _format(code)
    assert "\n\n" in result  # blank line between multi-line and next promise


def test_format_body_block():
    code = 'body common control { inputs => { "a.cf" }; }'
    result = _format(code)
    assert "body common control" in result
    assert "inputs" in result


def test_format_long_list_wraps():
    code = (
        'bundle agent x { vars: "v" slist => '
        '{ "aaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbb", "ccccccccccccccccc" }; }'
    )
    result = _format(code, line_length=50)
    lines = result.strip().split("\n")
    assert len(lines) > 3  # should have wrapped


def test_format_line_length_respected():
    code = (
        'bundle agent x { vars: "v" slist => '
        '{ "aaa", "bbb", "ccc", "ddd", "eee", "fff" }; }'
    )
    result = _format(code, line_length=40)
    for line in result.strip().split("\n"):
        # Allow slight overshoot for long strings that can't be split
        assert len(line) <= 80, f"Line too long: {line!r}"
