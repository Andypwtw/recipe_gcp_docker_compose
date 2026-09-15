#!/usr/bin/env bash
set -euo pipefail

for id in 1 2 3 4; do
  container="crawler-worker-${id}"
  echo "=== ${container} ==="

  docker compose exec -T "${container}" \
    python - <<'PY'
import os
import requests
from crawler.worker import create_source_bound_session

ip = os.environ["PRIVATE_IP"]
session = create_source_bound_session(ip)
try:
    r = session.get(
        "https://ifconfig.me/ip",
        timeout=10,
    )
    r.raise_for_status()
    print(
        f"private={ip} public={r.text.strip()}"
    )
finally:
    session.close()
PY
done
