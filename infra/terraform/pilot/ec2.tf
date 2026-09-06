# Persistent 20 GiB gp3 Encrypted EBS Volume for SQLite
resource "aws_ebs_volume" "memory_data" {
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = var.ebs_volume_size_gb
  type              = "gp3"
  encrypted         = true

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name        = "odoo-process-memory-${var.environment}-ebs-data"
    Purpose     = "Persistent SQLite database storage"
  }
}

# Single t3.small EC2 Host
resource "aws_instance" "mcp_host" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = var.ec2_instance_type
  subnet_id            = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.mcp_host.id]
  iam_instance_profile = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size           = 12
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = <<-EOF
              #!/bin/bash
              set -euxo pipefail

              # Update & install dependencies
              apt-get update
              apt-get install -y ca-certificates curl gnupg lsb-release jq awscli

              # Install Docker and Docker Compose Plugin
              install -m 0755 -d /etc/apt/keyrings
              curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
              chmod a+r /etc/apt/keyrings/docker.gpg
              echo "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
              apt-get update
              apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

              systemctl enable docker
              systemctl start docker

              # Format and Mount Persistent EBS Volume
              DATA_MOUNT="/mnt/process-memory-data"
              mkdir -p "$DATA_MOUNT"

              # Wait up to 60s for EBS volume attachment
              DEVICE="/dev/nvme1n1"
              if [ ! -b "$DEVICE" ]; then
                DEVICE="/dev/xvdf"
              fi

              for i in {1..30}; do
                if [ -b "$DEVICE" ]; then break; fi
                sleep 2
              done

              if [ -b "$DEVICE" ]; then
                if ! blkid "$DEVICE"; then
                  mkfs.ext4 -F "$DEVICE"
                fi
                UUID=$(blkid -s UUID -o value "$DEVICE")
                if ! grep -q "$UUID" /etc/fstab; then
                  echo "UUID=$UUID $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
                fi
                mount -a || true
              fi

              chmod 777 "$DATA_MOUNT"

              # Setup application directory
              mkdir -p /opt/process-memory

              # Setup hourly backup cron
              cat << 'CRON_EOF' > /etc/cron.hourly/process-memory-backup
              #!/bin/bash
              set -e
              if [ -f /opt/process-memory/docker-compose.yml ]; then
                docker exec mcp-server python scripts/backup_sqlite.py --s3-bucket "${aws_s3_bucket.backups.id}" || true
              fi
              CRON_EOF
              chmod +x /etc/cron.hourly/process-memory-backup

              echo "Bootstrap completed successfully."
              EOF

  tags = {
    Name = "odoo-process-memory-${var.environment}-host"
  }
}

# Attach Persistent Data Volume to EC2 Instance
resource "aws_volume_attachment" "ebs_att" {
  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.memory_data.id
  instance_id = aws_instance.mcp_host.id
}

# Static Elastic IP Address
resource "aws_eip" "mcp_eip" {
  instance = aws_instance.mcp_host.id
  domain   = "vpc"

  tags = {
    Name = "odoo-process-memory-${var.environment}-eip"
  }
}
