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
variable "connection"  { default = "local" }
variable "host"        { default = "127.0.0.1" }
variable "ssh_port"    { default = 22 }
variable "ssh_user"    { default = "" }
variable "ssh_key"     { default = "" }

provider "terrible" {
  state_file = var.state_file
}

# Host A — the "logical" target
resource "terrible_host" "app" {
  host             = var.host
  connection       = var.connection
  port             = var.ssh_port
  user             = var.ssh_user != "" ? var.ssh_user : null
  private_key_path = var.ssh_key != "" ? var.ssh_key : null
}

# Host B — the delegate (same target for testing)
resource "terrible_host" "control" {
  host             = var.host
  connection       = var.connection
  port             = var.ssh_port
  user             = var.ssh_user != "" ? var.ssh_user : null
  private_key_path = var.ssh_key != "" ? var.ssh_key : null
}

# Task is logically on host A but delegated to host B
resource "terrible_command" "delegated" {
  host_id        = terrible_host.app.id
  delegate_to_id = terrible_host.control.id
  cmd            = "touch /tmp/terrible_delegate_marker.txt"
}

output "delegate_rc"      { value = terrible_command.delegated.rc }
output "delegate_changed" { value = terrible_command.delegated.changed }
