# Cloud VM — provision an EC2 instance then configure it with terrible
#
# This example shows the most common real-world pattern: a cloud provider
# creates the machine, and terrible runs Ansible tasks against it once it's up.
#
# The terrible_host resource consumes outputs from the aws_instance (public IP,
# keypair), creating an implicit dependency — Terraform will not try to connect
# until the instance exists and is reachable.
#
# Prerequisites:
#   - AWS credentials in environment (AWS_PROFILE, AWS_ACCESS_KEY_ID, etc.)
#   - An EC2 key pair named "my-keypair" with the private key at ~/.ssh/my-keypair.pem
#   - A security group that allows inbound SSH on port 22
#
# Run with:
#   tofu init

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    terrible = {
      source  = "registry.terraform.io/rhencke/terrible"
      version = "0.0.1"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

provider "terrible" {
}

# --- Infrastructure (AWS) ----------------------------------------------------

resource "aws_instance" "web" {
  ami           = "ami-0c55b159cbfafe1f0"   # Amazon Linux 2 us-east-1
  instance_type = "t3.micro"
  key_name      = "my-keypair"

  tags = {
    Name = "terrible-example"
  }
}

# Wait for SSH to be reachable before handing the host to terrible.
# aws_instance is "created" as soon as the API call returns, but SSH takes
# another 30-60s.  This null_resource + provisioner pattern gates on port 22.
resource "null_resource" "wait_for_ssh" {
  triggers = {
    instance_id = aws_instance.web.id
  }

  provisioner "local-exec" {
    command = "until nc -zw5 ${aws_instance.web.public_ip} 22; do sleep 5; done"
  }
}

# --- Host (terrible) ---------------------------------------------------------

# terrible_host consumes the public IP from the aws_instance output.
# Terraform infers depends_on automatically from the reference.
resource "terrible_host" "web" {
  host             = aws_instance.web.public_ip
  user             = "ec2-user"
  private_key_path = "~/.ssh/my-keypair.pem"

  depends_on = [null_resource.wait_for_ssh]
}

# --- Configuration tasks (terrible) -----------------------------------------

resource "terrible_command" "update_packages" {
  host_id = terrible_host.web.id
  cmd     = "sudo yum update -y"
}

resource "terrible_file" "app_dir" {
  host_id = terrible_host.web.id
  path    = "/opt/myapp"
  state   = "directory"
  owner   = "ec2-user"

  depends_on = [terrible_command.update_packages]
}

resource "terrible_copy" "config" {
  host_id  = terrible_host.web.id
  content  = "log_level=info\nport=8080\n"
  dest     = "/opt/myapp/app.conf"
  owner    = "ec2-user"

  depends_on = [terrible_file.app_dir]
}

# --- Outputs -----------------------------------------------------------------

output "instance_id" {
  value = aws_instance.web.id
}

output "public_ip" {
  value = aws_instance.web.public_ip
}

output "packages_updated" {
  value = terrible_command.update_packages.changed
}
