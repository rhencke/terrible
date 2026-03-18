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
- `uv` — **the only Python tool used**. No pip, poetry, pipenv, or other package managers. All commands go through `uv run`.

## Common Commands

```bash
# Run unit tests (100% coverage required)
make test

# Run integration tests against localhost
make integration-test

# Run all tests (unit + integration — same as pre-commit hook)
make test-all

# Install git pre-commit hook
make install-hooks

# Install provider into Terraform plugin directory
make install-provider

# Run provider in dev mode (prints TF_REATTACH_PROVIDERS)
make run-provider

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

### Pre-release checklist

Before cutting any release:

1. All planned milestone issues are closed.
2. `uv run pytest -q` passes with 100% coverage locally.
3. CI is green on `main` (check with `gh run list --limit 3`).
4. Working tree is clean (`git status` shows nothing).
5. `pyproject.toml` version matches the release version.

### Cutting a release (pre-0.5.0 — no registry publishing yet)

Use `scripts/release.sh` or the `make release` target. Never tag or create
GitHub releases manually — the script enforces the checklist, runs tests,
creates the annotated tag, pushes it, and creates the GitHub release.

```bash
scripts/release.sh 0.4.0 <<'EOF'
Short title line (becomes the release title)

## New features
- bullet points here

## Bug fixes / internals
- etc.
EOF

# Equivalent:
make release VERSION=0.4.0   # reads notes from stdin
```

Release notes must be non-empty and follow the format above. The first
non-empty line becomes the release title on GitHub.

After pushing, verify CI passes:

```bash
gh run list --limit 3
gh run watch <run-id> --exit-status
```

### Cutting a release (0.5.0+ — Terraform Registry publishing)

**Nothing publishes until everything passes on every platform.**

The `release.yml` workflow enforces this with two sequential stages:

**Stage 1 — validate (all must pass before stage 2 runs):**
- Unit tests (`uv run pytest -q`, 100% coverage) on all 5 platform runners
- Integration tests on all 5 platform runners
- PyInstaller binary builds on all 5 platform runners

**Stage 2 — publish (runs only if stage 1 is fully green):**
- Merge all platform zips
- Generate `SHA256SUMS`
- GPG-sign (`SHA256SUMS.sig`)
- Upload all assets to GitHub release
- registry.terraform.io auto-detects within ~10 minutes

A single failing platform in stage 1 — whether a test failure or a build
failure — blocks the entire release. No partial publishing.

To cut a release:

1. Run pre-release checklist above.
2. Push the tag:
   ```bash
   git tag -a v0.5.0 -m "v0.5.0 — <title>"
   git push origin v0.5.0
   ```
3. Monitor the workflow:
   ```bash
   gh run watch --exit-status
   ```
4. If all green, the release publishes automatically. If anything fails,
   delete the tag, fix the issue, and re-tag.

`scripts/release.sh` (issue #21) will wrap steps 2–3 and surface failures
clearly.

### Milestones and issues

Every release has a corresponding GitHub milestone (`v0.x.0`). Close issues
as they are implemented. Before releasing, confirm the milestone shows 0 open
issues.

To check:
```bash
gh api repos/rhencke/terrible/milestones --jq '.[] | {title, open_issues, closed_issues}'
```

## Pre-commit Hook

A pre-commit hook enforces 100% unit test coverage and passing integration
tests before every commit. Install it if absent:

```bash
scripts/install-hooks.sh
```

The hook runs:
1. `uv run pytest tests/ --ignore=tests/integration -q` (unit tests, 100% coverage)
2. `TERRIBLE_INTEGRATION=1 uv run pytest tests/integration/ -q --no-cov` (integration tests)

## Claude Instructions

- Do not add `Co-Authored-By: Claude` or any Claude/Anthropic attribution to commit messages.
- Always use `scripts/release.sh` when tagging a release — never tag or create GitHub releases manually.
- Always check CI after every push (`gh run list --limit 3`) and report the result.
- Always close GitHub issues when implementing their features.
- Always tag releases with release notes — never leave notes empty.
- Before the first commit in a session, check if `.git/hooks/pre-commit` exists. If not, run `scripts/install-hooks.sh`.
