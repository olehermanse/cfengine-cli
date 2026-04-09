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


def format_json_file(filename, check):
    assert filename.endswith(".json")

    if check:
        r = not pretty_check_file(filename)
        if r:
            print(f"JSON file '{filename}' needs reformatting")
        return r

    r = pretty_file(filename)
    if r:
        print(f"JSON file '{filename}' was reformatted")
    return r


def text(node: Node):
    if not node.text:
        return ""
    return node.text.decode("utf-8")


class Formatter:
    def __init__(self):
        self.empty = True
        self.previous = None
        self.buffer = ""

    def _write(self, message, end="\n"):
        self.buffer += message + end

    def print_lines(self, lines, indent):
        for line in lines:
            self.print(line, indent)

    def print(self, string, indent):
        if type(string) is not str:
            string = text(string)
        if not self.empty:
            self._write("\n", end="")
        self._write(" " * indent + string, end="")
        self.empty = False

    def print_same_line(self, string):
        if type(string) is not str:
            string = text(string)
        self._write(string, end="")

    def blank_line(self):
        self.print("", 0)

    def update_previous(self, node):
        tmp = self.previous
        self.previous = node
        return tmp


# ---------------------------------------------------------------------------
# Stringify helpers — flatten nodes into single-line strings
# ---------------------------------------------------------------------------


def stringify_parameter_list(parts):
    """Join pre-extracted string tokens into a formatted parameter list.

    Used when formatting bundle/body headers. Comments are
    stripped from the parameter_list node before this function is called,
    so `parts` contains only the structural tokens: "(", identifiers, ","
    separators, and ")".  The function removes any trailing comma before
    ")", then joins the tokens with appropriate spacing (space after each
    comma, no space after "(" or before ")").

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


def stringify_single_line_nodes(nodes):
    """Join a list of tree-sitter nodes into a single-line string.

    Spacing rules:
      - A space is inserted after each "," separator.
      - A space is inserted before and after "=>" (fat arrow).
      - A space is inserted after "{" and before "}".
      - No extra space otherwise (e.g. no space after "(" or before ")").
    """
    result = ""
    previous = None
    for node in nodes:
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


def stringify_single_line_node(node):
    if not node.children:
        return text(node)
    return stringify_single_line_nodes(node.children)


# ---------------------------------------------------------------------------
# List / rval splitting — multi-line formatting for long values
# ---------------------------------------------------------------------------


def split_generic_value(node, indent, line_length):
    if node.type == "call":
        return split_rval_call(node, indent, line_length)
    if node.type == "list":
        return split_rval_list(node, indent, line_length)
    return [stringify_single_line_node(node)]


def split_generic_list(middle, indent, line_length):
    elements = []
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
    return elements


def maybe_split_generic_list(nodes, indent, line_length):
    string = " " * indent + stringify_single_line_nodes(nodes)
    if len(string) < line_length:
        return [string]
    return split_generic_list(nodes, indent, line_length)


def split_rval_list(node, indent, line_length):
    assert node.type == "list"
    assert node.children[0].type == "{"
    first = text(node.children[0])
    last = " " * indent + text(node.children[-1])
    middle = node.children[1:-1]
    elements = maybe_split_generic_list(middle, indent + 2, line_length)
    return [first, *elements, last]


def split_rval_call(node, indent, line_length):
    assert node.type == "call"
    assert node.children[0].type == "calling_identifier"
    assert node.children[1].type == "("
    first = text(node.children[0]) + "("
    last = " " * indent + text(node.children[-1])
    middle = node.children[2:-1]
    elements = maybe_split_generic_list(middle, indent + 2, line_length)
    return [first, *elements, last]


def split_rval(node, indent, line_length):
    if node.type == "list":
        return split_rval_list(node, indent, line_length)
    if node.type == "call":
        return split_rval_call(node, indent, line_length)
    return [stringify_single_line_node(node)]


def maybe_split_rval(node, indent, offset, line_length):
    line = stringify_single_line_node(node)
    if len(line) + offset < line_length:
        return [line]
    return split_rval(node, indent, line_length)


# ---------------------------------------------------------------------------
# Attribute formatting
# ---------------------------------------------------------------------------


def attempt_split_attribute(node, indent, line_length):
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


def stringify(node, indent, line_length):
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


def _get_stakeholder_list(children):
    """Return the list node inside a stakeholder, or None."""
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if not stakeholder:
        return None
    return next((c for c in stakeholder.children if c.type == "list"), None)


def _stakeholder_has_comments(children):
    list_node = _get_stakeholder_list(children)
    if not list_node:
        return False
    return any(c.type == "comment" for c in list_node.children)


def _has_trailing_comma(middle):
    """Check if list middle nodes end with a trailing comma."""
    for node in reversed(middle):
        if node.type == ",":
            return True
        if node.type != "comment":
            return False
    return False


def _promiser_text(children):
    """Return the raw promiser string, or None."""
    promiser_node = next((c for c in children if c.type == "promiser"), None)
    if not promiser_node:
        return None
    return text(promiser_node)


def _promiser_line_with_stakeholder(children):
    """Build 'promiser -> { stakeholder }' as a single-line string, or None."""
    prefix = _promiser_text(children)
    if not prefix:
        return None
    arrow = next((c for c in children if c.type == "->"), None)
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if arrow and stakeholder:
        prefix += " " + text(arrow) + " " + stringify_single_line_node(stakeholder)
    return prefix


def _stakeholder_needs_splitting(children, indent, line_length):
    """Check if the stakeholder list needs to be split across multiple lines."""
    if _stakeholder_has_comments(children):
        return True
    line = _promiser_line_with_stakeholder(children)
    if not line:
        return False
    return indent + len(line) > line_length


def _format_stakeholder_elements(middle, indent, line_length):
    """Format the middle elements (between { and }) of a stakeholder list."""
    if not any(n.type == "comment" for n in middle):
        if _has_trailing_comma(middle):
            return split_generic_list(middle, indent, line_length)
        return maybe_split_generic_list(middle, indent, line_length)
    # Comments present — format element-by-element to preserve them
    elements = []
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
    return elements


# ---------------------------------------------------------------------------
# Promise formatting
# ---------------------------------------------------------------------------


def _has_stakeholder(children):
    return any(c.type == "stakeholder" for c in children)


def can_single_line_promise(node, indent, line_length):
    """Check if a promise node can be formatted on a single line."""
    if node.type != "promise":
        return False
    children = node.children
    attrs = [c for c in children if c.type == "attribute"]
    next_sib = node.next_named_sibling
    if len(attrs) > 1 or (next_sib and next_sib.type == "half_promise"):
        return False
    if _has_stakeholder(children) and attrs:
        return False
    if _has_stakeholder(children) and _stakeholder_needs_splitting(
        children, indent, line_length
    ):
        return False
    line = _promiser_line_with_stakeholder(children)
    if not line:
        return False
    if attrs:
        line += " " + stringify_single_line_node(attrs[0]) + ";"
    else:
        line += ";"
    return indent + len(line) <= line_length


def _format_promise(node, children, fmt, indent, line_length, macro_indent):
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

        has_comments = _stakeholder_has_comments(children)
        close_indent = indent + 2 if (attrs or has_comments) else indent
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


def _format_remaining_children(children, fmt, indent, line_length, macro_indent):
    """Format promise children, skipping promiser/stakeholder parts."""
    for child in children:
        if child.type in PROMISER_PARTS:
            continue
        autoformat(child, fmt, line_length, macro_indent, indent)


# ---------------------------------------------------------------------------
# Block header formatting (bundle, body, promise blocks)
# ---------------------------------------------------------------------------


def _format_block_header(node, fmt):
    """Format block header and return the body's children list."""
    header_parts = []
    header_comments = []
    for x in node.children[0:-1]:
        if x.type == "comment":
            header_comments.append(text(x))
        elif x.type == "parameter_list":
            parts = []
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


def _needs_blank_line_before(child, indent, line_length):
    """Determine if a blank line should be inserted before this child."""
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


def _is_empty_comment(node):
    """Check if a bare '#' comment should be dropped."""
    if text(node).strip() != "#":
        return False
    prev = node.prev_named_sibling
    nxt = node.next_named_sibling
    return not (prev and prev.type == "comment" and nxt and nxt.type == "comment")


def _skip_comments(sibling, direction="next"):
    """Walk past adjacent comments to find the nearest non-comment sibling."""
    while sibling and sibling.type == "comment":
        sibling = (
            sibling.next_named_sibling
            if direction == "next"
            else sibling.prev_named_sibling
        )
    return sibling


def _comment_indent(node, indent):
    """Determine the indentation level for a leaf comment node."""
    nearest = _skip_comments(node.next_named_sibling, "next")
    if nearest is None:
        nearest = _skip_comments(node.prev_named_sibling, "prev")
    if nearest and nearest.type in INDENTED_TYPES:
        return indent + 2
    return indent


# ---------------------------------------------------------------------------
# Main recursive formatter
# ---------------------------------------------------------------------------


def autoformat(node, fmt, line_length, macro_indent, indent=0):
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


def format_policy_file(filename, line_length, check):
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


def format_policy_fin_fout(fin, fout, line_length, check):
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
