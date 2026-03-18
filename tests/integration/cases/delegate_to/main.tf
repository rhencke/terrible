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
  default     = "/tmp/delegate_to_state.json"
}

provider "terrible" {
  state_file = var.state_file
}

# Host A — the "logical" target
resource "terrible_host" "app" {
  host       = "127.0.0.1"
  connection = "local"
}

# Host B — the delegate (also local for testing)
resource "terrible_host" "control" {
  host       = "127.0.0.1"
  connection = "local"
}

# Task is logically on host A but delegated to host B
resource "terrible_command" "delegated" {
  host_id        = terrible_host.app.id
  delegate_to_id = terrible_host.control.id
  cmd            = "touch /tmp/terrible_delegate_marker.txt"
}

output "delegate_rc" {
  value = terrible_command.delegated.rc
}

output "delegate_changed" {
  value = terrible_command.delegated.changed
}
