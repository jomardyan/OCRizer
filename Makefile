ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif
.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help install install-cli dev test lint check build package security clean
help: ## Show commands without installing or running OCR
	@"$(PYTHON)" tools/make-support.py help
install: ## Install editable OCR, GUI and development dependencies
	"$(PYTHON)" -m pip install -e ".[dev,gui]"
install-cli: ## Install editable OCR and development tools without the GUI
	"$(PYTHON)" -m pip install -e ".[dev]"
dev: ## Start the local Qt GUI (requires the GUI extra)
	"$(PYTHON)" -c "from ocrizer.gui.app import main; raise SystemExit(main())"
test: ## Run pytest; integration/GUI prerequisites are reported by tests
	"$(PYTHON)" -m pytest
lint: ## Run Ruff and mypy against the maintained sources
	"$(PYTHON)" -m ruff check src tests packaging
	"$(PYTHON)" -m mypy src
check: lint test ## Run lint/type checks before the test suite
build: ## Build the Python wheel and source archive
	"$(PYTHON)" -m build
package: build ## Build and validate distribution metadata with Twine
	"$(PYTHON)" -m twine check dist/*
security: ## Audit installed dependencies in the selected Python environment
	"$(PYTHON)" -m pip_audit --local --progress-spinner off
clean: ## Remove only owned distribution, test and linter output
	"$(PYTHON)" tools/make-support.py clean
