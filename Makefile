# cairosvg needs libcairo. On macOS, Homebrew installs it OUTSIDE the default
# dyld search path AND `uv run` drops DYLD_* (SIP strips it across the exec
# chain), so the docs render fails under `uv run`. Work around it by calling the
# venv python directly with DYLD_FALLBACK_LIBRARY_PATH pointed at Homebrew's
# cairo. On Linux (incl. CI), apt's libcairo2 is on a standard path and
# `uv run python` works. Run `uv sync --group docs` first so `.venv` exists.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  CAIRO_PREFIX := $(shell brew --prefix cairo 2>/dev/null)
  DOCS_PY := DYLD_FALLBACK_LIBRARY_PATH=$(CAIRO_PREFIX)/lib ./.venv/bin/python
else
  DOCS_PY := uv run python
endif

.PHONY: help docs-diagrams docs-build docs-serve docs-check docs-wiki

help:
	@echo "docs-diagrams  render diagram masters -> SVG (site) + PNG (committed)"
	@echo "docs-build     render diagrams + site, then mkdocs --strict"
	@echo "docs-serve     render diagrams + site, then mkdocs serve"
	@echo "docs-check     render diagrams + check_docs + mkdocs --strict"
	@echo "docs-wiki      render wiki + push_wiki --check (no network)"

docs-diagrams:
	$(DOCS_PY) -m scripts.docs.render_diagrams

docs-build:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.build_docs --site
	uv run mkdocs build --strict

docs-serve:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.build_docs --site
	uv run mkdocs serve

docs-check:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.check_docs
	uv run mkdocs build --strict

docs-wiki:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.build_docs --wiki
	$(DOCS_PY) -m scripts.docs.push_wiki --check
