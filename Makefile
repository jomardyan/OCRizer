# Requires Python 3.11+ and the OCR system tools documented in README.md.
PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help install dev test lint build clean package security
help:
	@echo "make install dev test lint build package security clean"
install:
	$(PYTHON) -m pip install -e '.[dev,gui]'
dev:
	$(PYTHON) -c "from ocrizer.gui.app import main; raise SystemExit(main())"
test:
	$(PYTHON) -m pytest
lint:
	$(PYTHON) -m ruff check src tests packaging
	$(PYTHON) -m mypy src
build package:
	$(PYTHON) -m build
security:
	$(PYTHON) -m pip_audit --local
clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('build', 'dist', '.pytest_cache', '.mypy_cache', '.ruff_cache')]"
