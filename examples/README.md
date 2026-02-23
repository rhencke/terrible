Examples
--------

This folder contains a minimal example showing how you might declare an
Ansible-managed resource from Terraform using the pure-Python provider in
this repository.

- `terraform/main.tf` — illustrative HCL using a hypothetical `ansible` provider.
- `ansible/site.yml` — a tiny playbook that creates/writes `/tmp/ansible_provider_example.txt`.
- `ansible/inventory.ini` — inventory with a `localhost` connection.

To try the example locally (conceptual):

1. Install Python dependencies from `pyproject.toml`.
2. Run the provider or Python helper code that bridges Terraform and Ansible.
3. Use Terraform to apply the `main.tf` configuration.

This repository is a learning/reference implementation — it does not ship a
real provider binary for Terraform registries.
