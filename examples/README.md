Examples
--------

Each subdirectory is a self-contained Terraform configuration that demonstrates
a different pattern for using the `terrible` provider.

All examples that target localhost can be run with:

```bash
make install-provider          # build + install the provider binary once
cd examples/<name>
tofu init
tofu apply -var="state_file=/tmp/<name>_state.json"
```

---

### `terraform_provider/` — basic provider smoke-test

Ping localhost, run a command, create a directory.  The simplest possible
working configuration — a good starting point.

---

### `task_chain/` — sequential execution with explicit dependencies

Tasks form a pipeline: create directory → write config → start app → verify.
Each step declares `depends_on` the previous one so Terraform runs them in
order and stops if any step fails.

---

### `parallel_tasks/` — the same tasks applied to multiple hosts concurrently

Two hosts with identical task sets and no cross-host dependency.  Terraform's
default parallelism runs both sets simultaneously with no extra configuration.
Replace the placeholder IPs with real SSH targets.

---

### `triggers/` — re-run a task when inputs change

The `triggers` attribute forces a resource to be replaced (and therefore
re-executed) whenever any value in the map changes, even if the Ansible module
itself would be idempotent.  Useful for deployments, restarts, and handlers.

---

### `cloud_vm/` — provision a cloud VM then configure it with terrible

Creates an EC2 instance with the AWS provider, waits for SSH, then uses
`terrible_host` (consuming the instance's public IP) to run configuration
tasks.  Demonstrates how terrible slots into a larger Terraform graph
alongside cloud resources.  Requires AWS credentials.
