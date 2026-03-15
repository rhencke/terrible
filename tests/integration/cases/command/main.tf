terraform {
  required_providers {
    terrible = {
      source  = "local/terrible/terrible"
      version = "0.0.1"
    }
  }
}

variable "state_file" { type = string }

provider "terrible" {
  state_file = var.state_file
}

resource "terrible_host" "local" {
  host       = "127.0.0.1"
  connection = "local"
}

resource "terrible_command" "marker" {
  host_id = terrible_host.local.id
  cmd     = "touch /tmp/terrible_marker.txt"
}

output "marker_rc"      { value = terrible_command.marker.rc }
output "marker_changed" { value = terrible_command.marker.changed }
