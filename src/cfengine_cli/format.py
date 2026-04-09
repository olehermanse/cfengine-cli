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


def can_single_line_promise(node, indent, line_length):
    """Check if a promise node can be formatted on a single line."""
    if node.type != "promise":
        return False
    children = node.children
    attr_children = [c for c in children if c.type == "attribute"]
    next_sib = node.next_named_sibling
    has_continuation = next_sib and next_sib.type == "half_promise"
    if len(attr_children) != 1 or has_continuation:
        return False
    promiser_node = next((c for c in children if c.type == "promiser"), None)
    if not promiser_node:
        return False
    line = (
        text(promiser_node) + " " + stringify_single_line_node(attr_children[0]) + ";"
    )
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
        # Single-line promise: if exactly 1 attribute, no half_promise continuation,
        # not inside a class guard, and the whole line fits in line_length
        attr_children = [c for c in children if c.type == "attribute"]
        next_sib = node.next_named_sibling
        has_continuation = next_sib and next_sib.type == "half_promise"
        if len(attr_children) == 1 and not has_continuation:
            promiser_node = next((c for c in children if c.type == "promiser"), None)
            if promiser_node:
                line = (
                    text(promiser_node)
                    + " "
                    + stringify_single_line_node(attr_children[0])
                    + ";"
                )
                if indent + len(line) <= line_length:
                    fmt.print(line, indent)
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
