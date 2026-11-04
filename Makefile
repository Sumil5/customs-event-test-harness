# customs-event-test-harness - common tasks.  `make` lists them.
# Works with the GNU Make 3.81 that ships with macOS.

COMPOSE ?= docker compose
PERSISTENCE_FAULTS ?= rounding,truncate_description,ascii_names,drop_millis,skip_last_item,drop=0.02
ASSESSMENT_FAULTS ?= lane_boundary,delay=0.002:14000
SERVICES = intake assessment persistence

.DEFAULT_GOAL := help
.PHONY: help install up down ps logs ui lint format test e2e e2e-docker demo faults-on faults-off \
        test-faults demo-faults samples clean

help: ## List the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the local virtualenv with uv
	uv sync

up: ## Start Kafka, Oracle, moto and the three services; waits until healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop everything and delete all data
	$(COMPOSE) --profile test --profile tools down -v --remove-orphans

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow the services' structured logs
	$(COMPOSE) logs -f $(SERVICES)

ui: ## Kafka UI on http://localhost:8080
	$(COMPOSE) --profile tools up -d kafka-ui

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	uv run ruff format .
	uv run ruff check --fix .

test: ## Unit tests (no Docker needed)
	uv run pytest tests/unit

e2e: ## End-to-end tests from your machine against the running stack
	uv run pytest tests/e2e

e2e-docker: ## End-to-end tests inside the harness container (what CI runs)
	$(COMPOSE) --profile test run --rm --build harness

demo: ## Publish 2,000 messages, then reconcile and trace them
	uv run harness run -n 2000 --rate 200

faults-on: ## Restart the services with known defects switched on
	PERSISTENCE_FAULTS="$(PERSISTENCE_FAULTS)" ASSESSMENT_FAULTS="$(ASSESSMENT_FAULTS)" \
		$(COMPOSE) up -d --wait $(SERVICES)

faults-off: ## Restart the services without defects
	$(COMPOSE) up -d --wait $(SERVICES)

test-faults: ## Prove every injected defect is caught (needs faults-on)
	PERSISTENCE_FAULTS="$(PERSISTENCE_FAULTS)" ASSESSMENT_FAULTS="$(ASSESSMENT_FAULTS)" \
		uv run pytest tests/e2e/test_injected_faults.py -v

demo-faults: faults-on ## Run the demo against the faulty services, then restore them
	-uv run harness run -n 1000 --rate 200
	$(MAKE) faults-off

samples: ## Regenerate the example XML payloads in samples/
	for s in standard unicode_text lexical_variants minimal invalid_invoice_total malformed_xml; do \
		uv run harness generate -s $$s -n 1 -o samples --seed 7 || exit 1; done

clean: ## Delete generated reports
	rm -rf reports/run-* reports/window-* reports/LATEST reports/*.xml reports/*.log
