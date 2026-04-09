import tree_sitter_cfengine as tscfengine
from tree_sitter import Language, Parser, Node
from cfbs.pretty import pretty_file


def format_json_file(filename):
    assert filename.endswith(".json")
    r = pretty_file(filename)
    if r:
        print(f"JSON file '{filename}' was reformatted")


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
        # print(message, end=end)
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

    def update_previous(self, node):
        tmp = self.previous
        self.previous = node
        return tmp


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

    Operates on the direct child nodes of a CFEngine syntax construct
    (e.g. a list, call, or attribute). Each child is recursively
    flattened via stringify_single_line_node(). Spacing rules:
      - A space is inserted after each "," separator.
      - A space is inserted before and after "=>" (fat arrow).
      - No extra space otherwise (e.g. no space after "(" or before ")").

    Used by stringify_single_line_node() to recursively flatten any node with
    children, and by maybe_split_generic_list() to attempt a single-line
    rendering before falling back to multi-line splitting.
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


def has_stakeholder(children):
    return any(c.type == "stakeholder" for c in children)


def stakeholder_has_comments(children):
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if not stakeholder:
        return False
    for child in stakeholder.children:
        if child.type == "list":
            return any(c.type == "comment" for c in child.children)
    return False


def promiser_prefix(children):
    """Build the promiser text (without stakeholder)."""
    promiser_node = next((c for c in children if c.type == "promiser"), None)
    if not promiser_node:
        return None
    return text(promiser_node)


def promiser_line(children):
    """Build the promiser prefix: promiser + optional '-> stakeholder'."""
    prefix = promiser_prefix(children)
    if not prefix:
        return None
    arrow = next((c for c in children if c.type == "->"), None)
    stakeholder = next((c for c in children if c.type == "stakeholder"), None)
    if arrow and stakeholder:
        prefix += " " + text(arrow) + " " + stringify_single_line_node(stakeholder)
    return prefix


def stakeholder_needs_splitting(children, indent, line_length):
    """Check if the stakeholder list needs to be split across multiple lines."""
    if stakeholder_has_comments(children):
        return True
    prefix = promiser_line(children)
    if not prefix:
        return False
    return indent + len(prefix) > line_length


def split_stakeholder(children, indent, has_attributes, line_length):
    """Split a stakeholder list across multiple lines.

    Returns (opening_line, element_lines, closing_str) where:
    - opening_line: 'promiser -> {' to print at promise indent
    - element_lines: pre-indented element strings
    - closing_str: '}' or '};' pre-indented at the appropriate level
    """
    prefix = promiser_prefix(children)
    assert prefix is not None
    opening = prefix + " -> {"
    stakeholder = next(c for c in children if c.type == "stakeholder")
    list_node = next(c for c in stakeholder.children if c.type == "list")
    middle = list_node.children[1:-1]  # between { and }
    element_indent = indent + 4
    has_comments = stakeholder_has_comments(children)
    if has_attributes or has_comments:
        close_indent = indent + 2
    else:
        close_indent = indent
    elements = format_stakeholder_elements(middle, element_indent, line_length)
    return opening, elements, close_indent


def has_trailing_comma(middle):
    """Check if a list's middle nodes end with a trailing comma."""
    for node in reversed(middle):
        if node.type == ",":
            return True
        if node.type != "comment":
            return False
    return False


def format_stakeholder_elements(middle, indent, line_length):
    """Format the middle elements of a stakeholder list."""
    has_comments = any(n.type == "comment" for n in middle)
    if not has_comments:
        if has_trailing_comma(middle):
            return split_generic_list(middle, indent, line_length)
        return maybe_split_generic_list(middle, indent, line_length)
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


def can_single_line_promise(node, indent, line_length):
    """Check if a promise node can be formatted on a single line."""
    if node.type != "promise":
        return False
    children = node.children
    attr_children = [c for c in children if c.type == "attribute"]
    next_sib = node.next_named_sibling
    has_continuation = next_sib and next_sib.type == "half_promise"
    if len(attr_children) > 1 or has_continuation:
        return False
    # Promises with stakeholder + attributes are always multi-line
    if has_stakeholder(children) and attr_children:
        return False
    # Stakeholders that need splitting can't be single-lined
    if has_stakeholder(children) and stakeholder_needs_splitting(
        children, indent, line_length
    ):
        return False
    prefix = promiser_line(children)
    if not prefix:
        return False
    if attr_children:
        line = prefix + " " + stringify_single_line_node(attr_children[0]) + ";"
    else:
        line = prefix + ";"
    return indent + len(line) <= line_length


def autoformat(node, fmt, line_length, macro_indent, indent=0):
    previous = fmt.update_previous(node)
    if previous and previous.type == "macro" and text(previous).startswith("@else"):
        indent = macro_indent
    if node.type == "macro":
        fmt.print(node, 0)
        if text(node).startswith("@if"):
            macro_indent = indent
        elif text(node).startswith("@else"):
            indent = macro_indent
        return
    children = node.children
    if node.type in ["bundle_block", "promise_block", "body_block"]:
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
                # Append directly to previous part (no space before parens)
                header_parts[-1] = header_parts[-1] + stringify_parameter_list(parts)
            else:
                header_parts.append(text(x))
        line = " ".join(header_parts)
        if not fmt.empty:
            prev_sib = node.prev_named_sibling
            if not (prev_sib and prev_sib.type == "comment"):
                fmt.print("", 0)
        fmt.print(line, 0)
        for i, comment in enumerate(header_comments):
            if comment.strip() == "#":
                prev_is_comment = i > 0 and header_comments[i - 1].strip() != "#"
                next_is_comment = (
                    i + 1 < len(header_comments)
                    and header_comments[i + 1].strip() != "#"
                )
                if not (prev_is_comment and next_is_comment):
                    continue
            fmt.print(comment, 0)
        children = node.children[-1].children
    if node.type in [
        "bundle_section",
        "class_guarded_promises",
        "class_guarded_body_attributes",
        "class_guarded_promise_block_attributes",
        "promise",
        "half_promise",
        "attribute",
    ]:
        indent += 2
    if node.type == "attribute":
        lines = stringify(node, indent, line_length)
        fmt.print_lines(lines, indent=0)
        return
    if node.type == "promise":
        if can_single_line_promise(node, indent, line_length):
            prefix = promiser_line(children)
            assert prefix is not None
            attr_node = next((c for c in children if c.type == "attribute"), None)
            if attr_node:
                line = prefix + " " + stringify_single_line_node(attr_node) + ";"
            else:
                line = prefix + ";"
            fmt.print(line, indent)
            return
        # Multi-line promise with stakeholder that needs splitting
        attr_children = [c for c in children if c.type == "attribute"]
        if has_stakeholder(children) and stakeholder_needs_splitting(
            children, indent, line_length
        ):
            opening, elements, close_indent = split_stakeholder(
                children, indent, bool(attr_children), line_length
            )
            fmt.print(opening, indent)
            fmt.print_lines(elements, indent=0)
            if attr_children:
                fmt.print("}", close_indent)
            else:
                fmt.print("};", close_indent)
                return
            for child in children:
                if child.type in {"promiser", "->", "stakeholder"}:
                    continue
                autoformat(child, fmt, line_length, macro_indent, indent)
            return
        # Multi-line promise: print promiser (with stakeholder) then recurse for rest
        prefix = promiser_line(children)
        if prefix:
            fmt.print(prefix, indent)
            for child in children:
                if child.type in {"promiser", "->", "stakeholder"}:
                    continue
                autoformat(child, fmt, line_length, macro_indent, indent)
            return
    if children:
        for child in children:
            # Blank line between bundle sections
            if child.type == "bundle_section":
                prev = child.prev_named_sibling
                if prev and prev.type == "bundle_section":
                    fmt.print("", 0)
            # Blank line between promises in a section
            elif child.type == "promise":
                prev = child.prev_named_sibling
                if prev and prev.type in ["promise", "half_promise"]:
                    # Skip blank line between consecutive single-line promises
                    promise_indent = indent + 2
                    both_single = (
                        prev.type == "promise"
                        and can_single_line_promise(prev, promise_indent, line_length)
                        and can_single_line_promise(child, promise_indent, line_length)
                    )
                    if not both_single:
                        fmt.print("", 0)
            elif child.type in [
                "class_guarded_promises",
                "class_guarded_body_attributes",
                "class_guarded_promise_block_attributes",
            ]:
                prev = child.prev_named_sibling
                if prev and prev.type in [
                    "promise",
                    "half_promise",
                    "class_guarded_promises",
                ]:
                    fmt.print("", 0)
            elif child.type == "comment":
                prev = child.prev_named_sibling
                if prev and prev.type in [
                    "promise",
                    "half_promise",
                    "class_guarded_promises",
                    "class_guarded_body_attributes",
                    "class_guarded_promise_block_attributes",
                ]:
                    parent = child.parent
                    if parent and parent.type in [
                        "bundle_section",
                        "class_guarded_promises",
                    ]:
                        fmt.print("", 0)
            autoformat(child, fmt, line_length, macro_indent, indent)
        return
    if node.type in [",", ";"]:
        fmt.print_same_line(node)
        return
    if node.type == "comment":
        if text(node).strip() == "#":
            prev = node.prev_named_sibling
            nxt = node.next_named_sibling
            if not (prev and prev.type == "comment" and nxt and nxt.type == "comment"):
                return
        comment_indent = indent
        next_sib = node.next_named_sibling
        while next_sib and next_sib.type == "comment":
            next_sib = next_sib.next_named_sibling
        if next_sib is None:
            prev_sib = node.prev_named_sibling
            while prev_sib and prev_sib.type == "comment":
                prev_sib = prev_sib.prev_named_sibling
            if prev_sib and prev_sib.type in [
                "bundle_section",
                "class_guarded_promises",
                "class_guarded_body_attributes",
                "class_guarded_promise_block_attributes",
                "promise",
                "half_promise",
                "attribute",
            ]:
                comment_indent = indent + 2
        elif next_sib.type in [
            "bundle_section",
            "class_guarded_promises",
            "class_guarded_body_attributes",
            "class_guarded_promise_block_attributes",
            "promise",
            "half_promise",
            "attribute",
        ]:
            comment_indent = indent + 2
        fmt.print(node, comment_indent)
        return
    fmt.print(node, indent)


def format_policy_file(filename, line_length):
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
        with open(filename, "w") as f:
            f.write(new_data)
        print(f"Policy file '{filename}' was reformatted")


def format_policy_fin_fout(fin, fout, line_length):
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
