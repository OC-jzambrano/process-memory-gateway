#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:$PATH"

IMAGE_DIGEST="${1:-}"

if [ -z "$IMAGE_DIGEST" ]; then
  echo "Error: IMAGE_DIGEST parameter is required."
  exit 1
fi

# 1. Install Docker & tools if not present
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y docker.io docker-compose-v2 curl jq unzip
  systemctl enable --now docker
fi

systemctl is-active --quiet docker || systemctl start docker

# Ensure AWS CLI v2 is available for S3 backups
if ! command -v aws >/dev/null 2>&1; then
  echo "Installing AWS CLI v2..."
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
  unzip -qo /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install --update || /tmp/aws/install
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

# 2. Mount persistent EBS volume
DATA_MOUNT="/mnt/process-memory-data"
mkdir -p "$DATA_MOUNT"

if ! mountpoint -q "$DATA_MOUNT"; then
  DEVICE=$(lsblk -dpno NAME,TYPE | grep disk | grep -v nvme0n1 | awk '{print $1}' | head -n 1)
  if [ -n "$DEVICE" ]; then
    echo "Found secondary EBS block device: $DEVICE"
    if ! blkid "$DEVICE"; then
      echo "Formatting $DEVICE as ext4..."
      mkfs.ext4 -F "$DEVICE"
    fi
    mount "$DEVICE" "$DATA_MOUNT" || true
    UUID=$(blkid -s UUID -o value "$DEVICE" || true)
    if [ -n "$UUID" ] && ! grep -q "$DATA_MOUNT" /etc/fstab; then
      echo "UUID=$UUID $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
    fi
  fi
fi

mountpoint -q "$DATA_MOUNT" || (echo "FATAL: Persistent volume $DATA_MOUNT could not be mounted!" && exit 1)
chown 10001:10001 "$DATA_MOUNT"
chmod 750 "$DATA_MOUNT"

APP_DIR="/opt/process-memory"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# 3. Authenticate Docker with Amazon ECR
REGISTRY=$(echo "$IMAGE_DIGEST" | cut -d'/' -f1)
REGION="eu-north-1"
echo "Logging into Amazon ECR via the EC2 instance role..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# 4. Pull image
docker pull "$IMAGE_DIGEST"

touch .env
cp .env .env.bak
chmod 600 .env .env.bak

# 5. Write .env
grep -v '^MCP_IMAGE=' .env.bak | grep -v '^COGNITO_' > .env || true
cat << EOF >> .env
MCP_IMAGE=$IMAGE_DIGEST
DOMAIN_NAME=51.20.246.78
MCP_RESOURCE_URL=https://sjbs8r4vg0.execute-api.eu-north-1.amazonaws.com/companies/odooconcept_demo/mcp
COGNITO_DOMAIN=odoo-pm-pilot-354298.auth.eu-north-1.amazoncognito.com
COGNITO_USER_POOL_ID=eu-north-1_0CeSG3jfV
COGNITO_APP_CLIENT_ID=30bv65eumkbqei9l7p31q9ctvj
COGNITO_RESOURCE_SERVER_IDENTIFIER=https://mcp.example.com
EOF

# 6. Restart containers
docker compose down || true
docker compose up -d

echo "Checking container status..."
sleep 5
docker ps

# 7. Health check inside mcp-server container
READY=0
for i in {1..15}; do
  if docker exec mcp-server curl -fsS http://localhost:8000/health/live; then
    READY=1
    break
  fi
  sleep 3
done

if [ "$READY" -ne 1 ]; then
  echo "Health check failed, rolling back to previous configuration!"
  docker logs mcp-server || true
  mv .env.bak .env
  docker compose down || true
  docker compose up -d
  exit 1
fi

# Back up through the host's instance role. The app container cannot reliably
# reach EC2 instance metadata through Docker's bridge network.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BACKUP_BUCKET="odoo-process-memory-backups-${ACCOUNT_ID}-pilot"
printf 'BACKUP_BUCKET=%q\n' "$BACKUP_BUCKET" > /etc/default/process-memory-backup
chmod 600 /etc/default/process-memory-backup
cat << 'BACKUP_EOF' > /etc/cron.hourly/process-memory-backup
#!/usr/bin/env bash
set -euo pipefail
exec > >(logger -t process-memory-backup) 2>&1
source /etc/default/process-memory-backup

START_TS=$(date -u +%s)
BACKUP_DIR=/mnt/process-memory-data/backups
docker exec mcp-server mkdir -p /mnt/data/backups
docker exec mcp-server python scripts/backup_sqlite.py \
  --db-path /mnt/data/process_memory.db \
  --local-dir /mnt/data/backups

BACKUP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
  -name 'process_memory_*.db.gz' -newermt "@$START_TS" \
  -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
if [ -z "$BACKUP_FILE" ]; then
  echo "No new SQLite backup was created."
  exit 1
fi

BASENAME=$(basename "$BACKUP_FILE")
STAMP=${BASENAME#process_memory_}
YEAR=${STAMP:0:4}
MONTH=${STAMP:4:2}
DAY=${STAMP:6:2}
OBJECT_KEY="backups/$YEAR/$MONTH/$DAY/$BASENAME"
aws s3 cp --only-show-errors "$BACKUP_FILE" \
  "s3://$BACKUP_BUCKET/$OBJECT_KEY" --sse AES256
CONTENT_LENGTH=$(aws s3api head-object --bucket "$BACKUP_BUCKET" \
  --key "$OBJECT_KEY" --query ContentLength --output text)
if ! [[ "$CONTENT_LENGTH" =~ ^[1-9][0-9]*$ ]]; then
  echo "Uploaded backup failed the S3 object verification."
  exit 1
fi
rm -- "$BACKUP_FILE"
echo "Verified SQLite backup in s3://$BACKUP_BUCKET/$OBJECT_KEY ($CONTENT_LENGTH bytes)."
BACKUP_EOF
chmod 700 /etc/cron.hourly/process-memory-backup

rm -f .env.bak
echo "Deployment of $IMAGE_DIGEST completed successfully."
