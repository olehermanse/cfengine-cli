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

from enum import Enum
import os
import json
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
    assert '"' not in namespace
    assert '"' not in name
    if ":" in name:
        return name
    return f"{namespace}:{name}"


class Mode(Enum):
    NONE = None
    SYNTAX = 1
    DISCOVER = 2
    LINT = 3


@dataclass
class State:
    block_keyword: str | None = None  # "bundle" | "body" | "promise" | None
    block_type: str | None = None
    block_name: str | None = None
    promise_type: str | None = None  # "vars" | "files" | "classes" | ... | None
    attribute_name: str | None = None  # "if" | "string" | "slist" | ... | None
    namespace: str = "default"  # "ns" | "default" | ... |
    mode: Mode = Mode.NONE
    walking: bool = False
    strict: bool = True
    bundles = {}
    bodies = {}
    custom_promise_types = {}
    policy_file = None

    def print_summary(self):
        print("Bundles")
        print(self.bundles)
        print("Bodies")
        print(self.bodies)
        print("Custom promise types")
        print(self.custom_promise_types)

    def block_string(self) -> str | None:
        if not (self.block_keyword and self.block_type and self.block_name):
            return None

        return " ".join((self.block_keyword, self.block_type, self.block_name))

    def start_file(self, policy_file):
        assert not self.walking
        assert self.mode != Mode.NONE
        self.policy_file = policy_file
        self.namespace = "default"
        self.walking = True

    def end_of_file(self):
        assert self.walking
        assert self.mode != Mode.NONE
        assert self.block_keyword is None
        assert self.promise_type is None
        assert self.attribute_name is None
        self.walking = False
        self.policy_file = None

    def add_bundle(self, name: str):
        name = _qualify(name, self.namespace)
        # TODO: In the future we will record more information than True, like:
        #       - Can be a list / dict of all places a bundle with that
        #         qualified name is defined in cases there are duplicates.
        #       - Can record the location of each definition
        #       - Can record the parameters / signature
        #       - Can record whether the bundle is inside a macro
        #       - Can have a list of classes and vars defined inside
        self.bundles[name] = True

    def add_body(self, name: str):
        name = _qualify(name, self.namespace)
        self.bodies[name] = True

    def add_promise_type(self, name: str):
        self.custom_promise_types[name] = True

    def navigate(self, node):
        """This function is called whenever we move to a node, to update the
        state accordingly.

        For example:
        - When we encounter a closing } for a bundle, we want to set
          block_keyword from "bundle" to None
        """
        assert self.mode != Mode.NONE
        assert self.walking

        # Beginnings of blocks:
        if node.type in (
            "bundle_block_keyword",
            "body_block_keyword",
            "promise_block_keyword",
        ):
            self.block_keyword = _text(node)
            assert self.block_keyword in ("bundle", "body", "promise")
            return
        if node.type in (
            "bundle_block_type",
            "body_block_type",
            "promise_block_type",
        ):
            self.block_type = _text(node)
            assert self.block_type
            return
        if node.type in (
            "bundle_block_name",
            "body_block_name",
            "promise_block_name",
        ):
            self.block_name = _text(node)
            assert self.block_name
            return

        # Update namespace inside body file control:
        if (
            self.block_string() == "body file control"
            and self.attribute_name == "namespace"
            and node.type == "quoted_string"
        ):
            self.namespace = _text(node)[1:-1]
            return

        # New promise type (bundle section) inside a bundle:
        if node.type == "promise_guard":
            self.promise_type = _text(node)[:-1]  # strip trailing ':'
            return

        if node.type == "attribute_name":
            self.attribute_name = _text(node)
            return

        # Attributes always end with ; in all 3 block types
        if node.type == ";":
            self.attribute_name = None
            return

        # Clear things when ending a top level block:
        if node.type == "}" and node.parent.type != "list":
            assert self.attribute_name is None  # Should already be cleared by ;
            assert node.parent
            assert node.parent.type in [
                "bundle_block_body",
                "promise_block_body",
                "body_block_body",
            ]
            self.block_keyword = None
            self.block_type = None
            self.block_name = None
            self.promise_type = None
            return


class PolicyFile:
    def __init__(self, filename):
        self.filename = filename
        tree, lines, original_data = _parse_policy_file(filename)
        self.tree = tree
        self.lines = lines
        self.original_data = original_data

        # Flatten tree so it is easier to iterate over:
        self.nodes = []

        def visit(x):
            self.nodes.append(x)
            return 0

        _walk_callback(tree.root_node, visit)


def _check_syntax(policy_file: PolicyFile) -> int:
    assert state
    assert state.mode == Mode.SYNTAX
    filename = policy_file.filename
    lines = policy_file.lines
    errors = 0
    if not policy_file.tree.root_node.children:
        print(f"Error: Empty policy file '{filename}'")
        return 1

    assert policy_file.tree.root_node.type == "source_file"

    state.start_file(policy_file)
    for node in policy_file.nodes:
        state.navigate(node)
        _discover_node(node)
        if node.type != "ERROR":
            continue
        line = node.range.start_point[0] + 1
        column = node.range.start_point[1] + 1
        _highlight_range(node, lines)
        print(f"Error: Syntax error at {filename}:{line}:{column}")
        errors += 1
    state.end_of_file()
    return errors


def _discover_node(node) -> int:
    assert state
    # Define bodies:
    if node.type == "body_block_name":
        name = _text(node)
        if name == "control":
            return 0  # No need to define control blocks
        state.add_body(name)
        return 0

    # Define bundles:
    if node.type == "bundle_block_name":
        name = _text(node)
        state.add_bundle(name)
        return 0

    # Define custom promise types:
    if node.type == "promise_block_name":
        state.add_promise_type(_text(node))
        return 0

    return 0


def _discover(policy_file: PolicyFile) -> int:
    assert state
    assert state.mode == Mode.DISCOVER
    state.start_file(policy_file)
    for node in policy_file.nodes:
        state.navigate(node)
        _discover_node(node)
    state.end_of_file()
    return 0


def _lint_node(node, policy_file):
    return _node_checks(policy_file.filename, policy_file.lines, node)


def _lint(policy_file: PolicyFile) -> int:
    assert state
    assert state.mode == Mode.LINT
    errors = 0
    state.start_file(policy_file)
    for node in policy_file.nodes:
        state.navigate(node)
        errors += _lint_node(node, policy_file)
    state.end_of_file()
    if errors == 0:
        print(f"PASS: {policy_file.filename}")
    else:
        print(
            f"FAIL: {policy_file.filename} ({errors} error{'s' if errors > 1 else ''})"
        )
    return errors


def _find_policy_files(args):
    for arg in args:
        if os.path.isdir(arg):
            while arg.endswith(("/.", "/")):
                arg = arg[0:-1]
            for result in find(arg, extension=".cf"):
                yield result
        elif arg.endswith(".cf"):
            yield arg


def _find_json_files(args):
    for arg in args:
        if os.path.isdir(arg):
            for result in find(arg, extension=".json"):
                yield result
        elif arg.endswith(".json"):
            yield arg


def filter_filenames(filenames):
    for filename in filenames:
        if "/out/" in filename or "/." in filename:
            continue
        if filename.startswith(".") and not filename.startswith("./"):
            continue
        if filename.startswith("out/"):
            continue
        yield filename


def _lint_main(args: list, strict: bool) -> int:
    errors = 0

    global state
    state = State()
    state.strict = strict
    state.mode = Mode.SYNTAX

    json_filenames = filter_filenames(_find_json_files(args))
    policy_filenames = filter_filenames(_find_policy_files(args))

    # TODO: JSON checking could be split into parse
    #       and additional checks for cfbs.json.
    #       The second step could happen after discovery for consistency.
    for file in json_filenames:
        errors += _lint_json(file)

    policy_files = []
    for filename in policy_filenames:
        policy_file = PolicyFile(filename)
        r = _check_syntax(policy_file)
        errors += r
        if r != 0:
            print(f"FAIL: {filename} ({errors} error{'s' if errors > 1 else ''})")
            continue
        policy_files.append(policy_file)
    if errors != 0:
        return errors
    state.mode = Mode.DISCOVER

    for policy_file in policy_files:
        errors += _discover(policy_file)

    state.mode = Mode.LINT

    for policy_file in policy_files:
        errors += _lint(policy_file)

    return errors


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


def _walk_callback(node, callback) -> int:
    assert node
    assert callback

    errors = 0
    errors += callback(node)
    for child in node.children:
        _walk_callback(child, callback)
    return errors


def _node_checks(filename, lines, node):
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
        if (
            state.strict
            and promise_type not in BUILTIN_PROMISE_TYPES
            and promise_type not in state.custom_promise_types
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
        name = _text(node)
        qualified_name = _qualify(name, state.namespace)
        if (
            state.strict
            and qualified_name in state.bundles
            and state.promise_type in state.custom_promise_types
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to bundle '{name}' inside custom promise: '{state.promise_type}' at {filename}:{line}:{column}"
            )
            return 1
        if state.strict and (
            qualified_name not in state.bundles
            and qualified_name not in state.bodies
            and name not in BUILTIN_FUNCTIONS
        ):
            _highlight_range(node, lines)
            print(
                f"Error: Call to unknown function / bundle / body '{name}' at at {filename}:{line}:{column}"
            )
            return 1
    return 0


def _stateful_walk(filename, lines, node) -> int:
    assert state
    errors = 0

    def visit(x):
        nonlocal errors
        assert state
        state.navigate(node)
        if state.mode == Mode.LINT:
            errors += _node_checks(filename, lines, node)
        return errors

    return _walk_callback(node, visit)


def _walk(filename, lines, node) -> int:
    return _stateful_walk(filename, lines, node)


def _parse_policy_file(filename):
    assert os.path.isfile(filename)
    PY_LANGUAGE = Language(tscfengine.language())
    parser = Parser(PY_LANGUAGE)

    with open(filename, "rb") as f:
        original_data = f.read()
    tree = parser.parse(original_data)
    lines = original_data.decode().split("\n")

    return tree, lines, original_data


def _lint_policy_file_snippet(
    filename,
    original_filename,
    original_line,
    snippet,
    prefix,
):
    assert state
    assert prefix
    assert type(original_filename) is str
    assert type(original_line) is int
    assert type(snippet) is int
    assert type(prefix) is str
    assert original_filename and os.path.isfile(original_filename)
    assert original_line and original_line > 0
    assert snippet and snippet > 0
    assert os.path.isfile(filename)
    assert filename.endswith((".cf", ".cfengine3", ".cf3", ".cf.sub"))

    tree, lines, original_data = _parse_policy_file(filename)
    root_node = tree.root_node
    assert root_node.type == "source_file"
    errors = 0
    if not root_node.children:
        assert original_filename and original_line
        print(
            f"Error: Empty policy snippet {snippet} at '{original_filename}:{original_line}'"
        )
        errors += 1
    state.start_file(filename)
    errors += _walk(filename, lines, root_node)
    state.end_of_file()
    print(prefix, end="")
    if errors == 0:
        assert original_filename and original_line
        print(f"PASS: Snippet {snippet} at '{original_filename}:{original_line}' (cf3)")
        return 0

    assert original_filename and original_line
    print(f"FAIL: Snippet {snippet} at '{original_filename}:{original_line}' (cf3)")
    return errors


def _lint_policy_file(
    filename,
    prefix=None,
):
    assert state
    assert os.path.isfile(filename)
    assert filename.endswith((".cf", ".cfengine3", ".cf3", ".cf.sub"))

    tree, lines, original_data = _parse_policy_file(filename)
    root_node = tree.root_node
    assert root_node.type == "source_file"
    errors = 0
    if not root_node.children:
        print(f"Error: Empty policy file '{filename}'")
        errors += 1
    state.start_file(filename)
    errors += _walk(filename, lines, root_node)
    state.end_of_file()
    if prefix:
        print(prefix, end="")
    if errors == 0:
        print(f"PASS: {filename}")
        return 0

    print(f"FAIL: {filename} ({errors} error{'s' if errors > 1 else ''})")
    return errors


def _lint_json(file):
    assert os.path.isfile(file)
    if file.endswith("/cfbs.json"):
        return lint_cfbs_json(file)
    assert file.endswith(".json")
    return lint_json(file)


def _discovery_file(filename):
    assert state
    assert state.mode == Mode.DISCOVER

    tree, lines, original_data = _parse_policy_file(filename)
    root_node = tree.root_node
    assert root_node.type == "source_file"
    errors = 0
    errors += _lint_policy_file(filename)
    # errors += _walk(filename, lines, root_node)
    state.end_of_file()
    return errors


# Interface: These are the functions we want to be called from outside
# They create State() and should not be called recursively inside lint.py


def lint_single_file(file, strict=True):
    return _lint_main([file], strict)


def lint_args(args, strict=True) -> int:
    return _lint_main(args, strict)


def lint_policy_file_snippet(
    filename,
    original_filename,
    original_line,
    snippet,
    prefix,
    strict=True,
):
    global state
    state = State()
    state.strict = strict
    state.mode = Mode.DISCOVER
    errors = _discovery_file(filename)
    if errors:
        return errors
    state.mode = Mode.LINT
    if snippet:
        return _lint_policy_file_snippet(
            filename=filename,
            original_filename=original_filename,
            original_line=original_line,
            snippet=snippet,
            prefix=prefix,
        )
    assert not snippet
    assert not original_filename
    assert not prefix
    assert not original_line
    return _lint_policy_file(filename=filename)


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
