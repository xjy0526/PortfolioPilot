SHELL := /bin/sh

DEMO_COMPOSE := docker compose --project-name portfoliopilot-demo -f docker-compose.demo.yml
DEMO_PORT ?= 8000
CODE_VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null)

export DEMO_PORT
export CODE_VERSION

.PHONY: demo demo-build demo-db demo-up demo-down demo-reset demo-smoke demo-logs

demo: demo-up demo-smoke
	@printf '\nPortfolioPilot deterministic demo is ready.\n'
	@printf 'Dashboard: http://localhost:%s\n' "$(DEMO_PORT)"
	@printf 'Swagger:   http://localhost:%s/docs\n' "$(DEMO_PORT)"
	@printf 'Mode: synthetic fixtures, mock model, no production data, read-only Web\n'

demo-build:
	$(DEMO_COMPOSE) build web

demo-db:
	$(DEMO_COMPOSE) up -d --wait postgres

demo-up: demo-build demo-db
	$(DEMO_COMPOSE) run --rm --no-deps migrate
	$(DEMO_COMPOSE) run --rm --no-deps seed
	$(DEMO_COMPOSE) up -d --wait web

demo-down:
	$(DEMO_COMPOSE) down --remove-orphans

demo-reset: demo-build demo-db
	$(DEMO_COMPOSE) run --rm --no-deps reset

demo-smoke:
	@published="$$( $(DEMO_COMPOSE) port web 8080 )"; \
		test "$$published" = "127.0.0.1:$(DEMO_PORT)" || { \
			printf 'Demo Web port is not published as expected: %s\n' "$$published" >&2; \
			exit 1; \
		}
	$(DEMO_COMPOSE) run --rm smoke

demo-logs:
	$(DEMO_COMPOSE) logs --follow --tail=200 web postgres
