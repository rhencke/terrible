terraform {
  required_providers {
    terrible = {
      source  = "registry.terraform.io/rhencke/terrible"
      version = ">= 0.0.1"
    }
  }
}

variable "connection" { default = "local" }
variable "host"       { default = "127.0.0.1" }
variable "ssh_port"   { default = 22 }
variable "ssh_user"   { default = "" }
variable "ssh_key"    { default = "" }


provider "terrible" {
}

resource "terrible_host" "target" {
  host             = var.host
  connection       = var.connection
  port             = var.ssh_port
  user             = var.ssh_user != "" ? var.ssh_user : null
  private_key_path = var.ssh_key != "" ? var.ssh_key : null
}

resource "terrible_ping" "check" {
  host_id = terrible_host.target.id
}

output "ping_result" { value = terrible_ping.check.ping }
output "changed"     { value = terrible_ping.check.changed }
