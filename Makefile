.PHONY: help install dev test lint type eval build-ui run doctor backup clean

help:
	@echo "AgentForge / ForgeOps — development tasks"
	@echo "  make install   install backend + frontend deps"
	@echo "  make dev       run API server with auto-reload"
	@echo "  make run       run API server (production defaults)"
	@echo "  make build-ui  build the React frontend into frontend/dist"
	@echo "  make test      run pytest"
	@echo "  make lint      ruff check"
	@echo "  make type      mypy"
	@echo "  make eval      run the ForgeOps agent eval suite (CI gate)"
	@echo "  make doctor    environment self-check"
	@echo "  make backup    online SQLite backup"

install:
	python3 -m pip install -e '.[dev]'
	cd frontend && npm ci --no-audit --no-fund

dev:
	agentforge serve --reload

run:
	agentforge serve

build-ui:
	cd frontend && npm run build

test:
	python3 -m pytest tests/ -q

lint:
	ruff check src tests examples/mcp_servers

type:
	mypy src/agentforge --ignore-missing-imports

eval:
	agentforge eval examples/suites/forgeops.yaml

doctor:
	agentforge doctor

backup:
	agentforge backup

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache data/backups/*
