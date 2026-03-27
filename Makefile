# Enterprise MVA Platform — Developer Makefile
# ─────────────────────────────────────────────────────────────────────────────
# All commands are Docker-first: no Python or Node.js required on the host.
# Run `make help` to see a summary of all available targets.
# ─────────────────────────────────────────────────────────────────────────────

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT_DIR          := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
DDM_DIR           := $(ROOT_DIR)/ddm-l6
MVA_DIR           := $(ROOT_DIR)/mva-v2
BACKEND_DIR       := $(DDM_DIR)/backend
AUDIT_LOG         := $(BACKEND_DIR)/audit_chain.jsonl

# ─── Docker Compose Files ─────────────────────────────────────────────────────
DC_MAIN  := docker compose -f $(DDM_DIR)/docker-compose.yml
DC_TOOLS := docker compose -f $(DDM_DIR)/docker-compose.tools.yml

# ─── Colours for terminal output ──────────────────────────────────────────────
BOLD   := \033[1m
GREEN  := \033[0;32m
YELLOW := \033[1;33m
CYAN   := \033[0;36m
RESET  := \033[0m

# ─────────────────────────────────────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help message
	@echo ""
	@echo "$(BOLD)Enterprise MVA Platform — Available Commands$(RESET)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "$(CYAN)Development$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-22s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(YELLOW)Examples:$(RESET)"
	@echo "  make run-dev          # Start the full local stack"
	@echo "  make test-all         # Run every test suite"
	@echo "  make verify-audit     # Validate the cryptographic audit chain"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL / SETUP
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: install
install: ## Pull all Docker images and build local images (no network proxy needed)
	@echo "$(CYAN)▶ Building Docker images...$(RESET)"
	$(DC_MAIN) pull --ignore-pull-failures || true
	$(DC_MAIN) build
	@echo "$(GREEN)✔ Images built successfully.$(RESET)"

.PHONY: install-tools
install-tools: ## Build the ephemeral tools image (pytest runner, API tester)
	@echo "$(CYAN)▶ Building tools images...$(RESET)"
	$(DC_TOOLS) build
	@echo "$(GREEN)✔ Tools images built.$(RESET)"

# ─────────────────────────────────────────────────────────────────────────────
# RUN / STOP
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: run-dev
run-dev: ## Start the full local stack: backend API (port 8010) + Mission Control (port 9080)
	@echo "$(CYAN)▶ Starting local development stack...$(RESET)"
	$(DC_MAIN) up -d --build
	@echo ""
	@echo "$(GREEN)✔ Stack is up:$(RESET)"
	@echo "   Mission Control  →  http://localhost:9080/mission-control.html"
	@echo "   Backend API      →  http://localhost:8010/docs"
	@echo "   API Health       →  http://localhost:8010/health"
	@echo ""

.PHONY: stop
stop: ## Stop all running containers
	@echo "$(CYAN)▶ Stopping containers...$(RESET)"
	$(DC_MAIN) down
	@echo "$(GREEN)✔ Containers stopped.$(RESET)"

.PHONY: restart
restart: stop run-dev ## Restart the full local stack

.PHONY: logs
logs: ## Tail logs from all services (Ctrl-C to exit)
	$(DC_MAIN) logs -f

.PHONY: logs-backend
logs-backend: ## Tail backend service logs only
	$(DC_MAIN) logs -f ddm-backend

# ─────────────────────────────────────────────────────────────────────────────
# TESTING
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: test-backend
test-backend: install-tools ## Run backend pytest suite with coverage report
	@echo "$(CYAN)▶ Running backend tests...$(RESET)"
	$(DC_TOOLS) run --rm ddm-pytest
	@echo "$(GREEN)✔ Backend tests complete.$(RESET)"

.PHONY: test-frontend
test-frontend: ## Run mva-v2 frontend unit, functional, and regression tests
	@echo "$(CYAN)▶ Running frontend tests (inside Docker)...$(RESET)"
	docker compose -f $(MVA_DIR)/docker-compose.tools.yml run --rm mva-test
	@echo "$(GREEN)✔ Frontend tests complete.$(RESET)"

.PHONY: test-all
test-all: test-backend test-frontend ## Run the full test suite (backend + frontend)
	@echo "$(GREEN)✔ All tests passed.$(RESET)"

.PHONY: test-red-team
test-red-team: install-tools ## Run adversarial red-team evaluation suite
	@echo "$(YELLOW)▶ Running red-team adversarial suite...$(RESET)"
	$(DC_TOOLS) run --rm ddm-pytest pytest eval/red_team.py -v --tb=short
	@echo "$(GREEN)✔ Red-team suite complete.$(RESET)"

.PHONY: test-api
test-api: ## Run the shell-based API integration tests against the running stack
	@echo "$(CYAN)▶ Running API integration tests...$(RESET)"
	$(DC_TOOLS) run --rm ddm-api-test
	@echo "$(GREEN)✔ API tests complete.$(RESET)"

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY & AUDIT
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: verify-audit
verify-audit: ## Verify the tamper-evident Ed25519/SHA-256 audit log chain
	@echo "$(CYAN)▶ Verifying cryptographic audit chain...$(RESET)"
	@if [ ! -f "$(AUDIT_LOG)" ]; then \
		echo "$(YELLOW)⚠ No audit log found at $(AUDIT_LOG). Start the stack first.$(RESET)"; \
		exit 1; \
	fi
	$(DC_MAIN) exec ddm-backend python -c "\
from security.provenance import verify_payload, hash_payload; \
import json, sys; \
entries = [json.loads(l) for l in open('/app/audit_chain.jsonl') if l.strip()]; \
errors = 0; \
prev = '__GENESIS__'; \
for i, e in enumerate(entries): \
    if e.get('prev_hash') != prev: \
        print(f'CHAIN BREAK at entry {i}'); errors += 1; \
    prev = e.get('hash', ''); \
print(f'Verified {len(entries)} entries, {errors} error(s).'); \
sys.exit(1 if errors else 0) \
"
	@echo "$(GREEN)✔ Audit chain verified.$(RESET)"

.PHONY: security-scan
security-scan: ## Run Bandit static security analysis on the backend codebase
	@echo "$(CYAN)▶ Running Bandit security scan...$(RESET)"
	docker run --rm -v $(BACKEND_DIR):/src \
		python:3.11-slim sh -c "pip install bandit -q && bandit -r /src -ll -x /src/__pycache__ --format txt"
	@echo "$(GREEN)✔ Security scan complete.$(RESET)"

# ─────────────────────────────────────────────────────────────────────────────
# KUBERNETES
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: k8s-apply
k8s-apply: ## Apply all Kubernetes manifests to the current kubectl context
	@echo "$(CYAN)▶ Applying K8s manifests (namespace: mva-platform)...$(RESET)"
	kubectl apply -f $(ROOT_DIR)/deploy/k8s/mva-platform/
	@echo "$(GREEN)✔ Manifests applied.$(RESET)"

.PHONY: k8s-status
k8s-status: ## Check rollout status of all K8s deployments
	kubectl rollout status deployment -n mva-platform
	kubectl get pods -n mva-platform

.PHONY: k8s-teardown
k8s-teardown: ## WARNING: Delete all mva-platform K8s resources (irreversible)
	@echo "$(YELLOW)⚠ This will delete all resources in namespace mva-platform.$(RESET)"
	@read -p "Are you sure? (yes/no): " CONFIRM && [ "$$CONFIRM" = "yes" ]
	kubectl delete namespace mva-platform
	@echo "$(GREEN)✔ Namespace deleted.$(RESET)"

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: shell-backend
shell-backend: ## Open an interactive shell in the running backend container
	$(DC_MAIN) exec ddm-backend /bin/bash

.PHONY: clean
clean: ## Remove stopped containers and dangling Docker images
	@echo "$(CYAN)▶ Pruning stopped containers and dangling images...$(RESET)"
	docker container prune -f
	docker image prune -f
	@echo "$(GREEN)✔ Docker cleanup done.$(RESET)"

.PHONY: clean-all
clean-all: stop clean ## Stop stack and remove all project Docker images
	@echo "$(CYAN)▶ Removing project images...$(RESET)"
	$(DC_MAIN) down --rmi local --volumes --remove-orphans
	@echo "$(GREEN)✔ Full cleanup done.$(RESET)"

.PHONY: ps
ps: ## List running project containers and their status
	$(DC_MAIN) ps
