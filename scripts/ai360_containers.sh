#!/usr/bin/env bash
# ==============================================================================
# Ai360 Container Management Script
# ==============================================================================
# Manages the VRP AI container stack on compute host Ai360 (rootless Podman):
#   1. vrp-m5-osrm    - OSRM routing engine (Bologna/Modena road graphs)
#   2. vrp-m5-service - FastAPI HTTPS/WSS service with token auth & desktop origins
#   3. vrp-m5-worker  - Supervised learning & search worker with ROCm GPU support
#
# Supports running directly on Ai360 or remotely via:
#   ./scripts/ai360_containers.sh --remote [start|stop|restart|status|test-gpu|logs]
# ==============================================================================
set -euo pipefail

# Remote execution handler
if [[ "${1:-}" == "--remote" ]]; then
  shift
  REMOTE_HOST="${AI360_HOST:-Ai360}"
  REMOTE_CMD="${1:-status}"
  shift || true
  exec ssh -t "${REMOTE_HOST}" "cd /home/benz/myrepo/personal/vrp-ai-bench && ./scripts/ai360_containers.sh ${REMOTE_CMD} $*"
fi

ACTION="${1:-status}"

# Configuration & Paths (can be overridden via environment variables)
REPO_DIR="${VRP_REPO_DIR:-/home/benz/myrepo/personal/vrp-ai-bench}"
CONFIG_DIR="${VRP_CONFIG_DIR:-/home/benz/.config/vrp-ai-m5}"
OSRM_DATA_DIR="${VRP_OSRM_DATA_DIR:-/home/benz/vrp-ai-m5/data/source}"
if [[ ! -d "${OSRM_DATA_DIR}" ]] && [[ -d "${REPO_DIR}/data/source" ]]; then
  OSRM_DATA_DIR="${REPO_DIR}/data/source"
fi

IMAGE="${VRP_IMAGE:-localhost/vrp-ai-m5:local}"
OSRM_IMAGE="${VRP_OSRM_IMAGE:-ghcr.io/project-osrm/osrm-backend@sha256:729461bcc9ae9e6aafa92c0f93db9b060a32e85d5e72092c01ae4a4a9f1eb564}"

HOST_IP="${VRP_HOST_IP:-192.168.178.100}"
SERVER_NAME="${VRP_SERVER_NAME:-Ai360.fritz.box}"
PORT="${VRP_PORT:-8000}"
CLIENT_IP="${VRP_CLIENT_IP:-192.168.178.92}"

# GPU acceleration parameters for AMD ROCm / Strix Point (Radeon 890M / gfx1150)
# Includes --security-opt label=disable required by SELinux on Fedora Core
PODMAN_GPU_FLAGS=(
  --device /dev/kfd
  --device /dev/dri
  --group-add video
  --group-add render
  --ipc host
  --cap-add SYS_PTRACE
  --security-opt seccomp=unconfined
  --security-opt label=disable
)

start_osrm() {
  if podman ps -a --format '{{.Names}}' | grep -q '^vrp-m5-osrm$'; then
    echo "Stopping existing vrp-m5-osrm..."
    podman rm -f vrp-m5-osrm >/dev/null 2>&1 || true
  fi
  echo "Starting vrp-m5-osrm..."
  podman run -d --name vrp-m5-osrm --restart always \
    -p 127.0.0.1:5000:5000 \
    -v "${OSRM_DATA_DIR}:/data:ro" \
    "${OSRM_IMAGE}" \
    osrm-routed --algorithm mld -t 2 /data/nord-est.osrm
}

start_service() {
  if podman ps -a --format '{{.Names}}' | grep -q '^vrp-m5-service$'; then
    echo "Stopping existing vrp-m5-service..."
    podman rm -f vrp-m5-service >/dev/null 2>&1 || true
  fi
  echo "Starting vrp-m5-service (GPU-accelerated, HTTPS)..."
  podman run -d --name vrp-m5-service --network host --restart always \
    "${PODMAN_GPU_FLAGS[@]}" \
    -v "${REPO_DIR}:/app:z" \
    -v "${CONFIG_DIR}:/config:ro" \
    -w /app \
    "${IMAGE}" \
    python -m engine serve \
      --root /app/runs \
      --data-root /app/data \
      --host "${HOST_IP}" \
      --server-name "${SERVER_NAME}" \
      --port "${PORT}" \
      --certfile /config/server.crt \
      --keyfile /config/server.key \
      --token-file /config/token \
      --allowed-origin "http://127.0.0.1:8000" \
      --allowed-client-ip "${CLIENT_IP}"
}

start_worker() {
  if podman ps -a --format '{{.Names}}' | grep -q '^vrp-m5-worker$'; then
    echo "Stopping existing vrp-m5-worker..."
    podman rm -f vrp-m5-worker >/dev/null 2>&1 || true
  fi
  echo "Starting vrp-m5-worker (GPU-accelerated, ROCm)..."
  podman run -d --name vrp-m5-worker --network host --restart always \
    "${PODMAN_GPU_FLAGS[@]}" \
    -v "${REPO_DIR}:/app:z" \
    -w /app \
    "${IMAGE}" \
    python -m engine worker \
      --root /app/runs \
      --data-root /app/data
}

stop_containers() {
  echo "Stopping VRP AI containers..."
  podman rm -f vrp-m5-worker vrp-m5-service vrp-m5-osrm 2>/dev/null || true
  echo "Containers stopped."
}

status_containers() {
  echo "=== Container Status on $(hostname) ==="
  podman ps --filter "name=vrp-m5-*" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
  echo ""
  echo "=== GPU Status (Host) ==="
  if [[ -c /dev/kfd ]] && [[ -e /dev/dri/renderD128 ]]; then
    echo "OK: /dev/kfd and /dev/dri/renderD128 present"
  else
    echo "WARNING: GPU device nodes missing on host"
  fi
}

test_gpu() {
  echo "=== Testing PyTorch ROCm GPU Acceleration ==="
  podman exec vrp-m5-service python -c "
import torch
print('PyTorch Version:   ', torch.__version__)
print('ROCm/HIP Version:  ', getattr(torch.version, 'hip', 'None'))
print('CUDA Available:    ', torch.cuda.is_available())
if torch.cuda.is_available():
    print('Device Name:       ', torch.cuda.get_device_name(0))
    x = torch.randn(1000, 1000, device='cuda')
    y = torch.matmul(x, x)
    print('MatMul (1000x1000): SUCCESS, device =', y.device)
else:
    print('ERROR: PyTorch does not detect GPU!')
    exit(1)
"
  echo ""
  echo "=== Testing Engine GPU Probe ==="
  podman exec vrp-m5-service python -c "
from engine.learn_train import probe_gpu
res = probe_gpu(version=3)
print('probe_gpu(v3):', res)
assert res.get('status') == 'passed', 'probe_gpu failed'
"
  echo "All GPU checks passed successfully!"
}

show_logs() {
  TARGET="${2:-service}"
  case "${TARGET}" in
    osrm)    podman logs -f --tail 50 vrp-m5-osrm ;;
    service) podman logs -f --tail 50 vrp-m5-service ;;
    worker)  podman logs -f --tail 50 vrp-m5-worker ;;
    *)
      echo "Unknown target: ${TARGET}. Use 'osrm', 'service', or 'worker'."
      exit 1
      ;;
  esac
}

case "${ACTION}" in
  start|up)
    start_osrm
    start_service
    start_worker
    echo ""
    status_containers
    ;;
  stop|down)
    stop_containers
    ;;
  restart)
    start_osrm
    start_service
    start_worker
    echo ""
    status_containers
    ;;
  status|ps)
    status_containers
    ;;
  test-gpu|gpu)
    test_gpu
    ;;
  logs)
    show_logs "$@"
    ;;
  *)
    echo "Usage: $0 [--remote] {start|stop|restart|status|test-gpu|logs [osrm|service|worker]}"
    exit 1
    ;;
esac
