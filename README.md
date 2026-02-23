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

- Python 3.11+
- Terraform (for real usage)
- Ansible (to execute playbooks)

## Installation

This repository is a minimal implementation — it does not publish a binary
Terraform provider. For development, install the Python dependencies from
`pyproject.toml` and run the example code:

```bash
python -m pip install -e .
```

## Usage (example)

This repository includes a small runnable helper so the example works
immediately with only Python available. To run the example and reproduce the
playbook's effect (create/write the example file), run:

```bash
python3 examples/run_example.py
```

There is also a conceptual Terraform snippet in `examples/terraform/main.tf`
showing how an `ansible` provider could be used in HCL; that snippet is for
illustration only and not required to run the included example.

## Development

- Edit `main.py` to explore the provider's entrypoint and behavior.
- Run and iterate locally; the code is intentionally small so you can read and
	modify it quickly.

## Contributing

Contributions and issues are welcome. Keep changes focused and include tests
when adding features.

## License

MIT

