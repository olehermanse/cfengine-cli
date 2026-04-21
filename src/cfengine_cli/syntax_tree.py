"""Print tree-sitter syntax tree for a .cf file."""

import tree_sitter_cfengine as tscfengine
from tree_sitter import Language, Parser

_LANGUAGE = Language(tscfengine.language())
_PARSER = Parser(_LANGUAGE)


def format_sexp(sexp: str) -> str:
    """Format an S-expression with indentation."""
    out = []
    indent = 0
    i = 0
    while i < len(sexp):
        c = sexp[i]
        if c == "(":
            if out and out[-1] != "\n":
                out.append("\n")
            out.append("  " * indent)
            out.append("(")
            indent += 1
            i += 1
        elif c == ")":
            indent -= 1
            out.append(")")
            i += 1
        elif c == " " and i + 1 < len(sexp) and sexp[i + 1] == "(":
            i += 1  # skip space before '(', the '(' handler adds newline
        else:
            out.append(c)
            i += 1
    out.append("\n")
    return "".join(out)


def syntax_tree(path: str) -> int:
    with open(path, "rb") as f:
        data = f.read()
    tree = _PARSER.parse(data)
    print(format_sexp(str(tree.root_node)), end="")
    return 0
