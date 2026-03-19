# CLAUDE.md

## Project Overview

**terrible** is a Terraform provider written in pure Python that exposes Ansible tasks as Terraform-managed resources. Operators define target hosts and Ansible task/playbook executions as Terraform resources, keeping everything in Terraform state — no Ansible inventory required.

Resource model:
- `terrible_host` — represents a target host (SSH, WinRM, local, docker, etc.)
- `terrible_ansible_builtin_*` (and other modules) — dynamically-generated task resources, one per discovered Ansible module
- `terrible_playbook` — runs an Ansible playbook file
- `terrible_role` — runs an Ansible role
- `terrible_vault` (data source) — decrypts Ansible Vault ciphertext
- `terrible_datasource_ansible_builtin_*` — task data sources for modules with full check mode support

Task resources are discovered dynamically from installed Ansible modules at runtime. Their schemas are generated from each module's `DOCUMENTATION` and `RETURN` blocks and cached in SQLite.

## Tech Stack

- Python 3.12+
- [`tf`](https://pypi.org/project/tf/) — Python Terraform provider framework (gRPC)
- `ansible>=13.3.0` — Ansible runtime (executed in-process via `TaskQueueManager`)
- `pywinrm>=0.4.0` — WinRM support (optional extra)
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
terrible_provider/        # Main package
  cli.py                  # CLI entrypoint (tf.runner.run_provider)
  provider.py             # TerribleProvider — schema, state, resource/datasource registry
  host.py                 # terrible_host resource
  task_base.py            # TerribleTaskBase — dynamically-discovered Ansible module resources
  task_datasource.py      # Task data sources (modules with check_mode: full)
  discovery.py            # Dynamic discovery: Ansible modules → Terraform resources/datasources
  play.py                 # terrible_playbook and terrible_role resources
  vault.py                # terrible_vault data source (Ansible Vault decryption)
  install.py              # Provider installation utilities
scripts/
  install-hooks.sh        # Install pre-commit hook
  pre-commit              # Pre-commit hook script
  release.sh              # Release automation script
bin/
  terraform-provider-terrible  # Wrapper script for provider CLI
  install-provider             # Wrapper for install script
examples/
  ansible/                     # Example Ansible inventory + playbook
  terraform/                   # Conceptual HCL (illustrative)
  terraform_provider/          # Working Terraform config for local dev
  parallel_tasks/              # Integration test scenario
  task_chain/                  # Integration test scenario
  triggers/                    # Integration test scenario
  cloud_vm/                    # Cloud VM example
tests/
  test_provider.py             # Provider schema and config tests
  test_host.py                 # TerribleHost CRUD tests
  test_task_base.py            # Task resource tests
  test_task_datasource.py      # Task data source tests
  test_discovery.py            # Dynamic discovery and schema generation tests
  test_play.py                 # Playbook and role tests
  test_vault.py                # Vault data source tests
  integration/
    conftest.py                # Fixtures and Terraform provisioning
    test_cases.py              # Main integration test runner
    cases/                     # Per-feature integration test cases
      ping/                    # ansible.builtin.ping
      command/                 # ansible.builtin.command
      file_directory/          # ansible.builtin.file
      async_task/              # async_seconds parameter
      delegate_to/             # delegate_to_id parameter
      datasource_ping/         # data source for ping
      datasource_stat/         # data source for stat
      vault/                   # Ansible Vault decryption
```

## Architecture Notes

- **State:** Persisted locally in `terrible_state.json` (JSON)
- **Provider full name:** `local/terrible/terrible`
- **Ansible execution:** In-process via `TaskQueueManager` with a single thread-safe module execution lock
- **Discovery:** Ansible modules are introspected at startup; schemas generated from `DOCUMENTATION` and `RETURN` blocks; cached in SQLite at `~/.cache/tf-python-provider/discovery.db` keyed by Ansible version
- **Task resources:** One Terraform resource class per Ansible module. Common attributes: `host_id`, `result`, `changed`, `triggers`, `timeout`, `ignore_errors`, `changed_when`, `failed_when`, `environment`, `tags`, `skip_tags`, `async_seconds`, `poll_interval`, `delegate_to_id`
- **Task data sources:** Only generated for modules with `check_mode: support == "full"`; run in check+diff mode without making changes
- **WinRM:** Supported via `pywinrm`; configured on `terrible_host` with `connection = "winrm"` and `winrm_*` attributes
- **Vault:** `terrible_vault` data source requires `vault_password` or `vault_password_file` on the provider block
- **The `tf` package** handles all gRPC communication with Terraform core

## Development Workflow

All changes go through pull requests. Direct pushes to `main` are blocked.

1. **Branch** — create a feature branch from `main`
2. **Develop** — commit with pre-commit hooks passing (unit + integration tests)
3. **Push & PR** — push branch, open a PR against `main`
4. **CI** — unit tests (100% coverage) and integration tests must pass
5. **Address feedback** — resolve all review conversations
6. **Merge** — auto-merge after checks pass and conversations are resolved

Branch protection on `main` enforces:
- PRs required (no direct push)
- `test` status check must pass (unit + integration tests)
- All conversations must be resolved
- Stale reviews dismissed on new pushes
- Auto-merge enabled; branches deleted after merge

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on push to `main` and PRs.
It calls `make test-all` — the same single source of truth used by the
pre-commit hook. This ensures CI and local checks can never drift apart.

CI uses OpenTofu (Terraform-compatible open source fork) for integration tests.

## Release Process

### Pre-release checklist

Before cutting any release:

1. All planned milestone issues are closed.
2. `uv run pytest -q` passes with 100% coverage locally.
3. CI is green on `main` (check with `gh run list --limit 3`).
4. Working tree is clean (`git status` shows nothing).
5. `pyproject.toml` version matches the release version.

### Cutting a release

Use `scripts/release.sh` or the `make release` target. Never tag or create
GitHub releases manually — the script enforces the checklist, runs tests,
creates the annotated tag, pushes it, creates the GitHub release, then watches
the Actions workflow.

```bash
scripts/release.sh 0.5.0 <<'EOF'
Short title line (becomes the release title)

## New features
- bullet points here

## Bug fixes / internals
- etc.
EOF

# Equivalent:
make release VERSION=0.5.0   # reads notes from stdin
```

Release notes must be non-empty. The first non-empty line becomes the release
title on GitHub.

### Cutting a release (0.5.0+ — Terraform Registry publishing)

**Nothing publishes until everything passes on every platform.**

The `release.yml` workflow enforces this with two sequential stages:

**Stage 1 — validate (all must pass before stage 2 runs):**
- Unit tests (`uv run pytest -q`, 100% coverage) on all 5 platform runners
- Integration tests on all 5 platform runners (skipped on Windows — Ansible doesn't support Windows control nodes)
- PyInstaller binary builds on all 5 platform runners

**Stage 2 — publish (runs only if stage 1 is fully green):**
- Merge all platform zips
- Generate `SHA256SUMS`
- GPG-sign (`SHA256SUMS.sig`) using `GPG_PRIVATE_KEY` + `GPG_PASSPHRASE` secrets
- Upload all assets to GitHub release
- registry.terraform.io auto-detects within ~10 minutes

A single failing platform in stage 1 — whether a test failure or a build
failure — blocks the entire release. No partial publishing.

To cut a release:

```bash
scripts/release.sh 0.5.0 <<'EOF'
Short title line (becomes the release title)

## New features
- bullet points here
EOF
```

The script runs tests, tags, pushes, creates the GitHub release, then watches
the Actions workflow and reports success or failure. If the workflow fails,
the script prints instructions to roll back the tag and release.

### GPG key setup (one-time)

The Terraform Registry requires releases to be GPG-signed. Required secrets
in GitHub → Settings → Secrets and variables → Actions:

- `GPG_PRIVATE_KEY` — ASCII-armored private key (`gpg --armor --export-secret-keys KEY_ID`)
- `GPG_PASSPHRASE` — passphrase for the key

To generate a new key:
```bash
gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: terraform-provider-terrible releases
Name-Email: releases@example.com
Expire-Date: 0
EOF
```

### GPG key rotation

1. Generate a new key (see above).
2. Export and update `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE` in GitHub Secrets.
3. Register the new public key on registry.terraform.io (Settings → GPG Keys).
4. Leave the old key registered — the registry validates historical releases against it.
5. Delete the old key from the registry only after all old releases are superseded.

### Milestones and issues

Every release has a corresponding GitHub milestone (`v0.x.0`). Close issues
as they are implemented. Before releasing, confirm the milestone shows 0 open
issues.

To check:
```bash
gh api repos/rhencke/terraform-provider-terrible/milestones --jq '.[] | {title, open_issues, closed_issues}'
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
- Always work on a feature branch, never commit directly to `main`. Push and open a PR.
- Always use `scripts/release.sh` when tagging a release — never tag or create GitHub releases manually.
- Always check CI after every push (`gh run list --limit 3`) and report the result.
- Always close GitHub issues when implementing their features.
- Always tag releases with release notes — never leave notes empty.
- Before the first commit in a session, check if `.git/hooks/pre-commit` exists. If not, run `scripts/install-hooks.sh`.
