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
# Unit tests. Database tests skip without TEST_DATABASE_URL; use test-db for those.
# Needs: pip install -r requirements-mcp.txt -r requirements-dev.txt
PYTHON  ?= python3
TEST_PG := remnant-test-pg
test:
	$(PYTHON) -m pytest -q tests

# All tests incl. real-PostgreSQL ones, against a throwaway pgvector container
# (removed with its volume afterwards, also on failure).
test-db:
	@docker rm -fv $(TEST_PG) >/dev/null 2>&1 || true
	docker run -d --name $(TEST_PG) -e POSTGRES_USER=remnant -e POSTGRES_PASSWORD=test \
	  -e POSTGRES_DB=remnant -p 127.0.0.1:25499:5432 pgvector/pgvector:pg16 >/dev/null
	@for i in $$(seq 1 60); do docker exec $(TEST_PG) pg_isready -U remnant -q && break; sleep 1; done; sleep 1
	TEST_DATABASE_URL=postgresql://remnant:test@127.0.0.1:25499/remnant $(PYTHON) -m pytest -q tests; \
	  status=$$?; docker rm -fv $(TEST_PG) >/dev/null; exit $$status

# Lint
lint:
	@which ruff >/dev/null 2>&1 && ruff check src/ scripts/ plugin/ tests/ || echo "ruff not installed — skipping"

# Clean docker volumes
clean:
	docker compose down -v
