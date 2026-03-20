# terrible — Terraform Provider for Ansible (pure Python)

A minimal Terraform provider that integrates with Ansible, implemented in pure Python.
This repository contains a small reference implementation and helpers to use Ansible
playbooks and tasks as Terraform-managed resources.

## Overview

This project aims to provide a lightweight Terraform provider that delegates
configuration to Ansible. It's written in pure Python to make development and
extension easy for Python-savvy operators.

## Features

- Use Ansible playbooks as Terraform-managed resources
- Small, easy-to-read Python codebase for learning and extension
- Example integration patterns for provisioning and configuration

## Requirements

- Python 3.12+
- Terraform (for real usage)
- Ansible (to execute playbooks)

## Installation

This repository is a minimal implementation — it does not publish a binary
Terraform provider. For development, install the Python dependencies from
`pyproject.toml` and run the example code:

```bash
python -m pip install -e .
```

## Usage

Install in editable mode and run the provider in dev (reattach) mode:

```bash
pip install -e .
make run-provider       # prints TF_REATTACH_PROVIDERS
make example-fresh      # install, wipe state, auto-apply the example config
```

See [`examples/`](examples/) for working Terraform configurations demonstrating
task chains, parallel execution, triggers, and cloud VM provisioning.

## Development

The provider lives in `terrible_provider/`. Key files:

- `terrible_provider/provider.py` — provider entrypoint and state management
- `terrible_provider/task_base.py` — in-process Ansible execution engine
- `terrible_provider/discovery.py` — dynamic Ansible module → Terraform resource mapping
- `terrible_provider/host.py` — `terrible_host` resource

Run tests:

```bash
pytest -q                          # unit tests
make integration-test              # full Terraform + Ansible integration tests
```

## Contributing

Contributions and issues are welcome. Keep changes focused and include tests
when adding features.

## License

GPLv3

