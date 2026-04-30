ORG      := janip81
REGISTRY := ghcr.io
TAG      ?= latest

IMAGE_MCP := $(REGISTRY)/$(ORG)/claude-memory
IMAGE_UI  := $(REGISTRY)/$(ORG)/claude-memory-ui

.PHONY: dev build build-mcp build-ui push push-mcp push-ui tag-mcp tag-ui run test lint clean

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
test:
	@echo "TODO: add pytest"

# Lint
lint:
	@which ruff >/dev/null 2>&1 && ruff check src/ || echo "ruff not installed — skipping"

# Clean docker volumes
clean:
	docker compose down -v
