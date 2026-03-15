# Parallel Tasks — the same work applied to multiple hosts simultaneously
#
# Terraform's default parallelism (10) means independent resources run at the
# same time.  Because the two hosts share no dependency, Terraform will apply
# tasks on both hosts concurrently — no extra configuration needed.
#
# Replace the host/connection values with real SSH targets and a private key.
#
# Run with:
#   tofu apply -var="state_file=/tmp/parallel_state.json"

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
  default     = "/tmp/parallel_state.json"
}

provider "terrible" {
  state_file = var.state_file
}

# --- Hosts -------------------------------------------------------------------

resource "terrible_host" "web1" {
  host             = "10.0.0.1"
  user             = "deploy"
  private_key_path = "~/.ssh/id_ed25519"
}

resource "terrible_host" "web2" {
  host             = "10.0.0.2"
  user             = "deploy"
  private_key_path = "~/.ssh/id_ed25519"
}

# --- Tasks (identical on both hosts, run in parallel) ------------------------

resource "terrible_file" "app_dir_web1" {
  host_id = terrible_host.web1.id
  path    = "/opt/myapp"
  state   = "directory"
}

resource "terrible_file" "app_dir_web2" {
  host_id = terrible_host.web2.id
  path    = "/opt/myapp"
  state   = "directory"
}

resource "terrible_command" "deploy_web1" {
  host_id = terrible_host.web1.id
  cmd     = "cp /tmp/myapp.tar.gz /opt/myapp/ && tar -xzf /opt/myapp/myapp.tar.gz -C /opt/myapp"

  depends_on = [terrible_file.app_dir_web1]
}

resource "terrible_command" "deploy_web2" {
  host_id = terrible_host.web2.id
  cmd     = "cp /tmp/myapp.tar.gz /opt/myapp/ && tar -xzf /opt/myapp/myapp.tar.gz -C /opt/myapp"

  depends_on = [terrible_file.app_dir_web2]
}

# --- Outputs -----------------------------------------------------------------

output "deploy_changed" {
  value = {
    web1 = terrible_command.deploy_web1.changed
    web2 = terrible_command.deploy_web2.changed
  }
}
