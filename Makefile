COMPOSE_FILE = compose.prod.example.yml
COMPOSE      = docker compose -f $(COMPOSE_FILE)
COMPOSE_ALL  = $(COMPOSE) --profile workers --profile planka
DJANGO       = $(COMPOSE) exec django


# =============================================================================
# Core stack
# =============================================================================

.PHONY: pull up down restart logs

pull:
	$(COMPOSE_ALL) pull

up:
	$(COMPOSE_ALL) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE_ALL) up -d --force-recreate

logs:
	$(COMPOSE_ALL) logs -f


# =============================================================================
# Django management
# =============================================================================

.PHONY: migrate shell createsuperuser

migrate:
	$(DJANGO) python manage.py migrate --noinput

shell:
	$(DJANGO) python manage.py shell

createsuperuser:
	$(DJANGO) python manage.py createsuperuser


# =============================================================================
# First-run bootstrap
# Runs in order: migrate → create_chief_editor → setup_planka_oidc
# After this, open /backend/settings/ and click "Set up Planka API key",
# then add OIDC_ENFORCED=true to .env and run: make restart
# =============================================================================

.PHONY: bootstrap planka-setup

bootstrap: migrate
	$(DJANGO) python manage.py create_chief_editor
	$(COMPOSE_ALL) exec django python manage.py setup_planka_oidc
	@echo ""
	@echo "Bootstrap complete. Next:"
	@echo "  1. Open /backend/settings/ → 'Set up Planka API key'"
	@echo "  2. Add OIDC_ENFORCED=true to .env"
	@echo "  3. Run: make restart"


# =============================================================================
# Environment and AWS setup
# =============================================================================

.PHONY: gen-env aws-setup aws-setup-test

gen-env:
	bash ops/gen-env.sh

# Provision S3, IAM users, SES identity, and SNS topic.
# Requires admin AWS credentials in the environment (not the service creds in .env).
# Usage: BUCKET=my-bucket DOMAIN=yourdomain.com make aws-setup
aws-setup:
	python3 ops/aws_setup.py \
	  --bucket "$(BUCKET)" \
	  --domain "$(DOMAIN)" \
	  --region "$(AWS_REGION)" \
	  --webhook-secret "$(WEBHOOK_SECRET)" \
	  $(if $(AWS_PROFILE),--profile "$(AWS_PROFILE)") \
	  $(if $(AWS_SUFFIX),--suffix "$(AWS_SUFFIX)") \
	  $(if $(AWS_SES_DOMAIN),--ses-domain "$(AWS_SES_DOMAIN)")

aws-setup-test:
	python3 -m pytest -q -o addopts='' ops/tests/test_aws_setup.py


# =============================================================================
# Status / health
# =============================================================================

.PHONY: ps

ps:
	$(COMPOSE_ALL) ps
