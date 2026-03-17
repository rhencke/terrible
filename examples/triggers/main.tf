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

# Create the working directory first
resource "terrible_file" "workdir" {
  host_id = terrible_host.app.id
  path    = "/tmp/terrible_triggers"
  state   = "directory"
}

# Write the config file.  Idempotent: only changed=true when content differs.
resource "terrible_copy" "config" {
  host_id = terrible_host.app.id
  content = var.config_content
  dest    = "/tmp/terrible_triggers/app.conf"

  depends_on = [terrible_file.workdir]
}

# Deploy: write the version file.  `triggers` forces re-execution whenever
# `app_version` changes, even if the file content would be the same.
resource "terrible_copy" "version_file" {
  host_id = terrible_host.app.id
  content = "${var.app_version}\n"
  dest    = "/tmp/terrible_triggers/version.txt"

  triggers = jsonencode({
    version = var.app_version
  })

  depends_on = [terrible_file.workdir]
}

# Restart when EITHER the config content OR the deployed version changes.
# `triggers` stores the *input* values (known at plan time) — when they differ
# from the previous apply, terrible re-runs the task.
resource "terrible_command" "restart" {
  host_id = terrible_host.app.id
  cmd     = "touch /tmp/terrible_triggers/last_restart"

  triggers = jsonencode({
    config_hash = md5(var.config_content)
    app_version = var.app_version
  })

  depends_on = [terrible_copy.config, terrible_copy.version_file]
}

output "deployed_version" {
  value = var.app_version
}

output "restart_changed" {
  value = terrible_command.restart.changed
}
