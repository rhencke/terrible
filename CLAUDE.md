# CLAUDE.md

## Project Overview

**terrible** is a Terraform provider written in pure Python that exposes Ansible tasks as Terraform-managed resources. Operators define target hosts and Ansible task/playbook executions as Terraform resources, keeping everything in Terraform state — no Ansible inventory required.

Resource types:
- `terrible_host` — target host (SSH, WinRM, local, docker, etc.)
- `terrible_ansible_builtin_*` — dynamically-generated task resources, one per Ansible module
- `terrible_playbook` — runs an Ansible playbook file
- `terrible_role` — runs an Ansible role
- `terrible_vault` (data source) — decrypts Ansible Vault ciphertext
- `terrible_datasource_ansible_builtin_*` — task data sources for modules with full check mode support

Task resources are discovered dynamically from installed Ansible modules at runtime. Schemas are generated from each module's `DOCUMENTATION` and `RETURN` blocks and cached in SQLite.

## Tech Stack

- Python 3.12+
- [`tf`](https://pypi.org/project/tf/) — Python Terraform provider framework (gRPC)
- `ansible>=13.3.0` — executed in-process via `TaskQueueManager`
- `pywinrm>=0.4.0` — WinRM support (optional extra)
- `pytest` — testing
- `uv` — **the only Python tool used**. No pip, poetry, pipenv, or other package managers.

## Common Commands

```bash
make test               # Unit tests (100% coverage required)
make integration-test   # Integration tests against localhost
make test-all           # Unit + integration (same as pre-commit hook)
make install-hooks      # Install git pre-commit hook
make install-provider   # Install provider into Terraform plugin directory
make run-provider       # Run provider in dev mode (prints TF_REATTACH_PROVIDERS)
make example-init       # terraform init for examples
make example-apply      # terraform apply for examples
```

## Project Structure

```
terrible_provider/
  cli.py                # CLI entrypoint
  provider.py           # TerribleProvider — schema, state, resource/datasource registry
  host.py               # terrible_host resource
  task_base.py          # TerribleTaskBase — dynamically-discovered Ansible module resources
  task_datasource.py    # Task data sources (modules with check_mode: full)
  discovery.py          # Ansible modules → Terraform resources/datasources
  play.py               # terrible_playbook and terrible_role resources
  vault.py              # terrible_vault data source
  install.py            # Provider installation utilities
scripts/
  install-hooks.sh      # Install pre-commit hook
  pre-commit            # Pre-commit hook script
  release.sh            # Release automation
tests/
  test_*.py             # Unit tests (one file per module)
  integration/          # Integration test cases (ping, command, file, async, vault, etc.)
examples/
  terraform_provider/   # Working Terraform config for local dev
  parallel_tasks/       # Integration test scenario
  task_chain/           # Integration test scenario
  triggers/             # Integration test scenario
```

## Architecture Notes

- **State:** Persisted locally in `terrible_state.json`
- **Provider full name:** `local/terrible/terrible`
- **Ansible execution:** In-process via `TaskQueueManager` with a single thread-safe module execution lock
- **Discovery:** Modules introspected at startup; schemas cached in SQLite at `~/.cache/tf-python-provider/discovery.db` keyed by Ansible version
- **Task resources:** Common attributes: `host_id`, `result`, `changed`, `triggers`, `timeout`, `ignore_errors`, `changed_when`, `failed_when`, `environment`, `tags`, `skip_tags`, `async_seconds`, `poll_interval`, `delegate_to_id`
- **Task data sources:** Only generated for modules with `check_mode: support == "full"`; run in check+diff mode
- **WinRM:** Configured on `terrible_host` with `connection = "winrm"` and `winrm_*` attributes
- **Vault:** `terrible_vault` requires `vault_password` or `vault_password_file` on the provider block

## Development Model

All work is planned and tracked through GitHub issues. A release is a group of resolved issues — when a milestone's issues are all closed, that milestone becomes a release. New work starts by creating or identifying an issue.

This section is where Claude should record planning reasoning — decisions made in plan mode, trade-offs considered, and approach chosen — so context is preserved across sessions.

## Development Workflow

All changes go through pull requests. Direct pushes to `main` are blocked.

1. Identify or create a GitHub issue
2. Create a feature branch from `main`
3. Commit with pre-commit hooks passing (unit + integration tests)
4. Push and open a PR against `main`
5. CI must pass (unit + integration); all review conversations must be resolved
6. Auto-merge after checks pass; branch deleted after merge

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs `make test-all` on push to `main` and on PRs — the same command as the pre-commit hook. Uses OpenTofu for integration tests.

## Release Process

Always enter plan mode before cutting a release.

### Pre-release checklist

1. All milestone issues are closed
2. CI is green on `main` (`gh run list --limit 3`)
3. Working tree is clean
4. `pyproject.toml` version matches the release version

```bash
gh issue list --milestone v0.5.0                          # open issues in milestone
gh api repos/rhencke/terraform-provider-terrible/milestones --jq '.[] | {title, open_issues, closed_issues}'
gh issue close <number>                                    # close an issue when done
```

### Cutting a release

Use `scripts/release.sh` — never tag or create GitHub releases manually.

```bash
scripts/release.sh 0.5.0 <<'EOF'
Short title line (becomes the release title)

## New features
- bullet points here

## Bug fixes
- etc.
EOF
```

The script runs tests, tags, pushes, creates the GitHub release, and watches the Actions workflow.

### Release workflow (0.5.0+)

Two sequential stages — nothing publishes until all of stage 1 passes:

**Stage 1 — validate (all platforms):**
- Unit tests (100% coverage) on all 3 platform runners (linux/amd64, linux/arm64, darwin/arm64)
- Integration tests (skipped on arm64 — OpenTofu has no arm64 Linux binary in the setup action yet)
- PyInstaller binary builds
- Windows is not supported as a control node (Ansible requires Unix); use WSL on Windows hosts

**Stage 2 — publish:**
- Merge platform zips, generate `SHA256SUMS`, GPG-sign with `GPG_PRIVATE_KEY`/`GPG_PASSPHRASE` secrets
- Upload to GitHub release
- registry.terraform.io auto-detects within ~10 minutes

### GPG signing

The Terraform Registry requires GPG-signed releases. Secrets stored in GitHub Actions (`GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`) are the source of truth — no local copy needed since signing only happens in CI.

To rotate: generate a new RSA 4096-bit key (registry.terraform.io requires RSA or DSA — Ed25519 is not supported), update the GitHub Secrets, register the new public key on registry.terraform.io (leave the old key registered to cover historical releases).

## Pre-commit Hook

Enforces 100% unit coverage and passing integration tests before every commit.

```bash
scripts/install-hooks.sh   # install if absent
```

Runs:
1. `uv run pytest tests/ --ignore=tests/integration -q`
2. `TERRIBLE_INTEGRATION=1 uv run pytest tests/integration/ -q --no-cov`

## Claude Instructions

- Do not add `Co-Authored-By: Claude` or any Claude/Anthropic attribution to commit messages.
- Always work on a feature branch, never commit directly to `main`. Push and open a PR.
- Always enter plan mode before cutting a release; record reasoning in the Development Model section above.
- Always use `scripts/release.sh` when tagging a release — never tag or create GitHub releases manually.
- Always check CI after every push (`gh run list --limit 3`) and report the result.
- Always close GitHub issues when implementing their features.
- Always resolve PR review threads (via GraphQL `resolveReviewThread` mutation) as you address them — replying is not enough. Don't merge with unresolved conversations.
- Always tag releases with release notes — never leave notes empty.
- **Never delete tags or releases.** Tags and releases are immutable once pushed. Deleting them triggers GitHub tag-protection rules that permanently block recreation of the same ref name. If a release workflow fails, fix the workflow and cut a new patch version instead.
- Before the first commit in a session, check if `.git/hooks/pre-commit` exists. If not, run `scripts/install-hooks.sh`.
