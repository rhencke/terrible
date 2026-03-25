unexport VIRTUAL_ENV

.PHONY: help check test integration-test registry-test test-all lint typecheck format install-hooks install-provider run-provider example-init example-apply example-fresh build-binary docs release

help:
	@echo "Makefile targets:"
	@echo "  check                  - lint + typecheck + test-all + docs check (used by CI and pre-commit)"
	@echo "  test                   - run unit tests with 100% coverage"
	@echo "  integration-test       - run integration tests against localhost (local venv binary, dev mode)"
	@echo "  registry-test          - run integration tests pulling provider from registry.terraform.io"
	@echo "  test-all               - run unit + integration tests"
	@echo "  install-hooks          - install git pre-commit hook"
	@echo "  install-provider       - install provider into local terraform plugin registry"
	@echo "  run-provider           - run provider in dev mode (prints TF_REATTACH_PROVIDERS)"
	@echo "  example-init           - run 'tofu init' in the example project"
	@echo "  example-apply          - run 'tofu apply' in the example project (interactive)"
	@echo "  example-fresh          - reinstall provider, wipe state, and auto-apply the example"
	@echo "  build-binary           - build a standalone PyInstaller binary (terraform-provider-terrible)"
	@echo "  lint                   - check code with ruff (no fixes)"
	@echo "  typecheck              - check types with ty"
	@echo "  format                 - auto-format code with ruff (explicit action)"
	@echo "  docs                   - generate Terraform Registry docs into docs/ (requires install-provider)"
	@echo "  release VERSION=x.y.z  - run tests, tag, push, and create GitHub release (notes from stdin)"

check: lint typecheck test-all
	scripts/check-docs.sh

lint:
	uv run ruff check terrible_provider/ tests/
	uv run ruff format --check terrible_provider/ tests/

typecheck:
	uv run ty check terrible_provider/

format:
	uv run ruff format terrible_provider/ tests/
	uv run ruff check --fix terrible_provider/ tests/

test:
	uv run pytest tests/ --ignore=tests/integration -q

integration-test:
	TERRIBLE_INTEGRATION=1 TERRIBLE_DEV_MODE=1 uv run pytest tests/integration/ -q --no-cov --timeout=120

registry-test:
	TERRIBLE_INTEGRATION=1 uv run pytest tests/integration/ -q --no-cov --timeout=300

test-all: test integration-test

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
	uv run pyinstaller terrible.spec --distpath dist --workpath build/pyinstaller --noconfirm
	@echo "Binary built: ./dist/terraform-provider-terrible"

docs: install-provider
	scripts/generate-docs.sh

release:
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	scripts/release.sh "$(VERSION)"
