Examples
--------

This folder contains a minimal example showing how you might declare an
Ansible-managed resource from Terraform using the pure-Python provider in
this repository.

- `terraform/main.tf` — illustrative HCL using a hypothetical `ansible` provider.
- `ansible/site.yml` — a tiny playbook that creates/writes `/tmp/ansible_provider_example.txt`.
- `ansible/inventory.ini` — inventory with a `localhost` connection.

To try the example locally (works out-of-the-box):

1. Run the included Python runner which mimics the example Ansible playbook:

```bash
python3 examples/run_example.py
```

This writes `/tmp/ansible_provider_example.txt` with a short message. The
repo also contains a conceptual Terraform `examples/terraform/main.tf` that
shows how a real `ansible` provider could be used.

This repository is a learning/reference implementation — it does not ship a
real provider binary for Terraform registries.
