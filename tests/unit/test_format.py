from cfengine_cli.format import stringify_parameter_list, stringify_single_line_nodes


class MockNode:
    """Minimal stand-in for a tree-sitter Node used by stringify_single_line_nodes."""

    def __init__(self, node_type, node_text=None, children=None):
        self.type = node_type
        self.text = node_text.encode("utf-8") if node_text is not None else None
        self.children = children or []


def _leaf(node_type, node_text=None):
    return MockNode(node_type, node_text or node_type)


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
    assert stringify_single_line_nodes(nodes) == '{"a", "b"}'
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
