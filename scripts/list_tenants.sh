#!/bin/bash
# =============================================================================
# list_tenants.sh — Show all configured tenants and their connectors
# Usage: bash scripts/list_tenants.sh
# =============================================================================

echo "============================================"
echo " AutomatedAlerts — Configured Tenants"
echo "============================================"
echo ""

if [ ! -d "tenants" ]; then
  echo "No tenants directory found."
  exit 0
fi

for tenant_dir in tenants/*/; do
  tenant=$(basename "${tenant_dir}")
  config="${tenant_dir}config.yml"

  if [ ! -f "${config}" ]; then
    echo "  ${tenant}: (no config.yml)"
    continue
  fi

  python3 - << PYEOF
import yaml
with open('${config}') as f:
    cfg = yaml.safe_load(f)
org  = cfg.get('organisation', {})
print(f"  Tenant:    ${tenant}")
print(f"  Org:       {org.get('name','?')} ({org.get('environment','?')})")
print(f"  Alerts:    {cfg.get('alerts',{}).get('connector','?')}")
print(f"  Logs:      {cfg.get('logs',{}).get('connector','?')}")
print(f"  Issues:    {cfg.get('issues',{}).get('connector','?')}")
print(f"  LLM:       {cfg.get('llm',{}).get('provider','?')} / {cfg.get('llm',{}).get('model','?')}")
print(f"  Embeddings:{cfg.get('embeddings',{}).get('provider','?')} / {cfg.get('embeddings',{}).get('model','?')}")
print("")
PYEOF
done

echo "Deploy a tenant:"
echo "  bash scripts/deploy_tenant.sh iitr-banking"
echo "  bash scripts/deploy_tenant.sh acme-corp"
