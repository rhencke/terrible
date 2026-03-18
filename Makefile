UV=uv run

.PHONY: help editable-install install-provider run-provider example-init example-apply example-fresh integration-test integration-test-verbose build-binary release

help:
	@echo "Makefile targets:"
	@echo "  editable-install       - install package in editable mode"
	@echo "  install-provider       - install provider into local terraform plugin registry"
	@echo "  run-provider           - run provider in dev mode (prints TF_REATTACH_PROVIDERS)"
	@echo "  example-init           - run 'terraform init' in the example project"
	@echo "  example-apply          - run 'terraform apply' in the example project (interactive)"
	@echo "  example-fresh          - reinstall provider, wipe state, and auto-apply the example"
	@echo "  integration-test       - build binary and run integration tests against localhost"
	@echo "  integration-test-verbose - same, with verbose output"
	@echo "  build-binary           - build a standalone pex binary (terraform-provider-terrible)"
	@echo "  release VERSION=x.y.z  - run tests, tag, push, and create GitHub release (notes from stdin)"

editable-install:
	$(UV) pip install -e .

install-provider:
	$(UV) ./bin/install-provider

run-provider:
	$(UV) ./bin/terraform-provider-terrible --dev

example-init:
	cd examples/terraform_provider && terraform init

example-apply:
	cd examples/terraform_provider && terraform apply

example-fresh: install-provider
	rm -f examples/terraform_provider/terraform.tfstate examples/terraform_provider/terraform.tfstate.backup terrible_state.json
	cd examples/terraform_provider && terraform apply -auto-approve

integration-test:
	TERRIBLE_INTEGRATION=1 $(UV) pytest tests/integration/ -v --timeout=120

integration-test-verbose:
	TERRIBLE_INTEGRATION=1 $(UV) pytest tests/integration/ -v -s --timeout=120

release:
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	scripts/release.sh "$(VERSION)"

build-binary:
	uv export --format requirements-txt --no-dev --no-hashes | grep -v '^-e' > /tmp/terrible-requirements.txt
	$(UV) pex -r /tmp/terrible-requirements.txt . \
	  -o terraform-provider-terrible \
	  -m terrible_provider.cli:main
	@echo "Binary built: ./terraform-provider-terrible"
