.PHONY: help lint test integration-test test-all install-hooks install-provider run-provider example-init example-apply example-fresh build-binary release

help:
	@echo "Makefile targets:"
	@echo "  lint                   - run ruff (lint + format check) and ty (type check)"
	@echo "  test                   - run unit tests with 100% coverage"
	@echo "  integration-test       - run integration tests against localhost"
	@echo "  test-all               - lint + unit + integration tests (same as pre-commit hook)"
	@echo "  install-hooks          - install git pre-commit hook"
	@echo "  install-provider       - install provider into local terraform plugin registry"
	@echo "  run-provider           - run provider in dev mode (prints TF_REATTACH_PROVIDERS)"
	@echo "  example-init           - run 'tofu init' in the example project"
	@echo "  example-apply          - run 'tofu apply' in the example project (interactive)"
	@echo "  example-fresh          - reinstall provider, wipe state, and auto-apply the example"
	@echo "  build-binary           - build a standalone pex binary (terraform-provider-terrible)"
	@echo "  release VERSION=x.y.z  - run tests, tag, push, and create GitHub release (notes from stdin)"

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check

test:
	uv run pytest tests/ --ignore=tests/integration -q

integration-test:
	TERRIBLE_INTEGRATION=1 uv run pytest tests/integration/ -q --no-cov --timeout=120

test-all: lint test integration-test

install-hooks:
	scripts/install-hooks.sh

install-provider:
	uv run install-provider

run-provider:
	uv run terraform-provider-terrible --dev

example-init:
	cd examples/terraform_provider && tofu init

example-apply:
	cd examples/terraform_provider && tofu apply

example-fresh: install-provider
	rm -f examples/terraform_provider/terraform.tfstate examples/terraform_provider/terraform.tfstate.backup examples/terraform_provider/terrible_state.json
	cd examples/terraform_provider && tofu apply -auto-approve

build-binary:
	uv export --format requirements-txt --no-dev --no-hashes | grep -v '^-e' > /tmp/terrible-requirements.txt
	uv run pex -r /tmp/terrible-requirements.txt . \
	  -o terraform-provider-terrible \
	  -m terrible_provider.cli:main
	@echo "Binary built: ./terraform-provider-terrible"

release:
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	scripts/release.sh "$(VERSION)"
