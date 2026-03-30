# Tests

There are 4 types of tests: unit, shell, format, and lint.
All test scripts should be run from the repository root.

## Unit tests

Python unit tests using pytest, located in `tests/unit/` as `test_*.py` files.

```bash
uv run pytest
```

To add a new test, create a `tests/unit/test_<name>.py` file with test functions prefixed `test_`.

## Shell tests

Bash scripts in `tests/shell/` that exercise the CLI end-to-end.
Each script is a standalone test that runs commands and relies on `set -e` to fail on errors.

```bash
bash tests/run-shell-tests.sh
```

To add a new test, create a `tests/shell/<NNN>-<name>.sh` file.

## Format tests

Tests for `cfengine format`, located in `tests/format/`.
Each test is a pair of files:

- `<name>.input.cf` - the unformatted input
- `<name>.expected.cf` - the expected formatted output

The test runner formats each input file and diffs against the expected output.

```bash
bash tests/run-format-tests.sh
```

To add a new test, create both an `.input.cf` and `.expected.cf` file with matching names.

## Lint tests

Tests for `cfengine lint`, located in `tests/lint/`.
Each test is a `.cf` file.
The file extension determines the expected outcome:

- `<name>.cf` - expected to pass linting (exit code 0)
- `<name>.x.cf` - expected to fail linting (non-zero exit code), must have a corresponding `<name>.output.txt` file containing the expected error output

```bash
bash tests/run-lint-tests.sh
```

To add a passing test, create a `tests/lint/<NNN>_<name>.cf` file.
To add a failing test, create both a `tests/lint/<NNN>_<name>.x.cf` file and a `tests/lint/<NNN>_<name>.output.txt` file with the expected lint output.
The output file must match exactly, including the relative file path (e.g. `tests/lint/<NNN>_<name>.x.cf`).
