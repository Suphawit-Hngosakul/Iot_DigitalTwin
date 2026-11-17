# EC2 HTTP -> HTTPS relay for the ESP32 uploader (decision 2026-11-17:
# the board has no RAM for TLS while recording, see cloud/relay/relay.py).
# Learner Lab: default VPC, no instance profile needed (relay only calls the
# public ingest API). The Elastic IP keeps the address when the lab stops and
# starts the instance.
terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

variable "name_prefix" { type = string }
variable "upstream_url" { type = string }
variable "source_dir" { type = string }

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "port" {
  type    = number
  default = 8080
}

variable "allowed_cidrs" {
  description = "Who may POST to the relay. Boards move between networks, so the default is open; the device token still gates ingest."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ssh_cidrs" {
  description = "Port 22 for reading logs; empty = closed. EC2 Instance Connect (console) in us-east-1 = 18.206.107.24/29"
  type        = list(string)
  default     = []
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_security_group" "relay" {
  name        = "${var.name_prefix}-relay"
  description = "ESP32 plain HTTP to ingest relay"

  ingress {
    description = "relay"
    from_port   = var.port
    to_port     = var.port
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  dynamic "ingress" {
    for_each = length(var.ssh_cidrs) > 0 ? [1] : []
    content {
      description = "ssh (logs)"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.ssh_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    mkdir -p /opt/iot-dt-relay
    echo '${base64encode(file("${var.source_dir}/relay.py"))}' | base64 -d > /opt/iot-dt-relay/relay.py
    echo '${base64encode(file("${var.source_dir}/iot-dt-relay.service"))}' | base64 -d > /etc/systemd/system/iot-dt-relay.service
    printf 'UPSTREAM_URL=%s\nPORT=%s\n' '${var.upstream_url}' '${var.port}' > /etc/iot-dt-relay.env
    systemctl daemon-reload
    systemctl enable --now iot-dt-relay
  EOT
}

resource "aws_instance" "relay" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  vpc_security_group_ids      = [aws_security_group.relay.id]
  user_data                   = local.user_data
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
  }

  tags = { Name = "${var.name_prefix}-relay" }
}

resource "aws_eip" "relay" {
  instance = aws_instance.relay.id
  domain   = "vpc"
  tags     = { Name = "${var.name_prefix}-relay" }
}

output "relay_url" {
  value = "http://${aws_eip.relay.public_ip}:${var.port}/ingest"
}
