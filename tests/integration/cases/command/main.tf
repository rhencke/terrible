terraform {
  required_providers {
    terrible = {
      source  = "local/terrible/terrible"
      version = "0.0.1"
    }
  }
}

variable "state_file"  { type = string }
variable "connection"  { default = "local" }
variable "host"        { default = "127.0.0.1" }
variable "ssh_port"    { default = 22 }
variable "ssh_user"    { default = "" }
variable "ssh_key"     { default = "" }

provider "terrible" {
  state_file = var.state_file
}

resource "terrible_host" "target" {
  host             = var.host
  connection       = var.connection
  port             = var.ssh_port
  user             = var.ssh_user != "" ? var.ssh_user : null
  private_key_path = var.ssh_key != "" ? var.ssh_key : null
}

resource "terrible_command" "marker" {
  host_id = terrible_host.target.id
  cmd     = "touch /tmp/terrible_marker.txt"
}

output "marker_rc"      { value = terrible_command.marker.rc }
output "marker_changed" { value = terrible_command.marker.changed }
