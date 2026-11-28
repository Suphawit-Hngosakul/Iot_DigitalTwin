# EC2 HTTP -> HTTPS relay for the ESP32 uploader (decision 2026-11-17:
# the board has no RAM for TLS while recording, see cloud/relay/relay.py).
# The same instance runs the realtime hub (ws:// :8081, D22, spec 22) so the
# board talks to one IP and both stop/start together with the lab.
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

variable "hub_enabled" {
  description = "Also run the realtime hub (cloud/realtime-hub/hub.py) on this instance"
  type        = bool
  default     = true
}

variable "hub_source_dir" {
  type    = string
  default = ""
}

variable "hub_port" {
  type    = number
  default = 8081
}

variable "device_token_hashes" {
  description = "{ sha256(token) = device_id }, same map as ingest; the hub checks board tokens with it"
  type        = map(string)
  default     = {}
  sensitive   = true
}

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
    for_each = var.hub_enabled ? [1] : []
    content {
      description = "realtime hub (boards + web)"
      from_port   = var.hub_port
      to_port     = var.hub_port
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidrs
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
  # Hub install: venv + websockets (AL2023 = Python 3.9). DEVICE_TOKENS is
  # single-quoted so systemd keeps the JSON as is. Files go in gzipped:
  # EC2 user data is capped at 16 KB.
  hub_env   = "DEVICE_TOKENS='${jsonencode(var.device_token_hashes)}'\nPORT=${var.hub_port}\n"
  hub_setup = !var.hub_enabled ? "" : <<-EOT
    dnf install -y python3-pip
    mkdir -p /opt/iot-dt-hub
    echo '${base64gzip(file("${var.hub_source_dir}/hub.py"))}' | base64 -d | gunzip > /opt/iot-dt-hub/hub.py
    echo '${base64gzip(file("${var.hub_source_dir}/requirements.txt"))}' | base64 -d | gunzip > /opt/iot-dt-hub/requirements.txt
    echo '${base64gzip(file("${var.hub_source_dir}/iot-dt-hub.service"))}' | base64 -d | gunzip > /etc/systemd/system/iot-dt-hub.service
    echo '${base64encode(local.hub_env)}' | base64 -d > /etc/iot-dt-hub.env
    chmod 600 /etc/iot-dt-hub.env
    python3 -m venv /opt/iot-dt-hub/venv
    /opt/iot-dt-hub/venv/bin/pip install --quiet -r /opt/iot-dt-hub/requirements.txt
    systemctl daemon-reload
    systemctl enable --now iot-dt-hub
  EOT

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    mkdir -p /opt/iot-dt-relay
    echo '${base64gzip(file("${var.source_dir}/relay.py"))}' | base64 -d | gunzip > /opt/iot-dt-relay/relay.py
    echo '${base64gzip(file("${var.source_dir}/iot-dt-relay.service"))}' | base64 -d | gunzip > /etc/systemd/system/iot-dt-relay.service
    printf 'UPSTREAM_URL=%s\nPORT=%s\n' '${var.upstream_url}' '${var.port}' > /etc/iot-dt-relay.env
    systemctl daemon-reload
    systemctl enable --now iot-dt-relay
    ${local.hub_setup}
  EOT
}

# No SSH (plan R5): logs are read with SSM Session Manager (console: EC2 > Connect >
# Session Manager, or `aws ssm start-session --target <relay_instance_id>`).
# The SSM agent ships with AL2023; it only needs this role and outbound HTTPS.
resource "aws_iam_role" "relay" {
  name = "${var.name_prefix}-relay-ec2"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "relay_ssm" {
  role       = aws_iam_role.relay.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "relay" {
  name = "${var.name_prefix}-relay-ec2"
  role = aws_iam_role.relay.name
}

resource "aws_instance" "relay" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  iam_instance_profile        = aws_iam_instance_profile.relay.name
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

output "hub_device_url" {
  description = "Firmware wssUrl (plain ws://, D22)"
  value       = var.hub_enabled ? "ws://${aws_eip.relay.public_ip}:${var.hub_port}/device" : null
}

output "hub_live_url" {
  description = "Web / live.html: ws://.../live?device=all"
  value       = var.hub_enabled ? "ws://${aws_eip.relay.public_ip}:${var.hub_port}/live" : null
}

output "instance_id" {
  description = "aws ssm start-session --target <this>  (relay/hub logs: journalctl -u iot-dt-relay -f)"
  value       = aws_instance.relay.id
}

output "public_dns" {
  description = "EIP public DNS name (CloudFront origin for the hub, plan R4)"
  value       = aws_eip.relay.public_dns
}

output "hub_port" {
  value = var.hub_port
}
