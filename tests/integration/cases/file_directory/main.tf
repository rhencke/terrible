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

resource "terrible_file" "test_dir" {
  host_id = terrible_host.local.id
  path    = "/tmp/terrible_test_dir"
  state   = "directory"
}

output "path" { value = terrible_file.test_dir.path }
