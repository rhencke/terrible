terraform {
  required_providers {
    terrible = {
      source  = "local/terrible/terrible"
      version = "0.0.1"
    }
  }
}

provider "terrible" {}

resource "terrible_host" "web" {
  host = "192.168.1.100"
  port = 22
  user = "ubuntu"
}

resource "terrible_ping" "connectivity" {
  host_id = terrible_host.web.id
}

resource "terrible_command" "hello" {
  host_id = terrible_host.web.id
  cmd     = "echo 'hello from terrible'"
}

resource "terrible_file" "workdir" {
  host_id = terrible_host.web.id
  path    = "/tmp/terrible"
  state   = "directory"
}

output "host_id" {
  value = terrible_host.web.id
}

output "ping_result" {
  value = terrible_ping.connectivity.result
}

output "hello_changed" {
  value = terrible_command.hello.changed
}
