#!/usr/bin/env python3
"""Run the example without requiring Ansible or Terraform.

This script performs the minimal actions from `examples/ansible/site.yml` so
the example works out-of-the-box with only Python available.
"""
from pathlib import Path


def main() -> None:
    p = Path("/tmp/ansible_provider_example.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("Managed by the terrible Ansible Terraform provider example\n")
    print(f"Wrote example file: {p}")


if __name__ == "__main__":
    main()
