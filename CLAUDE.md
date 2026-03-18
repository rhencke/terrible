# CLAUDE.md

## Project Overview

**terrible** is a Terraform provider written in pure Python that exposes Ansible tasks as Terraform-managed resources. The goal is to let operators define and run Ansible tasks declaratively through Terraform without relying on Ansible's native inventory system — instead, target hosts are defined as `AnsibleHost` Terraform resources, keeping everything in Terraform state.

Intended resource model:
- `TerribleHost` — represents a target host (replaces Ansible inventory)
- `TerribleTask` (or similar) — represents an Ansible task or playbook execution as a resource
- Possibly data sources for Ansible facts or task outputs

The current implementation (`TerribleItem`) is a placeholder scaffold.

## Tech Stack

- Python 3.12+
- [`tf`](https://pypi.org/project/tf/) — Python Terraform provider framework (gRPC)
- `ansible>=13.3.0` — Ansible runtime
- `pytest` — testing
- `uv` — dependency management

## Common Commands

```bash
# Install package in editable mode
make editable-install        # or: pip install -e .

# Install provider into Terraform plugin directory
make install-provider

# Run provider in dev mode (prints TF_REATTACH_PROVIDERS)
make run-provider

# Run tests
pytest -q

# Run standalone example (no Terraform/Ansible needed)
python3 examples/run_example.py

# Terraform example workflow
make example-init
make example-apply
```

## Project Structure

```
terrible_provider/     # Main package
  cli.py               # CLI entrypoint (tf.runner.run_provider)
  provider.py          # TerribleProvider — manages state in terrible_state.json
  resources.py         # TerribleItem resource (CRUD, UUID-based IDs)
  install.py           # Provider installation utilities
scripts/
  install_provider.py  # Standalone install script with CLI flags
bin/
  terraform-provider-terrible  # Wrapper script for provider CLI
  install-provider             # Wrapper for install script
examples/
  run_example.py               # Runnable example requiring only Python
  ansible/                     # Example Ansible inventory + playbook
  terraform/                   # Conceptual HCL (illustrative)
  terraform_provider/          # Working Terraform config for local dev
tests/
  test_example.py              # Unit tests for example script
```

## Architecture Notes

- State is persisted locally in `terrible_state.json` (JSON)
- Provider full name: `local/terrible/terrible`
- The only resource is `TerribleItem` with schema: `id` (computed), `name` (required), `value` (optional)
- The `tf` package handles all gRPC communication with Terraform core

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on push to `main` and PRs:
1. Python 3.12 setup
2. `pip install -e .`
3. `pytest -q`

## Release Process

Always use `scripts/release.sh` (or `make release VERSION=x.y.z`) when cutting a release. Never tag or create a GitHub release manually. The script enforces a clean working tree, runs the test suite, creates the annotated tag, pushes it, and creates the GitHub release in one go.

```bash
# Pipe release notes into the script (or make target):
scripts/release.sh 0.4.0 <<'EOF'
New features in this release

## Features
- foo
- bar
EOF

# Equivalent via make:
make release VERSION=0.4.0   # reads notes interactively from stdin
```

Release notes must be written for every release — never leave them empty.

## Claude Instructions

- Do not add `Co-Authored-By: Claude` or any Claude/Anthropic attribution to commit messages.
- Always use `scripts/release.sh` when tagging a release — never tag or create GitHub releases manually.
