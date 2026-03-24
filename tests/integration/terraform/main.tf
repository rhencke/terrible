terraform {
  required_providers {
    terrible = {
      source  = "registry.terraform.io/rhencke/terrible"
      version = ">= 0.0.1"
    }
  }
}

variable "ssh_host" { type = string }
variable "ssh_port" { type = number; default = 22 }
variable "ssh_user" { type = string; default = "alpine" }
variable "ssh_key_path" { type = string; sensitive = true }
provider "terrible" {
}

resource "terrible_host" "vm" {
  host             = var.ssh_host
  port             = var.ssh_port
  user             = var.ssh_user
  private_key_path = var.ssh_key_path
}

resource "terrible_ping" "check" {
  host_id = terrible_host.vm.id
}

resource "terrible_command" "marker" {
  host_id = terrible_host.vm.id
  cmd     = "echo 'terrible-ok' | tee /tmp/terrible_marker.txt"
}

resource "terrible_file" "test_dir" {
  host_id = terrible_host.vm.id
  path    = "/tmp/terrible_test_dir"
  state   = "directory"
}

output "host_id"        { value = terrible_host.vm.id }
output "ping_result"    { value = terrible_ping.check.ping }
output "marker_stdout"  { value = terrible_command.marker.stdout }
output "marker_rc"      { value = terrible_command.marker.rc }
output "marker_changed" { value = terrible_command.marker.changed }
