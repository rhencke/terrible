# Triggers — re-run a task when inputs change, even if the task itself is idempotent
#
# The `triggers` attribute accepts an arbitrary map.  When any value in the map
# changes between applies, terrible treats the resource as dirty and re-runs the
# task, regardless of whether the module itself would report a change.
#
# Common uses:
#   - redeploy when a config file or artifact checksum changes
#   - re-run a handler when a related resource is updated
#   - force-restart a service after a package upgrade
#
# Run with:
#   tofu apply -var="app_version=1.2.3" -var="state_file=/tmp/triggers_state.json"

terraform {
  required_providers {
    terrible = {
      source  = "local/terrible/terrible"
      version = "0.0.1"
    }
  }
}

variable "state_file" {
  description = "Path for the terrible provider state file"
  default     = "/tmp/triggers_state.json"
}

variable "app_version" {
  description = "Application version to deploy.  Changing this triggers a redeploy."
  default     = "1.0.0"
}

variable "config_content" {
  description = "Contents of the app config file.  Changing this triggers a restart."
  default     = "log_level=info\nport=8080\n"
}

provider "terrible" {
  state_file = var.state_file
}

resource "terrible_host" "app" {
  host       = "127.0.0.1"
  connection = "local"
}

# Write the config file.  Terrible tracks whether the file content changed via
# the `changed` output, but we also want the restart task below to fire whenever
# the content changes — even on a second apply where the file already exists.
resource "terrible_copy" "config" {
  host_id = terrible_host.app.id
  content = var.config_content
  dest    = "/tmp/terrible_app/app.conf"
}

# Deploy the app.  `triggers` contains the version string, so tofu will mark
# this resource dirty — and re-run the task — whenever `app_version` changes.
resource "terrible_command" "deploy" {
  host_id = terrible_host.app.id
  cmd     = "echo 'deploying ${var.app_version}' > /tmp/terrible_app/version.txt"

  triggers = {
    version = var.app_version
  }
}

# Restart the service when EITHER the config OR the deploy changes.
# Referencing the `changed` output of upstream tasks in `triggers` means this
# task only fires on applies where something actually changed.
resource "terrible_command" "restart" {
  host_id = terrible_host.app.id
  cmd     = "echo 'restarted' >> /tmp/terrible_app/restart.log"

  triggers = {
    config_changed = tostring(terrible_copy.config.changed)
    deploy_changed = tostring(terrible_command.deploy.changed)
  }

  depends_on = [terrible_command.deploy, terrible_copy.config]
}

output "deployed_version" {
  value = var.app_version
}

output "restart_changed" {
  value = terrible_command.restart.changed
}
