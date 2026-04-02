"""
Linting of CFEngine related files.

Currently implemented for:
- *.cf (policy files)
- cfbs.json (CFEngine Build project files)
- *.json (basic JSON syntax checking)

Linting is performed in 3 steps:
1. Parsing - Read the .cf code and convert it into syntax trees
2. Discovery - Walk the syntax trees and record what is defined
3. Checking - Walk the syntax tree again and check for errors

By default, linting is strict about bundles and bodies being
defined somewhere in the supplied files / folders. This
can be disabled using the `--strict=no` flag.

Usage:
$ cfengine lint
$ cfengine lint ./core/ ./masterfiles/
$ cfengine lint --strict=no main.cf
"""

import os
import json
import itertools
import tree_sitter_cfengine as tscfengine
from dataclasses import dataclass
from tree_sitter import Language, Parser
from cfbs.validate import validate_config
from cfbs.cfbs_config import CFBSConfig
from cfbs.utils import find
from cfengine_cli.policy_language import (
    DEPRECATED_PROMISE_TYPES,
    ALLOWED_BUNDLE_TYPES,
    BUILTIN_PROMISE_TYPES,
    BUILTIN_FUNCTIONS,
)


def _qualify(name: str, namespace: str) -> str:
    """If name is already qualified (contains ':'), return as-is. Otherwise prepend namespace."""
    if ":" in name:
        return name
    return f"{namespace}:{name}"


@dataclass
class State:
    block_type: str | None = None  # "bundle" | "body" | "promise" | None
    promise_type: str | None = None  # "vars" | "files" | "classes" | ... | None
    attribute_name: str | None = None  # "if" | "string" | "slist" | ... | None
    namespace: str = "default"  # "ns" | "default" | ... |
    user_definitions = {}

    def end_of_file(self):
        assert self.block_type is None
        assert self.promise_type is None
        assert self.attribute_name is None
        self.namespace = "default"

    def navigate(self, node):
        """This function is called whenever we move to a node, to update the
        state accordingly.

        For example:
        - When we encounter a closing } for a bundle, we want to set
          block_type from "bundle" to None
        """
        if node.type == "}":
            assert self.attribute_name is None  # Should already be cleared by ;
            assert node.parent
            assert node.parent.type in [
                "bundle_block_body",
                "promise_block_body",
                "body_block_body",
                "list",
            ]
            if node.parent.type == "list":
                return
            # We just ended a block
            self.block_type = None
            self.promise_type = None
            return
        if node.type == ";":
            self.attribute_name = None
            return
        if node.type == "bundle_block":
            self.block_type = "bundle"
            return
        if node.type == "body_block":
            self.block_type = "body"
            return
        if node.type == "promise_block":
            self.block_type = "promise"
            return
        if node.type == "bundle_section":
            # A bundle_section is always: promise_guard, [promises], [class_guarded_promises...]
            guard = next((c for c in node.children if c.type == "promise_guard"), None)
            assert guard  # guaranteed to exist by the grammar
            self.promise_type = _text(guard)[:-1]  # strip trailing ':'
            return
        if node.type == "attribute":
            for child in node.children:
                if child.type == "attribute_name":
                    self.attribute_name = _text(child)
                    if self.attribute_name == "namespace":
                        self.namespace = _text(child.next_named_sibling).strip("\"'")
                    return
        return


# TODO: Will remove this global in the future.
state = None


def _highlight_range(node, lines):
    line = node.range.start_point[0] + 1
    column = node.range.start_point[1]

    length = len(lines[line - 1]) - column
    if node.range.start_point[0] == node.range.end_point[0]:
        # Starts and ends on same line:
        length = node.range.end_point[1] - node.range.start_point[1]
    assert length >= 1
    print("")
    if line >= 2:
        print(lines[line - 2])
    print(lines[line - 1])
    marker = "^"
    if length > 2:
        marker += "-" * (length - 2)
    if length > 1:
        marker += "^"
    print(" " * column + marker)


def _text(node):
    return node.text.decode()


def _walk_generic(filename, lines, node, visitor):
    visitor(node)
    for node in node.children:
        _walk_generic(filename, lines, node, visitor)


def _find_node_type(filename, lines, node, node_type):
    matches = []
    visitor = lambda x: matches.extend([x] if x.type == node_type else [])
    _walk_generic(filename, lines, node, visitor)
    return matches


def _node_checks(filename, lines, node, strict):
    """Checks we run on each node in the syntax tree,
    utilizes state for checks which require context."""
    assert state
    line = node.range.start_point[0] + 1
    column = node.range.start_point[1] + 1
    if node.type == "attribute_name" and _text(node) == "ifvarclass":
        _highlight_range(node, lines)
        print(
            f"Deprecation: Use 'if' instead of 'ifvarclass' at {filename}:{line}:{column}"
        )
        return 1
    if node.type == "promise_guard":
        assert _text(node) and len(_text(node)) > 1 and _text(node)[-1] == ":"
        promise_type = _text(node)[0:-1]
        if promise_type in DEPRECATED_PROMISE_TYPES:
            _highlight_range(node, lines)
            print(
                f"Deprecation: Promise type '{promise_type}' is deprecated at {filename}:{line}:{column}"
            )
            return 1
        if strict and (
            (
                promise_type
                not in BUILTIN_PROMISE_TYPES.union(
                    state.user_definitions.get("custom_promise_types", set())
                )
            )
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Undefined promise type '{promise_type}' at {filename}:{line}:{column}"
            )
            return 1
    if node.type == "bundle_block_name":
        if _text(node) != _text(node).lower():
            _highlight_range(node, lines)
            print(
                f"Convention: Bundle name should be lowercase at {filename}:{line}:{column}"
            )
            return 1
    if node.type == "promise_block_name":
        if _text(node) != _text(node).lower():
            _highlight_range(node, lines)
            print(
                f"Convention: Promise type should be lowercase at {filename}:{line}:{column}"
            )
            return 1
    if node.type == "bundle_block_type":
        if _text(node) not in ALLOWED_BUNDLE_TYPES:
            _highlight_range(node, lines)
            print(
                f"Error: Bundle type must be one of ({', '.join(ALLOWED_BUNDLE_TYPES)}), not '{_text(node)}' at {filename}:{line}:{column}"
            )
            return 1
    if node.type == "calling_identifier":
        if (
            strict
            and _qualify(_text(node), state.namespace)
            in state.user_definitions.get("all_bundle_names", set())
            and state.promise_type
            in state.user_definitions.get("custom_promise_types", set())
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to bundle '{_text(node)}' inside custom promise: '{state.promise_type}' at {filename}:{line}:{column}"
            )
            return 1
        if strict and (
            _qualify(_text(node), state.namespace)
            not in set.union(
                state.user_definitions.get("all_bundle_names", set()),
                state.user_definitions.get("all_body_names", set()),
            )
            and _text(node) not in BUILTIN_FUNCTIONS
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to unknown function / bundle / body '{_text(node)}' at at {filename}:{line}:{column}"
            )
            return 1
    return 0


def _stateful_walk(filename, lines, node, strict) -> int:
    global state
    if state is None:
        state = State()

    errors = _node_checks(filename, lines, node, strict)

    state.navigate(node)
    for child in node.children:
        errors += _stateful_walk(filename, lines, child, strict)
    return errors


def _walk(filename, lines, node, user_definitions=None, strict=True) -> int:
    if user_definitions is None:
        user_definitions = {}

    error_nodes = _find_node_type(filename, lines, node, "ERROR")
    if error_nodes:
        for node in error_nodes:
            line = node.range.start_point[0] + 1
            column = node.range.start_point[1] + 1
            _highlight_range(node, lines)
            print(f"Error: Syntax error at {filename}:{line}:{column}")
        return len(error_nodes)

    line = node.range.start_point[0] + 1
    column = node.range.start_point[1] + 1

    return _stateful_walk(filename, lines, node, strict)


def _parse_user_definitions(filename, lines, root_node):
    ns = "default"
    promise_blocks = set()
    bundle_blocks = set()
    body_blocks = set()

    for child in root_node.children:
        if child.type == "body_block":
            name_node = next(
                (c for c in child.named_children if c.type == "body_block_name"),
                None,
            )
            ns_attr = next(
                (
                    c
                    for c in _find_node_type(filename, lines, child, "attribute_name")
                    if _text(c) == "namespace"
                ),
                None,
            )
            if ns_attr is not None:
                ns = _text(ns_attr.next_named_sibling).strip("\"'")
            elif name_node is not None:
                body_blocks.add(_qualify(_text(name_node), ns))
        elif child.type == "bundle_block":
            name_node = next(
                (c for c in child.named_children if c.type == "bundle_block_name"),
                None,
            )
            if name_node is not None:
                bundle_blocks.add(_qualify(_text(name_node), ns))
        elif child.type == "promise_block":
            name_node = next(
                (c for c in child.named_children if c.type == "promise_block_name"),
                None,
            )
            if name_node is not None:
                promise_blocks.add(_text(name_node))

    return {
        "custom_promise_types": promise_blocks,
        "all_bundle_names": bundle_blocks,
        "all_body_names": body_blocks,
    }


def _parse_policy_file(filename):
    assert os.path.isfile(filename)
    PY_LANGUAGE = Language(tscfengine.language())
    parser = Parser(PY_LANGUAGE)

    with open(filename, "rb") as f:
        original_data = f.read()
    tree = parser.parse(original_data)
    lines = original_data.decode().split("\n")

    return tree, lines, original_data


def _lint_policy_file(
    filename,
    original_filename=None,
    original_line=None,
    snippet=None,
    prefix=None,
    strict=True,
):
    assert state
    assert original_filename is None or type(original_filename) is str
    assert original_line is None or type(original_line) is int
    assert snippet is None or type(snippet) is int
    if (
        original_filename is not None
        or original_line is not None
        or snippet is not None
    ):
        assert original_filename and os.path.isfile(original_filename)
        assert original_line and original_line > 0
        assert snippet and snippet > 0
    assert os.path.isfile(filename)
    assert filename.endswith((".cf", ".cfengine3", ".cf3", ".cf.sub"))

    tree, lines, original_data = _parse_policy_file(filename)
    root_node = tree.root_node
    if root_node.type != "source_file":
        if snippet:
            assert original_filename and original_line
            print(
                f"Error: Failed to parse policy snippet {snippet} at '{original_filename}:{original_line}'"
            )
        else:
            print(f"       Empty policy file '{filename}'")
        print("       Is this valid CFEngine policy?")
        print("")
        lines = original_data.decode().split("\n")
        if not len(lines) <= 5:
            lines = lines[:4] + ["..."]
        for line in lines:
            print("       " + line)
        print("")
        return 1
    assert root_node.type == "source_file"
    errors = 0
    if not root_node.children:
        if snippet:
            assert original_filename and original_line
            print(
                f"Error: Empty policy snippet {snippet} at '{original_filename}:{original_line}'"
            )
        else:
            print(f"Error: Empty policy file '{filename}'")
        errors += 1
    errors += _walk(filename, lines, root_node, state, strict)
    if prefix:
        print(prefix, end="")
    if errors == 0:
        if snippet:
            assert original_filename and original_line
            print(
                f"PASS: Snippet {snippet} at '{original_filename}:{original_line}' (cf3)"
            )
        else:
            print(f"PASS: {filename}")
        return 0

    if snippet:
        assert original_filename and original_line
        print(f"FAIL: Snippet {snippet} at '{original_filename}:{original_line}' (cf3)")
    else:
        print(f"FAIL: {filename} ({errors} error{'s' if errors > 0 else ''})")
    return errors


def _lint_folder(folder, strict=True):
    errors = 0
    policy_files = []
    while folder.endswith(("/.", "/")):
        folder = folder[0:-1]
    for filename in itertools.chain(
        find(folder, extension=".json"), find(folder, extension=".cf")
    ):
        if filename.startswith(("./.", "./out/", folder + "/.", folder + "/out/")):
            continue
        if filename.startswith(".") and not filename.startswith("./"):
            continue

        if filename.endswith((".cf", ".cfengine3", ".cf3", ".cf.sub")):
            policy_files.append(filename)
        else:
            errors += lint_single_file(filename)

    # Second pass: lint all policy files
    for filename in policy_files:
        errors += _lint_policy_file(filename, strict=strict)
    return errors


def _lint_single_file(file, strict=True):
    assert os.path.isfile(file)
    if file.endswith("/cfbs.json"):
        return lint_cfbs_json(file)
    if file.endswith(".json"):
        return lint_json(file)
    assert file.endswith(".cf")
    return lint_policy_file(file, strict=strict)


def _lint_single_arg(arg, strict=True):
    if os.path.isdir(arg):
        return _lint_folder(arg, strict)
    assert os.path.isfile(arg)

    return _lint_single_file(arg, strict=strict)


def _discovery_file(filename):
    assert state
    tree, lines, _ = _parse_policy_file(filename)
    assert tree.root_node.type == "source_file"
    for key, val in _parse_user_definitions(filename, lines, tree.root_node).items():
        state.user_definitions[key] = state.user_definitions.get(key, set()).union(val)
    state.end_of_file()


def _discovery_folder(folder):
    assert os.path.isdir(folder)
    for filename in os.listdir(folder):
        _discovery_file(folder + "/" + filename)


def _discovery_args(args):
    for arg in args:
        if (
            arg in ("/", ".", "./", "~", "~/")
            or arg.endswith("/")
            or os.path.isdir(arg)
        ):
            _discovery_folder(arg)
        else:
            _discovery_file(arg)


# Interface: These are the functions we want to be called from outside
# They create State() and should not be called recursively inside lint.py


def lint_single_file(file, strict=True):
    global state
    state = State()
    _discovery_file(file)
    return _lint_single_file(file, strict)


def lint_args(args, strict):
    global state
    state = State()
    _discovery_args(args)
    errors = 0
    for arg in args:
        errors += _lint_single_arg(arg, strict)
    return errors


def lint_policy_file(
    filename,
    original_filename=None,
    original_line=None,
    snippet=None,
    prefix=None,
    strict=True,
):
    global state
    state = State()
    _discovery_file(filename)
    return _lint_policy_file(
        filename=filename,
        original_filename=original_filename,
        original_line=original_line,
        snippet=snippet,
        prefix=prefix,
        strict=strict,
    )


def lint_cfbs_json(filename) -> int:
    assert os.path.isfile(filename)
    assert filename.endswith("cfbs.json")

    config = CFBSConfig.get_instance(filename=filename, non_interactive=True)
    r = validate_config(config)

    if r == 0:
        print(f"PASS: {filename}")
        return 0
    print(f"FAIL: {filename}")
    return r


def lint_json(filename) -> int:
    assert os.path.isfile(filename)

    with open(filename, "r") as f:
        data = f.read()

    try:
        data = json.loads(data)
    except:
        print(f"FAIL: {filename} (invalid JSON)")
        return 1
    print(f"PASS: {filename}")
    return 0
