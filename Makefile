.PHONY: default format lint install check

default: check

format:
	uv tool run black .
	prettier . --write

lint:
	uv tool run black --check .
	uv tool run flake8 src/ --ignore=E203,W503,E722,E731 --max-complexity=100 --max-line-length=160
	uv tool run pyflakes src/
	uv tool run pyright src/

install:
	pipx install --force --editable .

check: format lint install
	uv run pytest
	bash tests/run-lint-tests.sh
	bash tests/run-format-tests.sh
	bash tests/run-shell-tests.sh
