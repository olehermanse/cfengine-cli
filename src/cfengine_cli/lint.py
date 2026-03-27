"""
Linting of CFEngine related files.

Currently implemented for:
- *.cf (policy files)
- cfbs.json (CFEngine Build project files)
- *.json (basic JSON syntax checking)

Usage:
$ cfengine lint
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


@dataclass
class _State:
    block_type: str | None = None  # "bundle" | "body" | "promise" | None
    promise_type: str | None = None  # "vars" | "files" | "classes" | ... | None
    attribute_name: str | None = None  # "if" | "string" | "slist" | ... | None

    def update(self, node) -> "_State":
        """Updates and returns the state that should apply to the children of `node`."""
        if node.type == "bundle_block":
            return _State(block_type="bundle")
        if node.type == "body_block":
            return _State(block_type="body")
        if node.type == "promise_block":
            return _State(block_type="promise")
        if node.type == "bundle_section":
            for child in node.children:
                if child.type == "promise_guard":
                    return _State(
                        block_type=self.block_type,
                        promise_type=_text(child)[:-1],  # strip trailing ':'
                    )
            return _State(block_type=self.block_type)
        if node.type == "attribute":
            for child in node.children:
                if child.type == "attribute_name":
                    return _State(
                        block_type=self.block_type,
                        promise_type=self.promise_type,
                        attribute_name=_text(child),
                    )
        return self


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


def _node_checks(filename, lines, node, user_definition, strict, state: _State):
    """Checks we run on each node in the syntax tree,
    utilizes state for checks which require context."""
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
                    user_definition.get("custom_promise_types", set())
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
            and _text(node) in user_definition.get("all_bundle_names", set())
            and state.promise_type in user_definition.get("custom_promise_types", set())
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to bundle '{_text(node)}' inside custom promise: '{state.promise_type}' at {filename}:{line}:{column}"
            )
            return 1
        if strict and (
            _text(node)
            not in BUILTIN_FUNCTIONS.union(
                user_definition.get("all_bundle_names", set()),
                user_definition.get("all_body_names", set()),
            )
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to unknown function / bundle / body '{_text(node)}' at at {filename}:{line}:{column}"
            )
            return 1
    return 0


def _stateful_walk(
    filename, lines, node, user_definition, strict, state: _State | None = None
) -> int:
    if state is None:
        state = _State()

    errors = _node_checks(filename, lines, node, user_definition, strict, state)

    child_state = state.update(node)
    for child in node.children:
        errors += _stateful_walk(
            filename, lines, child, user_definition, strict, child_state
        )
    return errors


def _walk(filename, lines, node, user_definition=None, strict=True) -> int:
    if user_definition is None:
        user_definition = {}

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

    return _stateful_walk(filename, lines, node, user_definition, strict)


def _parse_user_definition(filename, lines, root_node):
    promise_blocks = _find_node_type(filename, lines, root_node, "promise_block_name")
    bundle_blocks = _find_node_type(filename, lines, root_node, "bundle_block_name")
    body_blocks = _find_node_type(filename, lines, root_node, "body_block_name")

    return {
        "custom_promise_types": {_text(x) for x in promise_blocks},
        "all_bundle_names": {_text(x) for x in bundle_blocks},
        "all_body_names": {_text(x) for x in body_blocks},
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


def lint_policy_file(
    filename,
    original_filename=None,
    original_line=None,
    snippet=None,
    prefix=None,
    user_definition=None,
    strict=True,
):
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

    if user_definition is None:
        user_definition = {}

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
    errors += _walk(filename, lines, root_node, user_definition, strict)
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


def lint_folder(folder, strict=True):
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

    user_definition = {}

    # First pass: Gather custom types
    for filename in policy_files if strict else []:
        tree, lines, _ = _parse_policy_file(filename)
        if tree.root_node.type == "source_file":
            for key, val in _parse_user_definition(
                filename, lines, tree.root_node
            ).items():
                user_definition[key] = user_definition.get(key, set()).union(val)

    # Second pass: lint all policy files
    for filename in policy_files:
        errors += lint_policy_file(
            filename, user_definition=user_definition, strict=strict
        )
    return errors


def lint_single_file(file, user_definition=None, strict=True):
    assert os.path.isfile(file)
    if file.endswith("/cfbs.json"):
        return lint_cfbs_json(file)
    if file.endswith(".json"):
        return lint_json(file)
    assert file.endswith(".cf")
    return lint_policy_file(file, user_definition=user_definition, strict=strict)


def lint_single_arg(arg, strict=True):
    if os.path.isdir(arg):
        return lint_folder(arg, strict)
    assert os.path.isfile(arg)

    return lint_single_file(arg, strict)
