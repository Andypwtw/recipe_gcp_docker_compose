#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo ".env not found. Run: bash scripts/bootstrap-env.sh"
  exit 1
fi

set -a
source ./.env
set +a

echo "== docker compose services =="
docker compose --env-file .env ps

echo
echo "== Flask API health =="
curl --fail --silent "http://127.0.0.1:${FLASK_PORT:-5001}/health"
echo

echo
echo "== Kafka topics =="
docker compose --env-file .env exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list

echo
echo "== MongoDB raw_recipes index =="
docker compose --env-file .env exec -T mongodb mongosh \
  --quiet \
  --username "$MONGO_APP_USER" \
  --password "$MONGO_APP_PASSWORD" \
  --authenticationDatabase "$MONGO_DATABASE" \
  "$MONGO_DATABASE" \
  --eval 'printjson(db.raw_recipes.getIndexes())'

echo
echo "== Dynamic Taiwan proxy pool =="
docker compose --env-file .env exec -T proxy-manager python /app/scripts/check_proxy_pool.py || {
  echo "No verified TW proxy is available yet. Direct crawler can still operate."
}

echo
echo "== Airflow connection DAG =="
docker compose --env-file .env exec -T airflow-scheduler airflow dags test test_connections_pipeline "$(date +%F)"
