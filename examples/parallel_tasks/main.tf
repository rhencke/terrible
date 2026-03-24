# Parallel Tasks — the same work applied to multiple hosts simultaneously
#
# Terraform's default parallelism (10) means independent resources run at the
# same time.  Because the two hosts share no dependency, Terraform will apply
# tasks on both hosts concurrently — no extra configuration needed.
#
# This example uses localhost twice (different work directories) to demonstrate
# the pattern locally.  In production, replace the hosts with real SSH targets
# and a private key:
#
#   resource "terrible_host" "web1" {
#     host             = "10.0.0.1"
#     user             = "deploy"
#     private_key_path = "~/.ssh/id_ed25519"
#   }
#
# Run with:

terraform {
  required_providers {
    terrible = {
      source  = "registry.terraform.io/rhencke/terrible"
      version = "0.0.1"
    }
  }
}

provider "terrible" {
}

# --- Hosts -------------------------------------------------------------------

resource "terrible_host" "web1" {
  host       = "127.0.0.1"
  connection = "local"
}

resource "terrible_host" "web2" {
  host       = "127.0.0.1"
  connection = "local"
}

# --- Tasks (identical on both hosts, applied in parallel) --------------------

resource "terrible_file" "app_dir_web1" {
  host_id = terrible_host.web1.id
  path    = "/tmp/terrible_web1"
  state   = "directory"
}

resource "terrible_file" "app_dir_web2" {
  host_id = terrible_host.web2.id
  path    = "/tmp/terrible_web2"
  state   = "directory"
}

resource "terrible_copy" "deploy_web1" {
  host_id = terrible_host.web1.id
  content = "deployed\n"
  dest    = "/tmp/terrible_web1/app.txt"

  depends_on = [terrible_file.app_dir_web1]
}

resource "terrible_copy" "deploy_web2" {
  host_id = terrible_host.web2.id
  content = "deployed\n"
  dest    = "/tmp/terrible_web2/app.txt"

  depends_on = [terrible_file.app_dir_web2]
}

# --- Outputs -----------------------------------------------------------------

output "deploy_changed" {
  value = {
    web1 = terrible_copy.deploy_web1.changed
    web2 = terrible_copy.deploy_web2.changed
  }
}
