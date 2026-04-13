from __future__ import annotations

from typing import IO

import tree_sitter_cfengine as tscfengine
from tree_sitter import Language, Parser, Node
from cfbs.pretty import pretty_file, pretty_check_file

# Node types that increase indentation by 2 when entered
INDENTED_TYPES = {
    "bundle_section",
    "class_guarded_promises",
    "class_guarded_body_attributes",
    "class_guarded_promise_block_attributes",
    "promise",
    "half_promise",
    "attribute",
}

CLASS_GUARD_TYPES = {
    "class_guarded_promises",
    "class_guarded_body_attributes",
    "class_guarded_promise_block_attributes",
}

BLOCK_TYPES = {"bundle_block", "promise_block", "body_block"}

PROMISER_PARTS = {"promiser", "->", "stakeholder"}


def format_json_file(filename: str, check: bool) -> int:
    """Reformat a JSON file in place using cfbs pretty-printer.

    Returns 0 in case of successful reformat or no reformat needed.
    Returns 1 when check is True and reformat is needed.
    """
    assert filename.endswith(".json")

    if check:
        success = pretty_check_file(filename)
        # pretty_check_file() in cfbs needs correct typehint:
        assert type(success) is bool
        if not success:
            print(f"JSON file '{filename}' needs reformatting")
        return int(not success)

    reformatted = pretty_file(filename)
    if reformatted:
        print(f"JSON file '{filename}' was reformatted")
    return 0  # Successfully reformatted or no reformat needed


def text(node: Node) -> str:
    """Extract the UTF-8 text content of a tree-sitter node."""
    if not node.text:
        return ""
    return node.text.decode("utf-8")


class Formatter:
    """Accumulates formatted output line-by-line into a string buffer."""

    def __init__(self) -> None:
        self.empty: bool = True
        self.previous: Node | None = None
        self.buffer: str = ""

    def _write(self, message: str, end: str = "\n") -> None:
        """Append text to the buffer with the given line ending."""
        self.buffer += message + end

    def print_lines(self, lines: list[str], indent: int) -> None:
        """Print multiple pre-formatted lines."""
        for line in lines:
            self.print(line, indent)

    def print(self, string: str | Node, indent: int) -> None:
        """Print a string or node on a new line with the given indentation."""
        if not isinstance(string, str):
            string = text(string)
        if not self.empty:
            self._write("\n", end="")
        self._write(" " * indent + string, end="")
        self.empty = False

    def print_same_line(self, string: str | Node) -> None:
        """Append text to the current line without a preceding newline."""
        if not isinstance(string, str):
            string = text(string)
        self._write(string, end="")

    def blank_line(self) -> None:
        """Insert a blank separator line."""
        self.print("", 0)

    def update_previous(self, node: Node) -> Node | None:
        """Set the previously-visited node, returning the old value."""
        tmp = self.previous
        self.previous = node
        return tmp


# ---------------------------------------------------------------------------
# Stringify helpers — flatten nodes into single-line strings
# ---------------------------------------------------------------------------


def stringify_parameter_list(parts: list[str]) -> str:
    """Join string tokens into a formatted parameter list.

    Removes trailing commas and adds spacing after commas.
    Example: ["(", "a", ",", "b", ",", ")"] -> "(a, b)"
    """
    # Remove trailing comma before closing paren
    cleaned = []
    for i, part in enumerate(parts):
        if part == "," and i + 1 < len(parts) and parts[i + 1] == ")":
            continue
        cleaned.append(part)
    result = ""
    previous = None
    for part in cleaned:
        if previous and previous != "(" and part != "," and part != ")":
            result += " "
        elif previous == ",":
            result += " "
        result += part
        previous = part
    return result


def stringify_single_line_nodes(nodes: list[Node]) -> str:
    """Join tree-sitter nodes into a single-line string with CFEngine spacing.

    Inserts spaces after ",", around "=>", and inside "{ }".
    Strips trailing commas immediately preceding ")" or "}".
    """
    result = ""
    previous = None
    for i, node in enumerate(nodes):
        # Strip trailing comma before closing bracket/paren
        if node.type == ",":
            next_node = nodes[i + 1] if i + 1 < len(nodes) else None
            if next_node is not None and next_node.type in (")", "}"):
                continue
        string = stringify_single_line_node(node)
        if previous and previous.type == ",":
            result += " "
        if previous and node.type == "=>":
            result += " "
        if previous and previous.type == "=>":
            result += " "
        if previous and previous.type == "{":
            result += " "
        if previous and node.type == "}":
            result += " "
        result += string
        previous = node
    return result


def stringify_single_line_node(node: Node) -> str:
    """Recursively flatten a node and its children into a single-line string."""
    if not node.children:
        return text(node)
    return stringify_single_line_nodes(node.children)


# ---------------------------------------------------------------------------
# List / rval splitting — multi-line formatting for long values
# ---------------------------------------------------------------------------


def split_generic_value(node: Node, indent: int, line_length: int) -> list[str]:
    """Split a value node (call, list, or other) into multi-line strings."""
    if node.type == "call":
        return split_rval_call(node, indent, line_length)
    if node.type == "list":
        return split_rval_list(node, indent, line_length)
    return [stringify_single_line_node(node)]


def split_generic_list(middle: list[Node], indent: int, line_length: int) -> list[str]:
    """Split list elements into one-per-line strings, each pre-indented."""
    elements: list[str] = []
    for element in middle:
        if elements and element.type == ",":
            elements[-1] = elements[-1] + ","
            continue
        line = " " * indent + stringify_single_line_node(element)
        if len(line) < line_length:
            elements.append(line)
        else:
            lines = split_generic_value(element, indent, line_length)
            elements.append(" " * indent + lines[0])
            elements.extend(lines[1:])
    # Always add a trailing comma on multi-line lists, on the last
    # non-comment element (so it doesn't end up after a trailing comment).
    for i in range(len(elements) - 1, -1, -1):
        if elements[i].lstrip().startswith("#"):
            continue
        if not elements[i].endswith(","):
            elements[i] = elements[i] + ","
        break
    return elements


def maybe_split_generic_list(
    nodes: list[Node], indent: int, line_length: int
) -> list[str]:
    """Try a single-line rendering; fall back to split_generic_list if too long."""
    string = " " * indent + stringify_single_line_nodes(nodes)
    if len(string) < line_length:
        return [string]
    return split_generic_list(nodes, indent, line_length)


def split_rval_list(node: Node, indent: int, line_length: int) -> list[str]:
    """Split a list rval ({ ... }) into multi-line form."""
    assert node.type == "list"
    assert node.children[0].type == "{"
    first = text(node.children[0])
    last = " " * indent + text(node.children[-1])
    middle = node.children[1:-1]
    elements = maybe_split_generic_list(middle, indent + 2, line_length)
    return [first, *elements, last]


def split_rval_call(node: Node, indent: int, line_length: int) -> list[str]:
    """Split a function call rval (func(...)) into multi-line form."""
    assert node.type == "call"
    assert node.children[0].type == "calling_identifier"
    assert node.children[1].type == "("
    first = text(node.children[0]) + "("
    last = " " * indent + text(node.children[-1])
    middle = node.children[2:-1]
    elements = maybe_split_generic_list(middle, indent + 2, line_length)
    return [first, *elements, last]


def split_rval(node: Node, indent: int, line_length: int) -> list[str]:
    """Split an rval node into multi-line form based on its type."""
    if node.type == "list":
        return split_rval_list(node, indent, line_length)
    if node.type == "call":
        return split_rval_call(node, indent, line_length)
    return [stringify_single_line_node(node)]


def maybe_split_rval(
    node: Node, indent: int, offset: int, line_length: int
) -> list[str]:
    """Return single-line rval if it fits at offset, otherwise split it."""
    line = stringify_single_line_node(node)
    if len(line) + offset < line_length:
        return [line]
    return split_rval(node, indent, line_length)


# ---------------------------------------------------------------------------
# Attribute formatting
# ---------------------------------------------------------------------------


def attempt_split_attribute(node: Node, indent: int, line_length: int) -> list[str]:
    """Split an attribute node, wrapping the rval if it's a list or call."""
    assert len(node.children) == 3
    lval = node.children[0]
    arrow = node.children[1]
    rval = node.children[2]

    if rval.type == "list" or rval.type == "call":
        prefix = " " * indent + text(lval) + " " + text(arrow) + " "
        offset = len(prefix)
        lines = maybe_split_rval(rval, indent, offset, line_length)
        lines[0] = prefix + lines[0]
        return lines
    return [" " * indent + stringify_single_line_node(node)]


def stringify(node: Node, indent: int, line_length: int) -> list[str]:
    """Return a node as pre-indented line(s), splitting if it exceeds line_length."""
    single_line = " " * indent + stringify_single_line_node(node)
    # Reserve 1 char for trailing ; or , after attributes
    effective_length = line_length - 1 if node.type == "attribute" else line_length
    if len(single_line) < effective_length:
        return [single_line]
    if node.type == "attribute":
        return attempt_split_attribute(node, indent, line_length - 1)
    return [single_line]


# ---------------------------------------------------------------------------
# Stakeholder helpers
# ---------------------------------------------------------------------------


def _get_stakeholder_list(children: list[Node]) -> Node | None:
    """Return the list node inside a promise's stakeholder, or None."""
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if not stakeholder:
        return None
    return next((c for c in stakeholder.children if c.type == "list"), None)


def _stakeholder_has_comments(children: list[Node]) -> bool:
    """Check if the stakeholder list contains any comment nodes."""
    list_node = _get_stakeholder_list(children)
    if not list_node:
        return False
    return any(c.type == "comment" for c in list_node.children)


def _has_trailing_comma(middle: list[Node]) -> bool:
    """Check if a list's middle nodes end with a trailing comma."""
    for node in reversed(middle):
        if node.type == ",":
            return True
        if node.type != "comment":
            return False
    return False


def _promiser_text(children: list[Node]) -> str | None:
    """Return the raw promiser string from promise children, or None."""
    promiser_node = next((c for c in children if c.type == "promiser"), None)
    if not promiser_node:
        return None
    return text(promiser_node)


def _promiser_line_with_stakeholder(children: list[Node]) -> str | None:
    """Build the full promiser line including '-> { stakeholder }', or None."""
    prefix = _promiser_text(children)
    if not prefix:
        return None
    arrow = next((c for c in children if c.type == "->"), None)
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if arrow and stakeholder:
        prefix += " " + text(arrow) + " " + stringify_single_line_node(stakeholder)
    return prefix


def _stakeholder_needs_splitting(
    children: list[Node], indent: int, line_length: int
) -> bool:
    """Check if the stakeholder list must be split (comments or too long).

    This function is only used on promises without attributes, since
    promises with both attributes and stakeholder are always split."""
    if _stakeholder_has_comments(children):
        return True
    line = _promiser_line_with_stakeholder(children)
    assert line
    return indent + len(line) > line_length


def _format_stakeholder_elements(
    middle: list[Node], indent: int, line_length: int
) -> list[str]:
    """Format the elements between { and } of a stakeholder list.

    Uses trailing-comma heuristic: lists with a trailing comma are always
    split one-per-line; without, they may stay on a single line.
    """
    if not any(n.type == "comment" for n in middle):
        if _has_trailing_comma(middle):
            return split_generic_list(middle, indent, line_length)
        return maybe_split_generic_list(middle, indent, line_length)
    # Comments present — format element-by-element to preserve them
    elements: list[str] = []
    for node in middle:
        if node.type == ",":
            if elements:
                elements[-1] = elements[-1] + ","
            continue
        if node.type == "comment":
            elements.append(" " * indent + text(node))
        else:
            line = " " * indent + stringify_single_line_node(node)
            if len(line) < line_length:
                elements.append(line)
            else:
                lines = split_generic_value(node, indent, line_length)
                elements.append(" " * indent + lines[0])
                elements.extend(lines[1:])
    # Always add a trailing comma to the last non-comment element
    for i in range(len(elements) - 1, -1, -1):
        if elements[i].lstrip().startswith("#"):
            continue
        if not elements[i].endswith(","):
            elements[i] = elements[i] + ","
        break
    return elements


# ---------------------------------------------------------------------------
# Promise formatting
# ---------------------------------------------------------------------------


def _has_stakeholder(children: list[Node]) -> bool:
    """Check if promise children include a stakeholder node."""
    return any(c.type == "stakeholder" for c in children)


def can_single_line_promise(node: Node, indent: int, line_length: int) -> bool:
    """Check if a promise can be formatted entirely on one line.

    Returns False for multi-attribute promises, promises with a
    half_promise continuation, or stakeholder+attribute combinations.
    """
    assert node.type == "promise"
    children = node.children
    attrs = [c for c in children if c.type == "attribute"]
    next_sib = node.next_named_sibling
    if len(attrs) > 1:
        # We always want to multiline a promise with multiple attributes
        # even if it would fit on one line, i.e this should be split:
        # "foo" string => "bar", comment => "baz";
        return False
    if next_sib and next_sib.type == "half_promise":
        # When the parser encounters promises which are split up
        # by macros, these are stored as "half" promises.
        # In such cases, we do not want to single line them.
        return False
    if _has_stakeholder(children) and attrs:
        # If a promise has both stakeholder and attribute(s) we want to
        # multiline it, i.e. this should be split:
        # "foo" -> { "bar" } string => "baz";
        return False
    if _has_stakeholder(children) and _stakeholder_needs_splitting(
        children, indent, line_length
    ):
        # A promise with a stakeholder and no attributes might be split
        # if the stakeholders are too long or include comments. i.e split this:
        # "foo" -> { "bar", "too long", "stakeholders", "list", "in here", "now" };
        assert not attrs  # Should have already returned in if above
        return False

    # A candidate for single line - promise with either only stakeholder(s)
    # or only one attribute. All that's left is to construct the full line
    # to see how long it is. Examples:
    # "foo" -> { "this", "fits" };
    # "foo" string => "This also fits";
    line = _promiser_line_with_stakeholder(children)
    assert line
    if attrs:
        assert len(attrs) == 1
        line += " " + stringify_single_line_node(attrs[0]) + ";"
    else:
        line += ";"

    # This is kind of the "main" / obvious check - does it fit on one line:
    return indent + len(line) <= line_length


def _format_promise(
    node: Node,
    children: list[Node],
    fmt: Formatter,
    indent: int,
    line_length: int,
    macro_indent: int,
) -> bool:
    """Format a promise node. Returns True if handled, False to fall through."""
    # Single-line promise
    if can_single_line_promise(node, indent, line_length):
        prefix = _promiser_line_with_stakeholder(children)
        assert prefix is not None
        attr = next((c for c in children if c.type == "attribute"), None)
        if attr:
            line = prefix + " " + stringify_single_line_node(attr) + ";"
        else:
            line = prefix + ";"
        fmt.print(line, indent)
        return True

    # Multi-line with split stakeholder
    if _has_stakeholder(children) and _stakeholder_needs_splitting(
        children, indent, line_length
    ):
        attrs = [c for c in children if c.type == "attribute"]
        promiser = _promiser_text(children)
        assert promiser is not None
        fmt.print(promiser + " -> {", indent)

        list_node = _get_stakeholder_list(children)
        assert list_node is not None
        middle = list_node.children[1:-1]
        element_indent = indent + 4
        elements = _format_stakeholder_elements(middle, element_indent, line_length)
        fmt.print_lines(elements, indent=0)

        close_indent = indent + 2
        if attrs:
            fmt.print("}", close_indent)
            _format_remaining_children(children, fmt, indent, line_length, macro_indent)
        else:
            fmt.print("};", close_indent)
        return True

    # Multi-line with inline stakeholder
    prefix = _promiser_line_with_stakeholder(children)
    if prefix:
        fmt.print(prefix, indent)
        _format_remaining_children(children, fmt, indent, line_length, macro_indent)
        return True

    return False


def _format_remaining_children(
    children: list[Node],
    fmt: Formatter,
    indent: int,
    line_length: int,
    macro_indent: int,
) -> None:
    """Format promise children, skipping promiser/arrow/stakeholder parts."""
    for child in children:
        if child.type in PROMISER_PARTS:
            continue
        autoformat(child, fmt, line_length, macro_indent, indent)


# ---------------------------------------------------------------------------
# Block header formatting (bundle, body, promise blocks)
# ---------------------------------------------------------------------------


def _format_block_header(node: Node, fmt: Formatter) -> list[Node]:
    """Format a block header line and return the body's children for further processing."""
    header_parts: list[str] = []
    header_comments: list[str] = []
    for x in node.children[0:-1]:
        if x.type == "comment":
            header_comments.append(text(x))
        elif x.type == "parameter_list":
            parts: list[str] = []
            for p in x.children:
                if p.type == "comment":
                    header_comments.append(text(p))
                else:
                    parts.append(text(p))
            header_parts[-1] = header_parts[-1] + stringify_parameter_list(parts)
        else:
            header_parts.append(text(x))
    line = " ".join(header_parts)
    if not fmt.empty:
        prev_sib = node.prev_named_sibling
        if not (prev_sib and prev_sib.type == "comment"):
            fmt.blank_line()
    fmt.print(line, 0)
    for i, comment in enumerate(header_comments):
        if comment.strip() == "#":
            prev_is_comment = i > 0 and header_comments[i - 1].strip() != "#"
            next_is_comment = (
                i + 1 < len(header_comments) and header_comments[i + 1].strip() != "#"
            )
            if not (prev_is_comment and next_is_comment):
                continue
        fmt.print(comment, 0)
    return node.children[-1].children


# ---------------------------------------------------------------------------
# Blank line logic
# ---------------------------------------------------------------------------


def _needs_blank_line_before(child: Node, indent: int, line_length: int) -> bool:
    """Check if a blank separator line should precede this child node."""
    prev = child.prev_named_sibling
    if not prev:
        return False

    if child.type == "bundle_section":
        return prev.type == "bundle_section"

    if child.type == "promise" and prev.type in {"promise", "half_promise"}:
        promise_indent = indent + 2
        both_single = (
            prev.type == "promise"
            and can_single_line_promise(prev, promise_indent, line_length)
            and can_single_line_promise(child, promise_indent, line_length)
        )
        return not both_single

    if child.type in CLASS_GUARD_TYPES:
        return prev.type in {"promise", "half_promise", "class_guarded_promises"}

    if child.type == "comment":
        if prev.type not in {"promise", "half_promise"} | CLASS_GUARD_TYPES:
            return False
        parent = child.parent
        return bool(
            parent and parent.type in {"bundle_section", "class_guarded_promises"}
        )

    return False


# ---------------------------------------------------------------------------
# Comment formatting
# ---------------------------------------------------------------------------


def _is_empty_comment(node: Node) -> bool:
    """Check if a bare '#' comment should be dropped (not between other comments)."""
    if text(node).strip() != "#":
        return False
    prev = node.prev_named_sibling
    nxt = node.next_named_sibling
    return not (prev and prev.type == "comment" and nxt and nxt.type == "comment")


def _skip_comments(sibling: Node | None, direction: str = "next") -> Node | None:
    """Walk past adjacent comment siblings to find the nearest non-comment."""
    while sibling and sibling.type == "comment":
        sibling = (
            sibling.next_named_sibling
            if direction == "next"
            else sibling.prev_named_sibling
        )
    return sibling


def _comment_indent(node: Node, indent: int) -> int:
    """Compute indentation for a leaf comment based on its nearest non-comment neighbor."""
    nearest = _skip_comments(node.next_named_sibling, "next")
    if nearest is None:
        nearest = _skip_comments(node.prev_named_sibling, "prev")
    if nearest and nearest.type in INDENTED_TYPES:
        return indent + 2
    return indent


# ---------------------------------------------------------------------------
# Main recursive formatter
# ---------------------------------------------------------------------------


def autoformat(
    node: Node,
    fmt: Formatter,
    line_length: int,
    macro_indent: int,
    indent: int = 0,
) -> None:
    """Recursively format a tree-sitter node tree into the Formatter buffer."""
    previous = fmt.update_previous(node)

    # Macro handling
    if previous and previous.type == "macro" and text(previous).startswith("@else"):
        indent = macro_indent
    if node.type == "macro":
        fmt.print(node, 0)
        if text(node).startswith("@if"):
            macro_indent = indent
        elif text(node).startswith("@else"):
            indent = macro_indent
        return

    # Block header (bundle/body/promise blocks)
    children = node.children
    if node.type in BLOCK_TYPES:
        children = _format_block_header(node, fmt)

    # Indentation
    if node.type in INDENTED_TYPES:
        indent += 2

    # Attribute — stringify and return
    if node.type == "attribute":
        fmt.print_lines(stringify(node, indent, line_length), indent=0)
        return

    # Promise — delegate to promise formatter
    if node.type == "promise":
        if _format_promise(node, children, fmt, indent, line_length, macro_indent):
            return

    # Interior node with children — recurse
    if children:
        for child in children:
            if _needs_blank_line_before(child, indent, line_length):
                fmt.blank_line()
            autoformat(child, fmt, line_length, macro_indent, indent)
        return

    # Leaf nodes
    if node.type in {",", ";"}:
        fmt.print_same_line(node)
    elif node.type == "comment":
        if not _is_empty_comment(node):
            fmt.print(node, _comment_indent(node, indent))
    else:
        fmt.print(node, indent)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def format_policy_file(filename: str, line_length: int, check: bool) -> int:
    """Format a .cf policy file in place, writing only if content changed.

    Returns 0 in case of successful reformat or no reformat needed.
    Returns 1 when check is True and reformat is needed."""
    assert filename.endswith(".cf")

    PY_LANGUAGE = Language(tscfengine.language())
    parser = Parser(PY_LANGUAGE)

    macro_indent = 0
    fmt = Formatter()
    with open(filename, "rb") as f:
        original_data = f.read()
    tree = parser.parse(original_data)

    root_node = tree.root_node
    assert root_node.type == "source_file"
    autoformat(root_node, fmt, line_length, macro_indent)

    new_data = fmt.buffer + "\n"
    if new_data != original_data.decode("utf-8"):
        if check:
            print(f"Policy file '{filename}' needs reformatting")
            return 1

        with open(filename, "w") as f:
            f.write(new_data)
        print(f"Policy file '{filename}' was reformatted")
    return 0


def format_policy_fin_fout(
    fin: IO[str], fout: IO[str], line_length: int, check: bool
) -> int:
    """Format CFEngine policy read from fin, writing the result to fout."""
    PY_LANGUAGE = Language(tscfengine.language())
    parser = Parser(PY_LANGUAGE)

    macro_indent = 0
    fmt = Formatter()
    original_data = fin.read().encode("utf-8")
    tree = parser.parse(original_data)

    root_node = tree.root_node
    assert root_node.type == "source_file"
    autoformat(root_node, fmt, line_length, macro_indent)

    new_data = fmt.buffer + "\n"
    fout.write(new_data)
    return 0
