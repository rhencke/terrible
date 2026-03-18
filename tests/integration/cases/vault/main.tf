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
  state_file          = var.state_file
  vault_password_file = abspath("${path.module}/vault_password.txt")
}

data "terrible_vault" "secret" {
  ciphertext = file("${path.module}/secret.vault")
}

output "decrypted" {
  value     = data.terrible_vault.secret.plaintext
  sensitive = true
}
