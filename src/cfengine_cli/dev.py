import os
from cfengine_cli.masterfiles.generate_release_information import (
    generate_release_information_impl,
)
from cfengine_cli.utils import UserError
from cfengine_cli.deptool import (
    update_dependency_tables as _update_dependency_tables,
    print_release_dependency_tables,
)
from cfengine_cli.docs import update_docs, check_docs


def generate_release_information_command(
    omit_download=False, check=False, min_version=None
):
    generate_release_information_impl(omit_download, check, min_version)
    return 0


def _continue_prompt() -> bool:
    answer = None
    while answer not in ("y", "n", "yes", "no"):
        print("Continue? (Y/n): ", end="")
        answer = input().strip().lower()
    return answer in ("y", "yes")


def _expect_repo(repo) -> bool:
    cwd = os.getcwd()
    if cwd.endswith(repo):
        return True
    print(f"Note: This command is intended to be run in the {repo} repo")
    print(f"      https://github.com/cfengine/{repo}")
    answer = _continue_prompt()
    return answer


def update_dependency_tables() -> int:
    answer = _expect_repo("buildscripts")
    if answer:
        return _update_dependency_tables()
    return 1


def print_dependency_tables(args) -> int:
    versions = args.versions
    answer = _expect_repo("buildscripts")
    if answer:
        return print_release_dependency_tables(versions)
    return 1


def format_docs() -> int:
    answer = _expect_repo("documentation")
    if answer:
        return update_docs()
    return 1


def lint_docs() -> int:
    answer = _expect_repo("documentation")
    if answer:
        return check_docs()
    return 1


def generate_release_information(
    omit_download=False, check=False, min_version=None
) -> int:
    answer = _expect_repo("release-information")
    if answer:
        generate_release_information_command(omit_download, check, min_version)
        return 0
    return 1


def dispatch_dev_subcommand(subcommand, args) -> int:
    if subcommand == "update-dependency-tables":
        return update_dependency_tables()
    if subcommand == "print-dependency-tables":
        return print_dependency_tables(args)
    if subcommand == "format-docs":
        return format_docs()
    if subcommand == "lint-docs":
        return lint_docs()
    if subcommand == "generate-release-information":
        return generate_release_information(
            args.omit_download, args.check_against_git, args.minimum_version
        )

    raise UserError("Invalid cfengine dev subcommand - " + subcommand)
