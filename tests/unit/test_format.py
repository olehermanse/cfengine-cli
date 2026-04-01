from cfengine_cli.format import stringify_children_from_strings, stringify_children


class MockNode:
    """Minimal stand-in for a tree-sitter Node used by stringify_children."""

    def __init__(self, node_type, node_text=None, children=None):
        self.type = node_type
        self.text = node_text.encode("utf-8") if node_text is not None else None
        self.children = children or []

def _leaf(node_type, node_text=None):
    return MockNode(node_type, node_text or node_type)

def test_stringify_children_from_strings():
    assert stringify_children_from_strings([]) == ""
    assert stringify_children_from_strings(["foo"]) == "foo"
    assert stringify_children_from_strings(["(", "a", ")"]) == "(a)"
    assert stringify_children_from_strings(["(", "a", ",", "b", ")"]) == "(a, b)"
    assert stringify_children_from_strings(["(", "a", ",", ")"]) == "(a)"
    assert (
        stringify_children_from_strings(["(", "a", ",", "b", ",", ")"])
        == "(a, b)"
    )
    assert stringify_children_from_strings(["a", "b", "c"]) == "a b c"
    assert stringify_children_from_strings(["a", ",", "b"]) == "a, b"
    assert stringify_children_from_strings(["(", ")"]) == "()"
    parts = ["(", "x", ",", "y", ",", "z", ")"]
    assert stringify_children_from_strings(parts) == "(x, y, z)"

def test_stringify_children():
    assert stringify_children([]) == ""
    assert stringify_children([_leaf("identifier", "foo")]) == "foo"

    nodes = [_leaf("string", '"a"'), _leaf(","), _leaf("string", '"b"')]
    assert stringify_children(nodes) == '"a", "b"'

    nodes = [_leaf("identifier", "lval"), _leaf("=>"), _leaf("string", '"rval"')]
    assert stringify_children(nodes) == 'lval => "rval"'

    nodes = [_leaf("("), _leaf("identifier", "x"), _leaf(")")]
    assert stringify_children(nodes) == "(x)"

    nodes = [
        _leaf("{"),
        _leaf("string", '"a"'),
        _leaf(","),
        _leaf("string", '"b"'),
        _leaf("}"),
    ]
    assert stringify_children(nodes) == '{"a", "b"}'
    nodes = [
        _leaf("identifier", "package_name"),
        _leaf("=>"),
        _leaf("string", '"nginx"'),
    ]

    assert stringify_children(nodes) == 'package_name => "nginx"'
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
    assert stringify_children(nodes) == 'x => func("arg")'
