// Example Terraform configuration showing conceptual usage of the
// pure-Python Ansible provider implemented in this repository.

provider "ansible" {}

resource "ansible_playbook" "deploy_app" {
  playbook = "../ansible/site.yml"
  inventory = "../ansible/inventory.ini"
}
