# Task Chain — sequential execution with explicit dependencies
#
# Pattern: each task depends on the previous one, forming a pipeline.
# Terraform respects the dependency graph and runs steps in order.
#
# This example: create a working directory → drop a config file → run the app
# → verify it started.  Each step only runs if the previous succeeded.
#
# Run with:
#   tofu apply -var="state_file=/tmp/task_chain.json"

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
  default     = "/tmp/task_chain_state.json"
}

provider "terrible" {
  state_file = var.state_file
}

resource "terrible_host" "app" {
  host       = "127.0.0.1"
  connection = "local"
}

# Step 1: create the working directory
resource "terrible_file" "workdir" {
  host_id = terrible_host.app.id
  path    = "/tmp/terrible_app"
  state   = "directory"
}

# Step 2: write a config file — only runs after the directory exists
resource "terrible_copy" "config" {
  host_id  = terrible_host.app.id
  content  = "log_level=info\nport=8080\n"
  dest     = "/tmp/terrible_app/app.conf"

  depends_on = [terrible_file.workdir]
}

# Step 3: "start" the app — runs after the config is in place
resource "terrible_command" "start" {
  host_id = terrible_host.app.id
  cmd     = "touch /tmp/terrible_app/app.pid"   # stand-in for a real start command

  depends_on = [terrible_copy.config]
}

# Step 4: verify — only runs after the start command
resource "terrible_command" "verify" {
  host_id = terrible_host.app.id
  cmd     = "test -f /tmp/terrible_app/app.pid && echo 'app is running'"

  depends_on = [terrible_command.start]
}

output "workdir_changed" {
  value = terrible_file.workdir.changed
}

output "start_rc" {
  value = terrible_command.start.rc
}

output "verify_stdout" {
  value = terrible_command.verify.stdout
}
