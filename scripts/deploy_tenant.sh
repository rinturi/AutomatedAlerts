#!/bin/bash
# =============================================================================
# deploy_tenant.sh — Deploy AutomatedAlerts for a specific tenant
# Usage: bash scripts/deploy_tenant.sh iitr-banking
#        bash scripts/deploy_tenant.sh acme-corp
# =============================================================================

set -e

TENANT=${1:-iitr-banking}
CONFIG_FILE="tenants/${TENANT}/config.yml"

echo "============================================"
echo " AutomatedAlerts — Deploying tenant: ${TENANT}"
echo "============================================"

# Validate tenant config exists
if [ ! -f "${CONFIG_FILE}" ]; then
  echo "ERROR: Config not found: ${CONFIG_FILE}"
  echo "Available tenants:"
  ls tenants/ 2>/dev/null || echo "  (none)"
  exit 1
fi

echo "[1/4] Validating config..."
python3 - << PYEOF
import yaml, sys
sys.path.insert(0, '.')
from platform.config_schema import validate
with open('${CONFIG_FILE}') as f:
    cfg = yaml.safe_load(f)
try:
    validate(cfg)
    print("  Config valid")
    print(f"  Organisation: {cfg['organisation']['name']}")
    print(f"  Alert source: {cfg['alerts']['connector']}")
    print(f"  Log source:   {cfg['logs']['connector']}")
    print(f"  Issue tracker:{cfg['issues']['connector']}")
    print(f"  LLM:          {cfg['llm']['provider']} / {cfg['llm']['model']}")
    print(f"  Embeddings:   {cfg['embeddings']['provider']} / {cfg['embeddings']['model']}")
except ValueError as e:
    print(f"  INVALID: {e}")
    sys.exit(1)
PYEOF

echo ""
echo "[2/4] Stopping existing containers..."
TENANT=${TENANT} docker compose -f docker-compose.tenant.yml down 2>/dev/null || true

echo ""
echo "[3/4] Building and starting containers..."
TENANT=${TENANT} \
  OPENAI_API_KEY=${OPENAI_API_KEY:-} \
  JIRA_EMAIL=${JIRA_EMAIL:-} \
  JIRA_API_TOKEN=${JIRA_API_TOKEN:-} \
  PAGERDUTY_API_KEY=${PAGERDUTY_API_KEY:-} \
  SNOW_USERNAME=${SNOW_USERNAME:-} \
  SNOW_PASSWORD=${SNOW_PASSWORD:-} \
  docker compose -f docker-compose.tenant.yml up -d --build

echo ""
echo "[4/4] Waiting for services to start..."
sleep 15

echo ""
echo "Checking MCP servers..."
for port in 9001 9002 9003 9004 9005; do
  STATUS=$(curl -s http://localhost:${port}/health \
    | python3 -c "import sys,json; d=json.load(sys.stdin); \
      print(f'  :{port} {d.get(\"service\",\"?\")} [{d.get(\"connector\",d.get(\"status\",\"?\"))}]')" \
    2>/dev/null || echo "  :${port} unreachable")
  echo "${STATUS}"
done

echo ""
echo "============================================"
echo " Tenant '${TENANT}' deployed successfully!"
echo "============================================"
echo ""
echo " Dashboard:  http://$(curl -s ifconfig.me 2>/dev/null || echo 'EC2_IP')"
echo " Grafana:    http://$(curl -s ifconfig.me 2>/dev/null || echo 'EC2_IP'):3000"
echo " Prometheus: http://$(curl -s ifconfig.me 2>/dev/null || echo 'EC2_IP'):9090"
echo ""
echo " Logs: docker compose -f docker-compose.tenant.yml logs -f agents"
