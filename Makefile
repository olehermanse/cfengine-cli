.PHONY: default format lint install check venv

default: check

venv:
	uv venv --clear
	uv sync

format: venv
	uv tool run black . --target-version py310
	prettier . --write

lint: venv
	uv tool run black --check . --fast
	uv tool run flake8 src/ --ignore=E203,W503,E722,E731 --max-complexity=100 --max-line-length=160
	uv tool run pyflakes src/
	uv tool run pyright src/

install:
	pipx install --force --editable .

check: venv format lint
	uv run pytest
	uv run bash tests/run-lint-tests.sh
	uv run bash tests/run-format-tests.sh
	uv run bash tests/run-shell-tests.sh
