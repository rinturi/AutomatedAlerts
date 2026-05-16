# Banking Mock — Agentic AI L1 Monitoring System
## IITR Agentic AI Capstone | Team: Raghavendra, Seetharamaiah K, Ravi Chandra C, Annie M, Sayak | April 2026

> **GitHub:** https://github.com/rinturi/banking-mock
> **Live Dashboard:** http://EC2_IP (served via Nginx on port 80)
> **Deployment:** AWS EC2 t2.micro — ap-south-1 (Mumbai)
> **Jira Board:** https://iitr-jiraboard.atlassian.net (Project: KAN)

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Quick Start](#3-quick-start)
4. [Repository Structure](#4-repository-structure)
5. [Code Flow — Step by Step](#5-code-flow--step-by-step)
6. [Component Deep Dive](#6-component-deep-dive)
7. [Complete Port Reference](#7-complete-port-reference)
8. [Environment Variables](#8-environment-variables)
9. [Common Commands](#9-common-commands)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Project Overview

This project automates the complete Level 1 (L1) banking operations monitoring workflow using a multi-agent Agentic AI system deployed across 16 Docker containers on a single AWS t2.micro (1 GB RAM).

Instead of an L1 engineer manually investigating alerts, searching logs, looking up JIRA tickets and applying fixes — three specialised AI agents orchestrated by LangGraph do all of this automatically and display the results on a live dashboard.

### Key Innovation — Dual RAG Architecture

**Classic RAG (Agent 2)** — retrieves resolutions from 30 real Jira defects (KAN-4 to KAN-33) using OpenAI text-embedding-3-small (1536-dim) and ChromaDB cosine similarity, then contextualises with GPT-4o-mini.

**Caching RAG (Agent 1)** — on every subsequent occurrence of the same alert, retrieves the pre-computed resolution from a self-populating cache in ~0.5 seconds using exact ID lookup (similarity=1.0). No Loki query. No Agent 2. No LLM call.

```
First occurrence  (Cache MISS):  ~15-20 seconds — full pipeline
All subsequent    (Cache HIT):   ~2-3 seconds   — cache only  (5-8x faster, $0 LLM cost)
```

### End-to-end flow

```
1.  payment-svc raises PaymentGatewayException (background thread, 12% probability)
2.  Promtail ships structured JSON log to Loki :3100
3.  Prometheus detects error rate spike -> fires PaymentGatewayTimeouts alert
4.  Alertmanager routes alert to grafana-mcp :9001/webhook
5.  Orchestrator polls every 30s -> deduplicates by alert_name -> invokes LangGraph

    [CACHE HIT]  Agent 1 finds resolution in alert-resolution-mcp -> skip to step 9
    [CACHE MISS] Agent 1 extracts root cause from Loki via kibana-mcp

6.  Agent 2 searches vectordb-mcp (OpenAI embeddings, ChromaDB) -> KAN-5
7.  Agent 2 fetches resolution from Jira Cloud via jira-mcp
8.  Agent 2 calls GPT-4o-mini -> llm_summary, llm_confidence, llm_key_action, llm_risk_note
9.  Agent 3 writes incident to dashboard-api :8080
10. Agent 3 populates alert-resolution-mcp cache (MISS only)
11. L1 dashboard :80 shows incident with STILL FIRING/RESOLVED badge + AI summary
```

---

## 2. Architecture

```
LAYER 5  L1 Operations Dashboard
         Nginx :80 -> index.html | polls dashboard-api :8080 every 10s
         STILL FIRING (red pulsing) / RESOLVED Xm ago (green)
         AI Summary | cache_hit badge | JIRA resolution
                    ^
LAYER 4  AGENTIC AI PIPELINE (LangGraph -- agents container)
         Agent 1: Alert Monitor    -- grafana-mcp + alert-resolution-mcp (Caching RAG)
         Agent 2: JIRA Resolver    -- vectordb-mcp + jira-mcp + GPT-4o-mini (Classic RAG)
         Agent 3: Dashboard Writer -- dashboard-api + alert-resolution-mcp (cache write)
                    ^
LAYER 3  MCP SERVERS (FastAPI -- Docker, banking-net network)
         grafana-mcp :9001  kibana-mcp :9002  jira-mcp :9003
         vectordb-mcp :9004  alert-resolution-mcp :9005
                    ^
LAYER 2  OBSERVABILITY STACK (Docker)
         Prometheus :9090  Alertmanager :9093  Grafana :3000
         Loki :3100        Promtail (Docker socket scraping)
                    ^
LAYER 1  MOCK BANKING MICROSERVICES (Docker)
         payment-svc :8001  auth-svc :8002
         loan-svc :8003     notification-svc :8004
```

---

## 3. Quick Start

### Prerequisites
- Docker and Docker Compose v2 (run `setup-aws.sh` on a fresh EC2)
- OpenAI API key with credits
- Jira Cloud account + API token
- Ports 80 and 8080 open in EC2 Security Group (0.0.0.0/0)

### Clone and configure

```bash
git clone https://github.com/rinturi/banking-mock.git
cd ~/banking-mock

# Persist env vars across sessions
echo 'export OPENAI_API_KEY="sk-proj-your-key-here"'  >> ~/.bashrc
echo 'export JIRA_EMAIL="your@email.com"'              >> ~/.bashrc
echo 'export JIRA_API_TOKEN="your-jira-api-token"'     >> ~/.bashrc
source ~/.bashrc
```

### Start the full stack

```bash
OPENAI_API_KEY=$OPENAI_API_KEY \
JIRA_EMAIL=$JIRA_EMAIL \
JIRA_API_TOKEN=$JIRA_API_TOKEN \
  docker compose -f docker-compose.free-tier.yml up -d --build

# Verify all 16 containers running
docker compose -f docker-compose.free-tier.yml ps

# Watch agent logs -- first cycle takes ~20s
docker compose -f docker-compose.free-tier.yml logs -f agents | grep -i "cache\|incident\|error"
```

### Verify after 60 seconds

```bash
# Microservices
for port in 8001 8002 8003 8004; do
  echo "Port $port: $(curl -s http://localhost:$port/health | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["status"])')"
done

# MCP servers (including alert-resolution-mcp :9005)
for port in 9001 9002 9003 9004 9005; do
  echo "Port $port: $(curl -s http://localhost:$port/health | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["status"])')"
done

# Incidents on dashboard
curl -s http://localhost:8080/incidents | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(f'Incidents: {d[\"total\"]}')"

# Cache entries
curl -s http://localhost:9005/health | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
   print(f'Cache entries: {d[\"total_entries\"]}  TTL: {d[\"ttl_days\"]}d')"

# Open dashboard
echo "Dashboard: http://$(curl -s ifconfig.me)"
```

---

## 4. Repository Structure

```
banking-mock/
|
+-- docker-compose.free-tier.yml   <- PRIMARY: 16 containers, Loki (no ELK), t2.micro
+-- docker-compose.yml             <- Full stack with Elasticsearch + Kibana
+-- Dockerfile                     <- Shared base for all 4 microservices
+-- requirements.txt               <- Shared deps (fastapi, prometheus-client)
+-- setup-aws.sh                   <- EC2 bootstrap: Docker + 2GB swap + Nginx + ports
+-- setup.sh                       <- Oracle Cloud bootstrap (iptables firewall)
+-- .gitignore                     <- Excludes .env, __pycache__, chroma_data/
|
+-- shared/
|   +-- log_utils.py               <- JsonFormatter + get_logger() used by all services
|
+-- payment-svc/main.py            <- Mock payment service :8001
+-- auth-svc/main.py               <- Mock auth service :8002
+-- loan-svc/main.py               <- Mock loan service :8003
+-- notification-svc/main.py       <- Mock notification service :8004
|
+-- prometheus/
|   +-- prometheus.yml             <- Scrape config: all 4 services every 10s
|   +-- alert_rules.yml            <- 11 PromQL alert rules across 4 services
|
+-- alertmanager/
|   +-- alertmanager.yml           <- Routes FIRING alerts to grafana-mcp :9001/webhook
|
+-- promtail/
|   +-- promtail.yml               <- Scrapes Docker socket -> ships JSON logs to Loki
|
+-- grafana/provisioning/
|   +-- datasources/prometheus.yml <- Auto-registers Prometheus datasource
|   +-- datasources/loki.yml       <- Auto-registers Loki datasource
|   +-- dashboards/                <- Banking Microservices Live Logs (auto-refresh 10s)
|
+-- mcp-servers/
|   +-- grafana-mcp/
|   |   +-- server.py              <- FastAPI wrapping Alertmanager REST API :9001
|   |   +-- requirements.txt
|   |   +-- Dockerfile
|   |
|   +-- kibana-mcp/
|   |   +-- server.py              <- FastAPI querying Loki HTTP API :9002
|   |   |                             LOG_MODE=loki, _extract_exception_type() regex
|   |   +-- requirements.txt
|   |   +-- Dockerfile
|   |
|   +-- jira-mcp/
|   |   +-- server.py              <- FastAPI wrapping Jira Cloud REST API v3 :9003
|   |   |                             Agile board workaround (JQL returns 410 on free tier)
|   |   +-- jira_store.json        <- Local fallback + vectordb seed (KAN-4 to KAN-33)
|   |   +-- requirements.txt
|   |   +-- Dockerfile
|   |
|   +-- vectordb-mcp/
|   |   +-- server.py              <- FastAPI ChromaDB + OpenAI text-embedding-3-small :9004
|   |   |                             No local model -- pure urllib HTTP to OpenAI API
|   |   +-- jira_store.json        <- 31 defects auto-indexed into ChromaDB on startup
|   |   +-- requirements.txt       <- numpy==1.26.4, chromadb==0.4.24 (no torch)
|   |   +-- Dockerfile             <- Lightweight: ~300MB image (no sentence-transformers)
|   |
|   +-- alert-resolution-mcp/
|       +-- server.py              <- Caching RAG MCP server :9005
|       |                             Exact ID lookup (alert_name::service, similarity=1.0)
|       |                             60-day TTL on last_retrieval_date
|       |                             24-hour background cleanup thread
|       +-- requirements.txt
|       +-- Dockerfile
|
+-- agents/
|   +-- state.py                   <- IncidentState TypedDict (all agents share this)
|   +-- mcp_client.py              <- HTTP client for all MCP calls + ALERT_RES_URL
|   +-- llm_client.py              <- OpenAI GPT-4o-mini client
|   +-- agent_alert_monitor.py     <- Agent 1: cache check -> alert detail -> Loki root cause
|   +-- agent_jira_resolver.py     <- Agent 2: vectordb -> jira -> GPT-4o-mini (MISS only)
|   +-- agent_dashboard_writer.py  <- Agent 3: write dashboard + populate cache (MISS only)
|   +-- orchestrator.py            <- LangGraph graph + polling loop + deduplication
|   +-- requirements.txt           <- langgraph, langchain-core, httpx
|   +-- Dockerfile
|
+-- dashboard/
    +-- api/
    |   +-- main.py                <- FastAPI incident store :8080
    |   |                             POST /incidents (dedup by alert_name::service)
    |   |                             GET  /incidents (browser polls every 10s)
    |   |                             GET  /firing-alerts (proxy to grafana-mcp)
    |   +-- requirements.txt
    |   +-- Dockerfile
    +-- frontend/
        +-- index.html             <- Single-file L1 dashboard served by Nginx :80
                                      Dark theme | polls every 10s
                                      STILL FIRING (red) / RESOLVED (green) badges
                                      AI Summary | cache_hit score badge | JIRA resolution
```

---

## 5. Code Flow — Step by Step

### Step 1: Microservice generates error + Loki receives log

```
payment-svc/main.py
  -> Background thread every 0.5s
  -> 12% probability: raises PaymentGatewayException
  -> Increments: payment_errors_total{error_type="gateway_timeout"}
  -> Writes JSON log to stdout:
     {"@timestamp":"...","level":"ERROR","service":"payment-svc",
      "message":"PaymentGatewayException: Upstream gateway timeout after 30000ms - ..."}

Promtail
  -> Scrapes Docker socket every 5s
  -> Discovers payment-svc container
  -> Ships to: POST http://loki:3100/loki/api/v1/push

prometheus/alert_rules.yml
  -> PaymentGatewayTimeouts:
     expr: increase(payment_errors_total{error_type="gateway_timeout"}[5m]) > 3
     for:  1m
     INACTIVE -> PENDING (condition true) -> FIRING (after 60s)

alertmanager/alertmanager.yml
  -> Receives FIRING from Prometheus
  -> Groups by service label
  -> POST http://grafana-mcp:9001/webhook
  -> grafana-mcp stores alert with MD5 hash alert_id
```

### Step 2: Orchestrator starts pipeline cycle

```
agents/orchestrator.py  (every 30 seconds)
  -> GET http://grafana-mcp:9001/tools/list_active_alerts
  -> Deduplicate by alert_name (one pipeline per unique name per cycle)
  -> For each unique alert:
       state = empty_state()  # IncidentState with cache_hit=None
       state["alert_name"] = "PaymentGatewayTimeouts"
       state["service"]    = "payment-svc"
       graph.invoke(state)
```

### Step 3: Agent 1 — Alert Monitor (cache check first)

```
agents/agent_alert_monitor.py

Step 1: GET grafana-mcp:9001/tools/get_alert_detail/<alert_id>
        -> _normalise_timestamp("2026-04-26 01:03:47 UTC")
              = "2026-04-26T01:03:47+00:00"  (Loki requires ISO 8601)

Step 2: GET alert-resolution-mcp:9005/tools/search_alert_resolution
        ?alert_name=PaymentGatewayTimeouts&service=payment-svc

        [CACHE HIT - similarity=1.0, exact ID: PaymentGatewayTimeouts::payment-svc]
          state["error_exception"]      = "PaymentGatewayException"
          state["error_message"]        = ""  (shown via error_exception)
          state["jira_issue_id"]        = "KAN-5"
          state["resolution"]           = cached llm_key_action
          state["resolution_found"]     = True
          state["jira_status"]          = "Done"
          state["llm_summary"]          = cached summary
          state["llm_confidence"]       = "high"
          state["llm_key_action"]       = "Update NetworkPolicy for egress"
          state["llm_risk_note"]        = "Verify no other services affected"
          state["cache_hit"]            = True
          state["vector_match_quality"] = "cache_hit"
          state["vector_top_score"]     = 1.0
          last_retrieval_date refreshed in cache (TTL reset to 60 more days)
          RETURN state  (Agent 2 skipped entirely)

        [CACHE MISS - not found or TTL expired]
          state["cache_hit"] = False
          -> proceed to Step 3

Step 3: [MISS only] GET kibana-mcp:9002/tools/extract_root_cause
        ?service=payment-svc&alert_name=PaymentGatewayTimeouts&window_minutes=5
        -> kibana-mcp queries Loki LogQL:
           {service="payment-svc"} |= "ERROR" within +-5min of alert timestamp
        -> _extract_exception_type("PaymentGatewayException: Upstream...")
              = "PaymentGatewayException"  (regex: ^([A-Z][a-zA-Z]+Exception))
        state["error_exception"]  = "PaymentGatewayException"
        state["error_message"]    = full log line
        state["error_recurrence"] = 9
        state["cache_hit"]        = False
```

### Step 4: LangGraph routing

```
agents/orchestrator.py -- route_after_agent1(state)

  cache_hit == True            -> "cache_hit"   -> agent_3 (Agent 2 skipped)
  cache_hit == False           -> "continue"    -> agent_2 (full pipeline)
  pipeline_error in hard_stops -> "skip_to_end" -> END

Graph edges:
  START    -> agent_1
  agent_1  -[cache_hit]->   agent_3 -> END
  agent_1  -[continue]->    agent_2 -> agent_3 -> END
  agent_1  -[skip_to_end]-> END
```

### Step 5: Agent 2 — JIRA Resolver (MISS only)

```
agents/agent_jira_resolver.py

Stage 1: GET vectordb-mcp:9004/tools/embed_and_search
         ?query=PaymentGatewayException: Upstream gateway timeout...
         &top_k=5&service_filter=payment-svc
         -> POST https://api.openai.com/v1/embeddings (text-embedding-3-small, 1536-dim)
         -> ChromaDB cosine similarity across 31 indexed defects
         -> Returns: {issue_id="KAN-5", score=0.71, match_quality="medium"}

Stage 2: GET jira-mcp:9003/tools/get_issue/KAN-5
         -> Jira Cloud: GET https://iitr-jiraboard.atlassian.net/rest/api/3/issue/KAN-5
         -> state["jira_issue_id"]   = "KAN-5"
         -> state["resolution_found"] = True

Stage 3: llm_client.summarise_resolution(...)
         -> POST https://api.openai.com/v1/chat/completions (gpt-4o-mini)
         -> Prompt: current error context + KAN-5 resolution text
         -> Returns: {llm_summary, llm_confidence="high",
                      llm_key_action, llm_risk_note}

Stage 4 (fallback if no vectordb match):
         -> jira-mcp search_issues() keyword match
         -> If still no match: jira-mcp create_issue() -> real KAN-40+ ticket
```

### Step 6: Agent 3 — Dashboard Writer

```
agents/agent_dashboard_writer.py

Step 1: POST http://dashboard-api:8080/incidents
        -> dashboard-api deduplicates: removes old entry for same alert_name::service
        -> state["incident_id"]      = "E6724885"
        -> state["dashboard_status"] = "written"

Step 2: [MISS only] POST alert-resolution-mcp:9005/tools/index_alert_resolution
        -> resolution = state["llm_key_action"]  (clean actionable text, not raw Jira)
        -> Stores: alert_name, service, exception_type, jira_issue_id,
                   llm_summary, llm_confidence, llm_key_action, llm_risk_note
        -> last_retrieval_date = now()   (60-day TTL starts here)
        -> occurrence_count = 1
        -> Log: "Agent 3: Alert resolution cached - action=created occurrences=1"
```

### Step 7: L1 Dashboard renders incident

```
dashboard/frontend/index.html
  -> Polls GET http://EC2_IP:8080/incidents every 10s
  -> Polls GET http://EC2_IP:8080/firing-alerts every 10s
       /firing-alerts proxies to grafana-mcp -> returns Set of FIRING alert names

  Renders incident card:
  +------------------------------------------------------------------+
  | PaymentGatewayTimeouts   payment-svc   critical                  |
  | [RED PULSING] STILL FIRING                                       |
  | Exception: PaymentGatewayException                               |
  | Semantic match: cache_hit (score: 1.000)                         |
  | JIRA: KAN-5                                                      |
  |                                                                  |
  | [AI] HIGH                                                        |
  | The current incident involves a PaymentGatewayException...       |
  | First action: Update NetworkPolicy for egress...                 |
  | Risk: Verify no other services affected...                       |
  +------------------------------------------------------------------+

  When alert resolves:  [GREEN] RESOLVED - 3m ago
```

---

## 6. Component Deep Dive

### 6.1 IncidentState

```python
# agents/state.py
class IncidentState(TypedDict):
    # Alert (Orchestrator + Agent 1)
    alert_id:          Optional[str]
    alert_name:        Optional[str]    # e.g. "PaymentGatewayTimeouts"
    alert_timestamp:   Optional[str]    # ISO 8601
    alert_severity:    Optional[str]    # "critical" / "warning"
    alert_summary:     Optional[str]
    service:           Optional[str]    # e.g. "payment-svc"

    # Root cause (Agent 1 -- from Loki or cache)
    error_message:     Optional[str]    # full log line (empty on cache HIT)
    error_exception:   Optional[str]    # e.g. "PaymentGatewayException"
    error_timestamp:   Optional[str]
    error_recurrence:  Optional[int]

    # Vector search (Agent 2)
    vector_match_quality: Optional[str]   # high/medium/low/none/cache_hit
    vector_top_score:     Optional[float] # 0.0-1.0 (1.0 on cache HIT)
    vector_top_issue_id:  Optional[str]

    # JIRA (Agent 2 or cache)
    jira_issue_id:     Optional[str]    # e.g. "KAN-5"
    jira_summary:      Optional[str]
    jira_status:       Optional[str]    # "Done" / "To Do"
    resolution:        Optional[str]    # llm_key_action stored here
    resolution_steps:  Optional[list]
    resolution_found:  Optional[bool]

    # LLM (Agent 2 GPT-4o-mini or cached)
    llm_summary:       Optional[str]
    llm_confidence:    Optional[str]    # high / medium / low
    llm_key_action:    Optional[str]    # first step for L1 engineer
    llm_risk_note:     Optional[str]    # risk before applying fix

    # Dashboard (Agent 3)
    incident_id:       Optional[str]
    dashboard_status:  Optional[str]    # "written" / "error"
    remark:            Optional[str]

    # Pipeline metadata
    pipeline_start:    Optional[str]
    pipeline_error:    Optional[str]
    retry_count:       Optional[int]
    cache_hit:         Optional[bool]   # True = Agent 2 was skipped entirely
```

### 6.2 LangGraph Graph

```python
# agents/orchestrator.py
graph = StateGraph(IncidentState)

graph.add_node("agent_1", agent1.run)
graph.add_node("agent_2", agent2.run)
graph.add_node("agent_3", agent3.run)

graph.add_edge(START, "agent_1")

graph.add_conditional_edges("agent_1", route_after_agent1, {
    "cache_hit":   "agent_3",   # HIT: skip Agent 2
    "continue":    "agent_2",   # MISS: full pipeline
    "skip_to_end": END,         # no alerts / grafana down
})

graph.add_edge("agent_2", "agent_3")
graph.add_edge("agent_3", END)

compiled_graph = graph.compile()
```

### 6.3 MCP Server Endpoints

**grafana-mcp :9001**
```
GET  /tools/list_active_alerts?severity=critical&limit=20
GET  /tools/get_alert_detail/{alert_id}
POST /webhook  <- Alertmanager sends here
```

**kibana-mcp :9002** (LOG_MODE=loki)
```
GET  /tools/extract_root_cause?service=payment-svc&alert_name=PaymentGatewayTimeouts&window_minutes=5
GET  /tools/search_logs?service=auth-svc&level=ERROR&limit=20
GET  /tools/get_error_summary?service=notification-svc
```

**jira-mcp :9003** (Jira Cloud)
```
GET  /tools/search_issues?query=PaymentGatewayException&service=payment-svc
GET  /tools/get_issue/KAN-5
POST /tools/create_issue  {summary, description, service, priority}
```

**vectordb-mcp :9004** (ChromaDB + OpenAI)
```
GET  /tools/embed_and_search?query=PaymentGatewayException+timeout&top_k=5
POST /tools/index_document
GET  /tools/get_collection_info
```

**alert-resolution-mcp :9005** (Caching RAG)
```
GET    /tools/search_alert_resolution?alert_name=PaymentGatewayTimeouts&service=payment-svc
POST   /tools/index_alert_resolution
GET    /tools/list_cached_resolutions
DELETE /tools/delete_resolution/{alert_name}/{service}
```

### 6.4 vectordb-mcp — OpenAI Embeddings (No Local Model)

Current version uses OpenAI API via urllib -- no torch, no sentence-transformers:

```python
def _openai_embed(texts: list) -> list:
    payload = json.dumps({"model": "text-embedding-3-small", "input": texts}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return [item["embedding"] for item in data["data"]]
```

Dockerfile is lightweight -- no torch (~300MB image vs ~2.5GB before):
```
numpy==1.26.4     # chromadb 0.4.24 uses np.float_ removed in numpy 2.0
chromadb==0.4.24
overrides==7.4.0
fastapi uvicorn pydantic
```

### 6.5 alert-resolution-mcp — Caching RAG

```python
# Cache entry structure
{
    "id":                  "PaymentGatewayTimeouts::payment-svc",
    "alert_name":          "PaymentGatewayTimeouts",
    "service":             "payment-svc",
    "exception_type":      "PaymentGatewayException",
    "jira_issue_id":       "KAN-5",
    "llm_summary":         "The current incident involves...",
    "llm_confidence":      "high",
    "llm_key_action":      "Update NetworkPolicy for egress",
    "llm_risk_note":       "Verify no other services affected",
    "first_seen":          "2026-04-26T01:03:47+00:00",  # never changes
    "last_retrieval_date": "2026-04-26T01:31:16+00:00",  # updated on every HIT
    "occurrence_count":    18,                             # incremented on every HIT
    "ttl_days":            60
}

# Exact ID lookup -- always similarity=1.0
doc_id   = f"{alert_name}::{service}"
existing = _collection.get(ids=[doc_id], include=["metadatas"])

# TTL: background thread every 24h deletes entries where
# last_retrieval_date < now - TTL_DAYS
# Uses last_retrieval_date (not created_at) so active entries never expire
```

### 6.6 Real Jira Cloud Integration

```
Site:        https://iitr-jiraboard.atlassian.net
Project:     KAN (Banking Issues)
Defects:     KAN-4 to KAN-33 (30 real defects, labelled by service + exception type)
New tickets: KAN-34+ (auto-created by Agent 2 for unknown alerts)

JQL workaround: Jira free tier returns HTTP 410 for /rest/api/3/search
Fix: GET /rest/agile/1.0/board/2/issue -> fetch all, filter in memory by labels
```

### 6.7 Grafana Loki — Real Log Collection

```yaml
# promtail/promtail.yml
clients:
  - url: http://loki:3100/loki/api/v1/push
scrape_configs:
  - job_name: banking-containers
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        filters:
          - name: name
            values: [payment-svc, auth-svc, loan-svc, notification-svc]
    pipeline_stages:
      - json:
          expressions: {level: level, message: message}
      - labels: {level:}
```

Exception extraction from message field (exception_type is not a separate JSON field):
```python
def _extract_exception_type(message: str) -> str:
    import re
    match = re.match(r'^([A-Z][a-zA-Z]+Exception|[A-Z][a-zA-Z]+Error)', message)
    return match.group(1) if match else ""
```

---

## 7. Complete Port Reference

| Port | Container | Service | Purpose |
|------|-----------|---------|---------|
| 22 | OS | SSH | Remote access |
| 80 | nginx (host OS) | L1 Dashboard | Serves index.html -- open to 0.0.0.0/0 |
| 3000 | grafana | Grafana UI | Dashboards + Loki Explore (admin/bankadmin123) |
| 3100 | loki | Grafana Loki | Log storage API (internal only) |
| 8001 | payment-svc | Payment Service | Mock banking payment + /metrics |
| 8002 | auth-svc | Auth Service | Mock banking auth + /metrics |
| 8003 | loan-svc | Loan Service | Mock banking loans + /metrics |
| 8004 | notification-svc | Notification Service | Mock notifications + /metrics |
| 8080 | dashboard-api | Dashboard API | POST /incidents + GET /incidents + GET /firing-alerts -- open to 0.0.0.0/0 |
| 9001 | grafana-mcp | grafana-mcp | Alertmanager wrapper -- Agent 1 |
| 9002 | kibana-mcp | kibana-mcp | Loki log search -- Agent 1 |
| 9003 | jira-mcp | jira-mcp | Jira Cloud REST -- Agent 2 |
| 9004 | vectordb-mcp | vectordb-mcp | ChromaDB + OpenAI embeddings -- Agent 2 |
| 9005 | alert-resolution-mcp | alert-resolution-mcp | Caching RAG + 60-day TTL -- Agent 1 + Agent 3 |
| 9090 | prometheus | Prometheus | Metrics + 11 alert rules |
| 9093 | alertmanager | Alertmanager | Alert routing -> grafana-mcp webhook |

---

## 8. Environment Variables

### Set on EC2 host (persisted in ~/.bashrc)

```bash
echo 'export OPENAI_API_KEY="sk-proj-your-key-here"'  >> ~/.bashrc
echo 'export JIRA_EMAIL="your@email.com"'              >> ~/.bashrc
echo 'export JIRA_API_TOKEN="your-jira-api-token"'     >> ~/.bashrc
source ~/.bashrc
```

### agents container

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | Required | OpenAI API key |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI model for contextualisation |
| `LLM_ENABLED` | `true` | Set `false` to disable LLM in test mode |
| `LLM_MAX_TOKENS` | `1024` | Max tokens per LLM response |
| `GRAFANA_MCP_URL` | `http://grafana-mcp:9001` | grafana-mcp URL |
| `KIBANA_MCP_URL` | `http://kibana-mcp:9002` | kibana-mcp URL |
| `JIRA_MCP_URL` | `http://jira-mcp:9003` | jira-mcp URL |
| `VECTORDB_MCP_URL` | `http://vectordb-mcp:9004` | vectordb-mcp URL |
| `ALERT_RESOLUTION_MCP_URL` | `http://alert-resolution-mcp:9005` | Caching RAG URL |
| `DASHBOARD_API_URL` | `http://dashboard-api:8080` | Dashboard API URL |
| `POLL_INTERVAL_SECONDS` | `30` | Seconds between pipeline cycles |
| `MCP_TIMEOUT` | `30` | HTTP timeout for MCP calls (seconds) |

### vectordb-mcp + alert-resolution-mcp containers

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | Required | For text-embedding-3-small API calls |
| `EMBED_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `HIGH_THRESHOLD` | `0.75` | Cosine similarity threshold (vectordb) |
| `MEDIUM_THRESHOLD` | `0.50` | Cosine similarity threshold (vectordb) |
| `TTL_DAYS` | `60` | Cache TTL in days (alert-resolution-mcp) |
| `CLEANUP_INTERVAL_HOURS` | `24` | Cleanup thread interval (alert-resolution-mcp) |

### jira-mcp container

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRA_URL` | Required | e.g. `https://iitr-jiraboard.atlassian.net` |
| `JIRA_EMAIL` | Required | Atlassian account email |
| `JIRA_API_TOKEN` | Required | API token from id.atlassian.com |
| `JIRA_PROJECT` | `KAN` | Jira project key |

---

## 9. Common Commands

### Start / Stop

```bash
# Start all 16 containers
OPENAI_API_KEY=$OPENAI_API_KEY \
JIRA_EMAIL=$JIRA_EMAIL \
JIRA_API_TOKEN=$JIRA_API_TOKEN \
  docker compose -f docker-compose.free-tier.yml up -d

# Rebuild + restart one service after code change
docker compose -f docker-compose.free-tier.yml stop agents
docker compose -f docker-compose.free-tier.yml rm -f agents
docker compose -f docker-compose.free-tier.yml build --no-cache agents
docker compose -f docker-compose.free-tier.yml up -d agents

# Stop all
docker compose -f docker-compose.free-tier.yml down

# Restart one service
docker compose -f docker-compose.free-tier.yml restart payment-svc
```

### Monitoring

```bash
# All container status
docker compose -f docker-compose.free-tier.yml ps --format "table {{.Name}}\t{{.Status}}"

# Watch cache HIT / MISS in agent logs
docker compose -f docker-compose.free-tier.yml logs -f agents | grep -i "cache"

# Memory usage (target: stay under 950MB)
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}" | sort -k2 -rh

# Loki LogQL (in Grafana Explore :3000)
{service="payment-svc"} |= "ERROR"
{service="payment-svc"} | json | exception_type="PaymentGatewayException"
{container=~"payment-svc|auth-svc|loan-svc|notification-svc"} |= "ERROR"
```

### Test MCP servers

```bash
# grafana-mcp
curl -s http://localhost:9001/tools/list_active_alerts | python3 -m json.tool

# kibana-mcp -- extract root cause from real Loki logs
curl -s "http://localhost:9002/tools/extract_root_cause?service=payment-svc&alert_name=PaymentGatewayTimeouts" \
  | python3 -m json.tool

# jira-mcp -- real Jira Cloud issue
curl -s http://localhost:9003/tools/get_issue/KAN-5 | python3 -m json.tool

# vectordb-mcp -- OpenAI 1536-dim semantic search
curl -s "http://localhost:9004/tools/embed_and_search?query=PaymentGatewayException+gateway+timeout&top_k=3" \
  | python3 -m json.tool

# alert-resolution-mcp -- cache status
curl -s http://localhost:9005/tools/list_cached_resolutions | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'Cached: {d[\"total\"]}  TTL: {d[\"ttl_days\"]}d')
for e in d['entries']:
    print(f'  {e[\"doc_id\"]:<45} occ={e[\"occurrence_count\"]:>3}  expires={e[\"expires_in_days\"]}d  jira={e[\"jira_issue_id\"]}')"

# dashboard-api
curl -s http://localhost:8080/incidents | python3 -c "
import sys,json; d=json.load(sys.stdin)
for i in d.get('incidents',[]):
    print(f'{i[\"alert_name\"]:<35} jira={i[\"jira_issue_id\"]}  cache={i.get(\"vector_match_quality\")}  llm={bool(i.get(\"llm_summary\"))}')"
```

### Manage cache entries

```bash
# Delete a specific cache entry (forces fresh resolution on next occurrence)
python3 - << 'EOF'
import urllib.request, urllib.parse, json
base  = "http://localhost:9005"
alert = "PaymentGatewayTimeouts"
svc   = "payment-svc"
url   = f"{base}/tools/delete_resolution/{urllib.parse.quote(alert)}/{urllib.parse.quote(svc)}"
resp  = urllib.request.urlopen(urllib.request.Request(url, method="DELETE"))
print(json.loads(resp.read()))
EOF

# Delete ALL cache entries (full reset)
python3 - << 'EOF'
import urllib.request, urllib.parse, json
base = "http://localhost:9005"
data = json.loads(urllib.request.urlopen(f"{base}/tools/list_cached_resolutions").read())
for e in data.get("entries", []):
    a = e["doc_id"].split("::")[0]
    s = e["doc_id"].split("::")[1]
    url = f"{base}/tools/delete_resolution/{urllib.parse.quote(a)}/{urllib.parse.quote(s)}"
    urllib.request.urlopen(urllib.request.Request(url, method="DELETE"))
    print(f"Deleted: {e['doc_id']}")
EOF
```

### Force alerts to fire (testing)

```bash
# Trigger payment errors
for i in {1..30}; do
  curl -s -X POST http://localhost:8001/payments/process \
    -H 'Content-Type: application/json' \
    -d '{"payment_id":"TEST001","amount":1000,"merchant_id":"MERCHANT_TEST"}' > /dev/null
done

# Trigger auth brute force
for i in {1..20}; do
  curl -s -X POST http://localhost:8002/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"user_id":"USER00001","password":"wrong","client_ip":"10.0.0.1"}' > /dev/null
done
```

### Nginx and Dashboard

```bash
# Check Nginx status
sudo systemctl status nginx

# Redeploy dashboard HTML (after changes to index.html)
sudo cp ~/banking-mock/dashboard/frontend/index.html /var/www/html/index.html

# EC2 restart checklist (Elastic IP assigned -- no IP change needed)
cd ~/banking-mock
OPENAI_API_KEY=$OPENAI_API_KEY \
JIRA_EMAIL=$JIRA_EMAIL \
JIRA_API_TOKEN=$JIRA_API_TOKEN \
  docker compose -f docker-compose.free-tier.yml up -d
sudo systemctl start nginx

# Verify dashboard is serving
curl -s http://localhost/ | grep -c "dashboard" && echo "Dashboard OK"
```

---

## 10. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Cache always MISS | alert-resolution-mcp not reachable from agents container | `docker exec agents python3 -c "import urllib.request; urllib.request.urlopen('http://alert-resolution-mcp:9005/health')"` |
| exception_type empty in cache / dashboard | Old cache entry before kibana-mcp fix | Delete stale entry -- see "Manage cache entries" commands above |
| error_message shows raw Jira description | Old cache entry stores full Jira text instead of llm_key_action | Delete stale entry and let MISS re-populate (now stores llm_key_action) |
| resolution_found=None on cache HIT | Old agent code before fix | Rebuild agents: `docker compose build --no-cache agents` |
| Jira search returns HTTP 410 | JQL /search blocked on Jira free tier | Already fixed: uses GET /rest/agile/1.0/board/2/issue + in-memory filter |
| vectordb-mcp startup freeze (old issue) | all-MiniLM-L6-v2 loading 200MB into RAM | Fixed: current version uses OpenAI API, no local model. Rebuild: `docker compose build --no-cache vectordb-mcp` |
| numpy.float_ AttributeError | numpy 2.0 removed np.float_ | Pin numpy==1.26.4 in Dockerfile before chromadb |
| posthog.capture TypeError | chromadb 0.5.x telemetry bug | Pin chromadb==0.4.24 + overrides==7.4.0 |
| Dashboard STILL FIRING after alert resolved | Normal -- /firing-alerts polls every 10s | Wait up to 10 seconds |
| agents container exits immediately | Syntax error or import error | `docker compose up agents` (without -d) to see error |
| OOM kills -- containers dying | Startup memory spike | Start in order: observability -> microservices -> MCP -> vectordb-mcp last |
| Promtail timestamp too old errors | Old Docker logs outside Loki ingestion window | Harmless -- only affects old logs; new real-time logs accepted fine |
| SSH hangs | Security Group rule uses WSL2 internal IP | Add SG rules from AWS Console browser, not WSL2 terminal |
| docker: command not found | Docker group not refreshed | Exit and reconnect: `exit` then `ssh banking-vm` |
| PPTX opens with repair prompt | Non-ASCII chars (emoji, em dash) in PPTX XML | Post-process: replace all emoji with ASCII text equivalents |

---

## Dataset / Knowledge Base

**Real Jira Defect Store** (Jira Cloud: iitr-jiraboard.atlassian.net + local fallback)
30 banking defects -- KAN-4 to KAN-33:
- `payment-svc` KAN-4 to KAN-11: DB pool, gateway timeout, duplicate tx, insufficient funds, card expiry
- `auth-svc` KAN-12 to KAN-19: Redis down, brute force, LDAP timeout, JWT failure, token race
- `loan-svc` KAN-20 to KAN-27: CIBIL timeout, risk engine crash, EMI overflow, KYC mismatch, OCR
- `notification-svc` KAN-28 to KAN-33: SMS gateway, SMTP cert, Kafka overflow, template, FCM

**Caching RAG Store** (alert-resolution-mcp :9005)
Auto-populated by Agent 3 after every successful MISS pipeline run.
Grows with every unique alert type. Exact ID lookup: `alert_name::service`.
60-day TTL based on `last_retrieval_date` -- active entries never expire.

---

*IITR Agentic AI Capstone -- Raghavendra, Seetharamaiah K, Ravi Chandra C, Annie M, Sayak -- April 2026*
*GitHub: https://github.com/rinturi/banking-mock*
