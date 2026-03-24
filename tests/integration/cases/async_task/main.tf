terraform {
  required_providers {
    terrible = {
      source  = "registry.terraform.io/rhencke/terrible"
      version = ">= 0.0.1"
    }
  }
}

variable "connection"  { default = "local" }
variable "host"        { default = "127.0.0.1" }
variable "ssh_port"    { default = 22 }
variable "ssh_user"    { default = "" }
variable "ssh_key"     { default = "" }

provider "terrible" {
}

resource "terrible_host" "target" {
  host             = var.host
  connection       = var.connection
  port             = var.ssh_port
  user             = var.ssh_user != "" ? var.ssh_user : null
  private_key_path = var.ssh_key != "" ? var.ssh_key : null
}

resource "terrible_command" "async_touch" {
  host_id       = terrible_host.target.id
  cmd           = "touch /tmp/terrible_async_marker.txt"
  async_seconds = 10
  poll_interval = 2
}

output "async_rc"      { value = terrible_command.async_touch.rc }
output "async_changed" { value = terrible_command.async_touch.changed }
