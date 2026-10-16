ORG      := janip81
REGISTRY := ghcr.io
TAG      ?= latest

IMAGE_MCP := $(REGISTRY)/$(ORG)/remnant
IMAGE_UI  := $(REGISTRY)/$(ORG)/remnant-ui

.PHONY: dev build build-mcp build-ui push push-mcp push-ui tag-mcp tag-ui run test test-db lint clean

# Start full local stack (postgres + MCP app)
dev:
	docker compose up --build

# Build both images
build: build-mcp build-ui

# Build MCP server image
build-mcp:
	docker build --no-cache -f Dockerfile.mcp -t $(IMAGE_MCP):$(TAG) .

# Build UI image
build-ui:
	docker build --no-cache -f Dockerfile.ui -t $(IMAGE_UI):$(TAG) .

# Push both images
push: push-mcp push-ui

# Push MCP server image
push-mcp:
	docker push $(IMAGE_MCP):$(TAG)

# Push UI image
push-ui:
	docker push $(IMAGE_UI):$(TAG)

# Tag and push a versioned MCP release: make tag-mcp TAG=v0.2.0
tag-mcp:
	@if [ -z "$(TAG)" ] || [ "$(TAG)" = "latest" ]; then echo "TAG not set — use: make tag-mcp TAG=v0.2.0"; exit 1; fi
	docker tag $(IMAGE_MCP):latest $(IMAGE_MCP):$(TAG)
	docker push $(IMAGE_MCP):$(TAG)

# Tag and push a versioned UI release: make tag-ui TAG=v0.2.0
tag-ui:
	@if [ -z "$(TAG)" ] || [ "$(TAG)" = "latest" ]; then echo "TAG not set — use: make tag-ui TAG=v0.2.0"; exit 1; fi
	docker tag $(IMAGE_UI):latest $(IMAGE_UI):$(TAG)
	docker push $(IMAGE_UI):$(TAG)

# Run MCP server only (needs postgres already running)
run:
	cd src && uvicorn main:app --reload --host 0.0.0.0 --port 8080

# Run tests
# Unit tests only: TEST_DATABASE_URL is cleared, because the DB tests drop and
# recreate tables in whatever database it points to. Use test-db for those.
# Needs: pip install -r requirements-mcp.txt -r requirements-dev.txt
PYTHON  ?= python3
TEST_PG := remnant-test-pg
test:
	env -u TEST_DATABASE_URL $(PYTHON) -m pytest -q tests

# All tests incl. real-PostgreSQL ones, against a throwaway pgvector container.
# One shell with a trap: the container and its volume are removed on success,
# test failure, startup failure, readiness timeout and interruption.
test-db:
	@set -u; \
	cleanup() { docker rm -fv $(TEST_PG) >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT; trap 'exit 130' INT TERM; cleanup; \
	docker run -d --name $(TEST_PG) -e POSTGRES_USER=remnant -e POSTGRES_PASSWORD=test \
	  -e POSTGRES_DB=remnant -p 127.0.0.1:25499:5432 pgvector/pgvector:pg16 >/dev/null || exit 1; \
	ready=0; for i in $$(seq 1 60); do \
	  if docker exec $(TEST_PG) pg_isready -U remnant -q 2>/dev/null; then ready=1; break; fi; sleep 1; done; \
	[ $$ready = 1 ] || { echo "test database not ready after 60 s" >&2; exit 1; }; sleep 1; \
	TEST_DATABASE_URL=postgresql://remnant:test@127.0.0.1:25499/remnant $(PYTHON) -m pytest -q tests

lint:
	@which ruff >/dev/null 2>&1 && ruff check src/ scripts/ plugin/ tests/ || echo "ruff not installed — skipping"

# Clean docker volumes
clean:
	docker compose down -v
