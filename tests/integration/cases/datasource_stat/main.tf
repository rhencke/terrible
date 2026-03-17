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

resource "terrible_file" "target" {
  host_id  = terrible_host.local.id
  path     = "/tmp/terrible_datasource_stat_test"
  state    = "touch"
}

data "terrible_stat" "check" {
  host_id = terrible_host.local.id
  path    = terrible_file.target.path
}

output "file_exists" { value = jsondecode(data.terrible_stat.check.stat).exists }
