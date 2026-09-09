#!/usr/bin/env bash
set -euxo pipefail

IMAGE_DIGEST="${1:-}"
if [ -z "$IMAGE_DIGEST" ]; then
  echo "Error: IMAGE_DIGEST parameter is required."
  exit 1
fi

DATA_MOUNT="/mnt/process-memory-data"
mkdir -p "$DATA_MOUNT"

# Mount persistent EBS volume if not already mounted
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
chmod 777 "$DATA_MOUNT"

APP_DIR="/opt/process-memory"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Run backup before starting new deployment if container exists
if docker ps -a --format '{{.Names}}' | grep -q '^mcp-server$'; then
  docker exec mcp-server python scripts/backup_sqlite.py || true
fi

# Pull the new container image
docker pull "$IMAGE_DIGEST"

touch .env
cp .env .env.bak

# Update configuration environment
grep -v '^MCP_IMAGE=' .env.bak | grep -v '^COGNITO_' > .env || true
cat << EOF >> .env
MCP_IMAGE=$IMAGE_DIGEST
COGNITO_DOMAIN=odoo-pm-pilot-354298.auth.eu-north-1.amazoncognito.com
COGNITO_USER_POOL_ID=eu-north-1_0CeSG3jfV
COGNITO_APP_CLIENT_ID=30bv65eumkbqei9l7p31q9ctvj
COGNITO_RESOURCE_SERVER_IDENTIFIER=https://mcp.example.com
EOF

# Restart application containers
docker compose down || true
docker compose up -d

echo "Checking container status..."
sleep 5
docker ps

# Health check directly inside mcp-server container
READY=0
for i in {1..10}; do
  if docker exec mcp-server curl -fsS http://localhost:8000/health/live; then
    READY=1
    break
  fi
  sleep 3
done

if [ "$READY" -ne 1 ]; then
  echo "Health check failed, rolling back to previous configuration!"
  mv .env.bak .env
  docker compose down || true
  docker compose up -d
  exit 1
fi

rm -f .env.bak
echo "Deployment of $IMAGE_DIGEST completed successfully."
