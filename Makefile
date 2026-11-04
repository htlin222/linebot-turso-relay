# linebot-turso-relay — end-to-end setup / deploy / debug
#
# Override the reference names on the command line, e.g.
#   make deploy DB=mybot R2_BUCKET=mybot-attachments
#
# Secrets never live here — they are read from .env (gitignored) at push time;
# op://... values in .env are resolved through the 1Password CLI (`op read`).

# ---- Config (override on the CLI) -------------------------------------------
DB         ?= lizard
R2_BUCKET  ?= lizard-attachments
WRANGLER   ?= pnpm dlx wrangler
SECRETS    := LINE_CHANNEL_SECRET LINE_CHANNEL_ACCESS_TOKEN TURSO_DATABASE_URL TURSO_AUTH_TOKEN

.DEFAULT_GOAL := help

# ---- Meta -------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@echo "linebot-turso-relay — targets:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Vars:  DB=$(DB)  R2_BUCKET=$(R2_BUCKET)"
	@echo "Happy path:  make install env  (edit .env)  db secrets r2 deploy url"

.PHONY: all
all: install db secrets r2 deploy ## Full setup: install -> db -> secrets -> r2 -> deploy

# ---- Setup ------------------------------------------------------------------
.PHONY: install
install: ## Install dependencies (pnpm)
	pnpm install

.PHONY: env
env: ## Scaffold .env and .dev.vars from the examples (won't overwrite)
	@[ -f .env ] && echo ".env exists — leaving it" || (cp .env.example .env && echo "created .env — fill in the 4 secrets")
	@[ -f .dev.vars ] && echo ".dev.vars exists — leaving it" || (cp .dev.vars.example .dev.vars && echo "created .dev.vars — mirror the same 4 secrets")

.PHONY: check-env
check-env: ## Fail if .env is missing or has blank secrets
	@[ -f .env ] || { echo "ERROR: .env missing — run 'make env' and fill it in"; exit 1; }
	@missing=0; for k in $(SECRETS); do \
	  v=$$(grep "^$$k=" .env | cut -d= -f2-); \
	  [ -n "$$v" ] || { echo "ERROR: $$k is blank in .env"; missing=1; }; \
	done; [ $$missing -eq 0 ] || exit 1
	@echo "all 4 secrets present in .env"

# ---- Provision --------------------------------------------------------------
.PHONY: db
db: ## Create the Turso DB (if needed) and apply schema/*.sql migrations
	@turso db show $(DB) >/dev/null 2>&1 || turso db create $(DB)
	@for f in schema/*.sql; do echo "applying $$f"; turso db shell $(DB) < $$f; done
	@echo "schema applied to '$(DB)'"

.PHONY: r2
r2: ## Create the R2 attachments bucket (idempotent)
	@$(WRANGLER) r2 bucket create $(R2_BUCKET) 2>/dev/null || echo "bucket '$(R2_BUCKET)' already exists"

.PHONY: secrets
secrets: check-env ## Push the 4 secrets from .env into Cloudflare
	@for k in $(SECRETS); do \
	  v=$$(grep "^$$k=" .env | cut -d= -f2-); \
	  case "$$v" in op://*) v=$$(op read --no-newline "$$v") || exit 1;; esac; \
	  printf '%s' "$$v" | $(WRANGLER) secret put $$k; \
	done
	@echo "secrets pushed to Cloudflare"

# ---- Deploy / run -----------------------------------------------------------
.PHONY: deploy
deploy: ## Deploy the worker to Cloudflare
	$(WRANGLER) deploy

.PHONY: dev
dev: ## Run the worker locally (http://localhost:8787), reads .dev.vars
	pnpm dev

.PHONY: dev-test
dev-test: ## Send a signed empty-events webhook to localhost:8787 (expect 200)
	@SECRET=$$(grep '^LINE_CHANNEL_SECRET=' .env | cut -d= -f2-); \
	case "$$SECRET" in op://*) SECRET=$$(op read --no-newline "$$SECRET") || exit 1;; esac; \
	BODY='{"destination":"U0","events":[]}'; \
	SIG=$$(printf '%s' "$$BODY" | openssl dgst -sha256 -hmac "$$SECRET" -binary | base64); \
	curl -i -X POST http://localhost:8787/webhook \
	  -H "x-line-signature: $$SIG" -H 'content-type: application/json' -d "$$BODY"

.PHONY: url
url: ## Print the webhook URL to paste into the LINE Console
	@$(WRANGLER) deployments list 2>/dev/null | head -20 || true
	@echo
	@echo "Webhook URL = https://$$($(WRANGLER) whoami >/dev/null 2>&1; echo '<worker>.<your-cf-subdomain>')/webhook"
	@echo "(Exact URL is printed by 'make deploy'. Paste it + '/webhook' into LINE Console -> Messaging API.)"

# ---- Debug ------------------------------------------------------------------
.PHONY: tail
tail: ## Live-tail worker logs
	$(WRANGLER) tail

.PHONY: typecheck
typecheck: ## TypeScript typecheck (no emit)
	pnpm typecheck

.PHONY: shell
shell: ## Open a Turso SQL shell against the DB
	turso db shell $(DB)
