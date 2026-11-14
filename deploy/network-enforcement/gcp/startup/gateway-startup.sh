#!/bin/bash
set -euo pipefail

# GCE reference bootstrap: install Docker and run the RaiCentral gateway container
# bound to :8000 for the internal load balancer health check.
# Override image and env via instance custom metadata keys:
#   rai-gateway-image (default: ghcr.io/ztechnium/raicentral:latest)
#   rai-gateway-env-file (optional path on VM, default: /etc/rai/gateway.env)

GATEWAY_IMAGE="$(curl -sf -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/rai-gateway-image \
  2>/dev/null || true)"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-ghcr.io/ztechnium/raicentral:latest}"

ENV_FILE="$(curl -sf -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/rai-gateway-env-file \
  2>/dev/null || true)"
ENV_FILE="${ENV_FILE:-/etc/rai/gateway.env}"

if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io
fi

mkdir -p /etc/rai
if [ ! -f "${ENV_FILE}" ]; then
  cat > "${ENV_FILE}" <<'EOF'
GATEWAY_ENFORCEMENT_MODE=ENFORCE
FAIL_CLOSED_ON_RAI_UNAVAILABLE=true
PROVIDER_GATEWAY_ISOLATED_CREDENTIALS=true
DATABASE_URL=postgresql://127.0.0.1:5432/rai_gateway
EOF
fi

docker rm -f rai-gateway 2>/dev/null || true
docker pull "${GATEWAY_IMAGE}"
docker run -d --restart=always --name rai-gateway \
  -p 0.0.0.0:8000:8000 \
  --env-file "${ENV_FILE}" \
  "${GATEWAY_IMAGE}"

deadline=$((SECONDS + 180))
until curl -sf "http://127.0.0.1:8000/health" >/dev/null; do
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    echo "RaiCentral gateway health check timed out" >&2
    docker logs rai-gateway 2>&1 | tail -50 >&2 || true
    exit 1
  fi
  sleep 3
done
echo "RaiCentral gateway healthy on :8000"