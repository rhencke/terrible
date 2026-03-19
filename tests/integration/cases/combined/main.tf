terraform {
  required_providers {
    terrible = {
      source  = "local/terrible/terrible"
      version = "0.0.1"
    }
  }
}

variable "state_file" { type = string }

# Single provider — vault password is harmless for non-vault resources
provider "terrible" {
  state_file          = var.state_file
  vault_password_file = abspath("${path.module}/vault_password.txt")
}

# ---------------------------------------------------------------------------
# Shared host — all cases use localhost
# ---------------------------------------------------------------------------
resource "terrible_host" "local" {
  host       = "127.0.0.1"
  connection = "local"
}

# ---------------------------------------------------------------------------
# ping — basic connectivity
# ---------------------------------------------------------------------------
resource "terrible_ping" "check" {
  host_id = terrible_host.local.id
}

output "ping_result" { value = terrible_ping.check.ping }
output "ping_changed" { value = terrible_ping.check.changed }

# ---------------------------------------------------------------------------
# command — touch a marker file
# ---------------------------------------------------------------------------
resource "terrible_command" "marker" {
  host_id = terrible_host.local.id
  cmd     = "touch /tmp/terrible_marker.txt"
}

output "marker_rc" { value = terrible_command.marker.rc }
output "marker_changed" { value = terrible_command.marker.changed }

# ---------------------------------------------------------------------------
# file_directory — create a directory
# ---------------------------------------------------------------------------
resource "terrible_file" "test_dir" {
  host_id = terrible_host.local.id
  path    = "/tmp/terrible_test_dir"
  state   = "directory"
}

output "dir_path" { value = terrible_file.test_dir.path }
output "dir_changed" { value = terrible_file.test_dir.changed }

# ---------------------------------------------------------------------------
# async_task — async touch with polling
# ---------------------------------------------------------------------------
resource "terrible_command" "async_touch" {
  host_id       = terrible_host.local.id
  cmd           = "touch /tmp/terrible_async_marker.txt"
  async_seconds = 10
  poll_interval = 2
}

output "async_rc" { value = terrible_command.async_touch.rc }
output "async_changed" { value = terrible_command.async_touch.changed }

# ---------------------------------------------------------------------------
# delegate_to — delegate execution from app host to control host
# ---------------------------------------------------------------------------
resource "terrible_host" "app" {
  host       = "127.0.0.1"
  connection = "local"
}

resource "terrible_host" "control" {
  host       = "127.0.0.1"
  connection = "local"
}

resource "terrible_command" "delegated" {
  host_id        = terrible_host.app.id
  delegate_to_id = terrible_host.control.id
  cmd            = "touch /tmp/terrible_delegate_marker.txt"
}

output "delegate_rc" { value = terrible_command.delegated.rc }
output "delegate_changed" { value = terrible_command.delegated.changed }

# ---------------------------------------------------------------------------
# datasource_ping — data source connectivity check
# ---------------------------------------------------------------------------
data "terrible_ping" "ds" {
  host_id = terrible_host.local.id
}

output "ds_ping_result" { value = data.terrible_ping.ds.ping }

# ---------------------------------------------------------------------------
# datasource_stat — stat a file created by another resource
# ---------------------------------------------------------------------------
resource "terrible_file" "stat_target" {
  host_id = terrible_host.local.id
  path    = "/tmp/terrible_datasource_stat_test"
  state   = "touch"
}

data "terrible_stat" "check" {
  host_id = terrible_host.local.id
  path    = terrible_file.stat_target.path
}

output "stat_file_exists" { value = jsondecode(data.terrible_stat.check.stat).exists }

# ---------------------------------------------------------------------------
# vault — decrypt an Ansible Vault encrypted secret
# ---------------------------------------------------------------------------
data "terrible_vault" "secret" {
  ciphertext = file("${path.module}/secret.vault")
}

output "vault_decrypted" {
  value     = data.terrible_vault.secret.plaintext
  sensitive = true
}
