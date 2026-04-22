import sys
import os
import re
import json
from cfengine_cli.profile import profile_cfengine, generate_callstack
from cfengine_cli.dev import dispatch_dev_subcommand
from cfengine_cli.lint import lint_args, PolicySyntaxError
from cfengine_cli.shell import user_command
from cfengine_cli.paths import bin
from cfengine_cli.version import cfengine_cli_version_string
from cfengine_cli.format import (
    format_policy_file,
    format_json_file,
    format_policy_fin_fout,
)
from cfengine_cli.utils import UserError
from cfbs.utils import find
from cfbs.commands import build_command
from cf_remote.commands import deploy as deploy_command


def _require_cfagent():
    if not os.path.exists(bin("cf-agent")):
        raise UserError(f"cf-agent not found at {bin('cf-agent')}")


def _require_cfhub():
    if not os.path.exists(bin("cf-hub")):
        raise UserError(f"cf-hub not found at {bin('cf-hub')}")


def help() -> int:
    print("Example usage:")
    print("cfengine run")
    return 0


def version() -> int:
    print(cfengine_cli_version_string())
    return 0


def build() -> int:
    r = build_command()
    return r


def deploy() -> int:
    r = deploy_command(None, None)
    return r


def _format_filename(filename: str, line_length: int, check: bool) -> int:
    """Format a single file.

    Raises PolicySyntaxError for .cf files with syntax errors."""
    if filename.startswith("./."):
        return 0
    if filename.endswith(".json"):
        return format_json_file(filename, check)
    if filename.endswith(".cf"):
        return format_policy_file(filename, line_length, check)
    raise UserError(f"Unrecognized file format: {filename}")


def _format_dirname(directory: str, line_length: int, check: bool) -> int:
    ret = 0
    for filename in find(directory, extension=".json"):
        ret |= _format_filename(filename, line_length, check)
    for filename in find(directory, extension=".cf"):
        if filename.endswith(".x.cf"):
            continue
        ret |= _format_filename(filename, line_length, check)
    return ret


def format(names, line_length, check) -> int:
    try:
        return _format_inner(names, line_length, check)
    except PolicySyntaxError as e:
        print(f"Error: {e}")
        return 1


def _format_inner(names, line_length, check) -> int:
    if not names:
        return _format_dirname(".", line_length, check)
    if len(names) == 1 and names[0] == "-":
        # Special case, format policy file from stdin to stdout
        return format_policy_fin_fout(sys.stdin, sys.stdout, line_length, check)

    ret = 0
    for name in names:
        if name == "-":
            raise UserError(
                "The - argument has a special meaning and cannot be combined with other paths"
            )
        if not os.path.exists(name):
            raise UserError(f"{name} does not exist")
        if os.path.isfile(name):
            ret |= _format_filename(name, line_length, check)
            continue
        if os.path.isdir(name):
            ret |= _format_dirname(name, line_length, check)
            continue
    if check:
        return ret
    return 0


def _lint(files, strict) -> int:
    if not files:
        return lint_args(["."], strict)
    return lint_args(files, strict)


def lint(files, strict) -> int:
    errors = _lint(files, strict)
    if errors == 0:
        print("Success, no errors found.")
    elif errors == 1:
        print("Failure, 1 error in total.")
    else:
        print(f"Failure, {errors} errors in total.")
    return errors


def report() -> int:
    _require_cfhub()
    _require_cfagent()
    user_command(f"{bin('cf-agent')} -KIf update.cf && {bin('cf-agent')} -KI")
    user_command(f"{bin('cf-hub')} --query rebase -H 127.0.0.1")
    user_command(f"{bin('cf-hub')} --query delta -H 127.0.0.1")
    return 0


def run() -> int:
    _require_cfagent()
    user_command(f"{bin('cf-agent')} -KIf update.cf && {bin('cf-agent')} -KI")
    return 0


def dev(subcommand, args) -> int:
    return dispatch_dev_subcommand(subcommand, args)


def profile(args) -> int:
    data = None
    with open(args.profiling_input, "r") as f:
        m = re.search(r"\[[.\s\S]*\]", f.read())
        if m is not None:
            data = json.loads(m.group(0))

    if data is not None and any([args.bundles, args.functions, args.promises]):
        profile_cfengine(data, args)

    if args.flamegraph:
        generate_callstack(data, args.flamegraph)

    return 0
