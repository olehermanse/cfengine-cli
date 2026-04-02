.PHONY: check format lint install

default: check

format:
	uv tool run black .
	prettier . --write

lint:
	uv tool run black --check .
	uv tool run flake8 src/ --ignore=E203,W503,E722,E731 --max-complexity=100 --max-line-length=160
	uv tool run pyflakes src/
	uv tool run pyright src/

check: format lint install
	uv tool run black --check .
	uv tool run flake8 src/ --ignore=E203,W503,E722,E731 --max-complexity=100 --max-line-length=160
	uv tool run pyflakes src/
	uv tool run pyright src/
	uv run pytest
	bash tests/run-lint-tests.sh
	bash tests/run-format-tests.sh
	bash tests/run-shell-tests.sh

install:
	git fetch --all --tags
	pipx install --force --editable .
