REPO     := claude-memory
ORG      := janip81
REGISTRY := ghcr.io
IMAGE    := $(REGISTRY)/$(ORG)/$(REPO)
TAG      ?= latest

.PHONY: dev build push tag run test lint clean

# Start full local stack (postgres + app)
dev:
	docker compose up --build

# Build image
build:
	docker build -t $(IMAGE):$(TAG) .

# Push image
push:
	docker push $(IMAGE):$(TAG)

# Tag a release: make tag TAG=v0.1.0
tag:
	@if [ -z "$(TAG)" ] || [ "$(TAG)" = "latest" ]; then echo "TAG not set — use: make tag TAG=v0.1.0"; exit 1; fi
	docker tag $(IMAGE):latest $(IMAGE):$(TAG)
	docker push $(IMAGE):$(TAG)

# Run app only (needs postgres already running)
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
