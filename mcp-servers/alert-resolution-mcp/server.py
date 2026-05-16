"""
alert-resolution-mcp — Caching RAG server for Agent 1.

Stores proven alert resolutions from past pipeline runs.
Agent 1 queries this first — on HIT skips Agent 2 entirely.
Agent 3 populates this after every successful resolution.
TTL-based auto-delete: entries not retrieved for TTL_DAYS are removed.
"""

import os
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import chromadb
from chromadb import Settings

# ── Config ────────────────────────────────────────────────────────────────────
MCP_PORT        = int(os.getenv("MCP_PORT", "9005"))
CHROMA_PATH     = os.getenv("CHROMA_PATH", "/app/alert_cache")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "alert_resolutions")
TTL_DAYS        = int(os.getenv("TTL_DAYS", "60"))
CLEANUP_INTERVAL= int(os.getenv("CLEANUP_INTERVAL_HOURS", "24")) * 3600
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "text-embedding-3-small")

# ── OpenAI embed via urllib (no openai package needed) ────────────────────────
def _openai_embed(texts: list) -> list:
    import urllib.request
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise Exception(f"OpenAI API error: {data['error']['message']}")
    return [item["embedding"] for item in data["data"]]

# ── ChromaDB setup ─────────────────────────────────────────────────────────────
Path(CHROMA_PATH).mkdir(parents=True, exist_ok=True)
_settings   = Settings(anonymized_telemetry=False, allow_reset=True)
_client     = chromadb.PersistentClient(path=CHROMA_PATH, settings=_settings)
_collection = _client.get_or_create_collection(name=COLLECTION_NAME)
print(f"[alert-resolution-mcp] Collection '{COLLECTION_NAME}' ready. "
      f"Entries: {_collection.count()}")

# ── TTL cleanup thread ─────────────────────────────────────────────────────────
def _cleanup_stale_entries():
    """Background thread — runs every CLEANUP_INTERVAL seconds.
    Deletes entries where last_retrieval_date is older than TTL_DAYS.
    """
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            cutoff = (datetime.now(timezone.utc) -
                      timedelta(days=TTL_DAYS)).isoformat()
            all_entries = _collection.get(include=["metadatas"])
            stale_ids   = []
            for doc_id, meta in zip(
                all_entries.get("ids", []),
                all_entries.get("metadatas", [])
            ):
                last_retrieval = meta.get("last_retrieval_date", "")
                if last_retrieval and last_retrieval < cutoff:
                    stale_ids.append(doc_id)
            if stale_ids:
                _collection.delete(ids=stale_ids)
                print(f"[alert-resolution-mcp] TTL cleanup: deleted "
                      f"{len(stale_ids)} stale entries "
                      f"(last_retrieval_date older than {TTL_DAYS} days): "
                      f"{stale_ids}")
            else:
                print(f"[alert-resolution-mcp] TTL cleanup: no stale entries found")
        except Exception as e:
            print(f"[alert-resolution-mcp] Cleanup error: {e}")

_cleanup_thread = threading.Thread(target=_cleanup_stale_entries, daemon=True)
_cleanup_thread.start()
print(f"[alert-resolution-mcp] TTL cleanup thread started "
      f"(TTL={TTL_DAYS} days, interval={CLEANUP_INTERVAL//3600}h)")

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="alert-resolution-mcp")

# ── Request models ─────────────────────────────────────────────────────────────
class IndexResolutionRequest(BaseModel):
    alert_name:      str
    service:         str
    exception_type:  Optional[str] = ""
    resolution:      Optional[str] = ""
    jira_issue_id:   Optional[str] = ""
    llm_summary:     Optional[str] = ""
    llm_confidence:  Optional[str] = ""
    llm_key_action:  Optional[str] = ""
    llm_risk_note:   Optional[str] = ""

# ── Helper — stable ID per alert+service ──────────────────────────────────────
def _make_id(alert_name: str, service: str) -> str:
    return f"{alert_name}::{service}"

def _make_text(alert_name: str, service: str,
               exception_type: str, resolution: str) -> str:
    return f"{alert_name} {service} {exception_type} {resolution}"

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":          "up",
        "service":         "alert-resolution-mcp",
        "collection":      COLLECTION_NAME,
        "total_entries":   _collection.count(),
        "ttl_days":        TTL_DAYS,
        "cleanup_interval_hours": CLEANUP_INTERVAL // 3600,
        "embed_model":     EMBED_MODEL,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }

@app.get("/tools/search_alert_resolution")
async def search_alert_resolution(
    alert_name: str,
    service:    str,
    threshold:  float = 0.75,
):
    """
    Agent 1 calls this first.
    Returns cached resolution if found and not expired.
    Also updates last_retrieval_date on hit to reset TTL.
    """
    if _collection.count() == 0:
        return {"tool": "search_alert_resolution", "hit": False,
                "reason": "cache empty"}

    # ── Exact ID lookup first (alert_name::service is always unique) ──────────
    doc_id   = _make_id(alert_name, service)
    existing = _collection.get(ids=[doc_id], include=["metadatas", "documents"])

    if existing["ids"]:
        meta       = existing["metadatas"][0]
        similarity = 1.0  # exact match
    else:
        # ── Fallback: semantic similarity search ──────────────────────────────
        query_text = f"{alert_name} {service}"
        try:
            embedding = _openai_embed([query_text])
            results   = _collection.query(
                query_embeddings=embedding,
                n_results=1,
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            return {"tool": "search_alert_resolution", "hit": False,
                    "reason": f"query failed: {e}"}

        ids       = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if not ids:
            return {"tool": "search_alert_resolution", "hit": False,
                    "reason": "no results"}

        similarity = max(0.0, 1.0 - distances[0])
        meta       = metadatas[0]

        if similarity < threshold:
            return {"tool": "search_alert_resolution", "hit": False,
                    "similarity": round(similarity, 4),
                    "reason": f"similarity {similarity:.3f} below threshold {threshold}"}

    # ── Cache HIT — update last_retrieval_date to reset TTL ──────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    meta["last_retrieval_date"] = now_iso
    meta["occurrence_count"]    = meta.get("occurrence_count", 1) + 1
    try:
        doc_id    = ids[0]
        doc_text  = results.get("documents", [[]])[0][0]
        emb       = _openai_embed([doc_text])
        _collection.update(
            ids=[doc_id],
            embeddings=emb,
            metadatas=[meta]
        )
    except Exception as e:
        print(f"[alert-resolution-mcp] Warning: could not update last_retrieval_date: {e}")

    return {
        "tool":             "search_alert_resolution",
        "hit":              True,
        "similarity":       round(similarity, 4),
        "alert_name":       meta.get("alert_name"),
        "service":          meta.get("service"),
        "exception_type":   meta.get("exception_type", ""),
        "jira_issue_id":    meta.get("jira_issue_id", ""),
        "resolution":       meta.get("resolution", ""),
        "llm_summary":      meta.get("llm_summary", ""),
        "llm_confidence":   meta.get("llm_confidence", ""),
        "llm_key_action":   meta.get("llm_key_action", ""),
        "llm_risk_note":    meta.get("llm_risk_note", ""),
        "last_retrieval_date": now_iso,
        "occurrence_count": meta.get("occurrence_count", 1),
        "first_seen":       meta.get("first_seen", ""),
        "source":           "alert_resolution_cache",
    }

@app.post("/tools/index_alert_resolution")
async def index_alert_resolution(req: IndexResolutionRequest):
    """
    Agent 3 calls this after every successful pipeline run.
    Creates new entry or updates existing one.
    Sets last_retrieval_date = now (TTL starts from here).
    """
    doc_id   = _make_id(req.alert_name, req.service)
    doc_text = _make_text(
        req.alert_name, req.service,
        req.exception_type or "", req.resolution or ""
    )
    now_iso  = datetime.now(timezone.utc).isoformat()

    # Check if entry already exists
    existing = _collection.get(ids=[doc_id], include=["metadatas"])
    if existing["ids"]:
        # Update existing — preserve first_seen and increment count
        old_meta         = existing["metadatas"][0]
        first_seen       = old_meta.get("first_seen", now_iso)
        occurrence_count = old_meta.get("occurrence_count", 1) + 1
        action           = "updated"
    else:
        first_seen       = now_iso
        occurrence_count = 1
        action           = "created"

    meta = {
        "alert_name":          req.alert_name,
        "service":             req.service,
        "exception_type":      req.exception_type or "",
        "resolution":          (req.resolution or "")[:500],
        "jira_issue_id":       req.jira_issue_id or "",
        "llm_summary":         (req.llm_summary or "")[:500],
        "llm_confidence":      req.llm_confidence or "",
        "llm_key_action":      (req.llm_key_action or "")[:300],
        "llm_risk_note":       (req.llm_risk_note or "")[:300],
        "first_seen":          first_seen,
        "last_retrieval_date": now_iso,
        "occurrence_count":    occurrence_count,
        "ttl_days":            TTL_DAYS,
    }

    try:
        embedding = _openai_embed([doc_text])
        if action == "updated":
            _collection.update(
                ids=[doc_id],
                embeddings=embedding,
                documents=[doc_text],
                metadatas=[meta]
            )
        else:
            _collection.add(
                ids=[doc_id],
                embeddings=embedding,
                documents=[doc_text],
                metadatas=[meta]
            )
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"ChromaDB operation failed: {e}")

    print(f"[alert-resolution-mcp] {action}: {doc_id} "
          f"(occurrences={occurrence_count})")
    return {
        "tool":             "index_alert_resolution",
        "action":           action,
        "doc_id":           doc_id,
        "alert_name":       req.alert_name,
        "service":          req.service,
        "jira_issue_id":    req.jira_issue_id,
        "occurrence_count": occurrence_count,
        "last_retrieval_date": now_iso,
        "ttl_days":         TTL_DAYS,
    }

@app.get("/tools/list_cached_resolutions")
async def list_cached_resolutions():
    """List all cached alert resolutions with TTL status."""
    all_entries = _collection.get(include=["metadatas"])
    now         = datetime.now(timezone.utc)
    cutoff      = now - timedelta(days=TTL_DAYS)
    entries     = []
    for doc_id, meta in zip(
        all_entries.get("ids", []),
        all_entries.get("metadatas", [])
    ):
        last_retrieval = meta.get("last_retrieval_date", "")
        try:
            last_dt    = datetime.fromisoformat(last_retrieval)
            days_since = (now - last_dt).days
            expires_in = TTL_DAYS - days_since
            is_stale   = last_dt < cutoff
        except Exception:
            days_since = -1
            expires_in = TTL_DAYS
            is_stale   = False

        entries.append({
            "doc_id":              doc_id,
            "alert_name":          meta.get("alert_name"),
            "service":             meta.get("service"),
            "jira_issue_id":       meta.get("jira_issue_id"),
            "llm_confidence":      meta.get("llm_confidence"),
            "occurrence_count":    meta.get("occurrence_count", 1),
            "first_seen":          meta.get("first_seen"),
            "last_retrieval_date": last_retrieval,
            "days_since_retrieval":days_since,
            "expires_in_days":     max(0, expires_in),
            "is_stale":            is_stale,
        })

    entries.sort(key=lambda x: x.get("last_retrieval_date", ""), reverse=True)
    return {
        "tool":          "list_cached_resolutions",
        "total":         len(entries),
        "ttl_days":      TTL_DAYS,
        "entries":       entries,
    }

@app.delete("/tools/delete_resolution/{alert_name}/{service}")
async def delete_resolution(alert_name: str, service: str):
    """Manually delete a cached resolution."""
    doc_id = _make_id(alert_name, service)
    existing = _collection.get(ids=[doc_id], include=[])
    if not existing["ids"]:
        raise HTTPException(status_code=404,
                            detail=f"{doc_id} not found in cache")
    _collection.delete(ids=[doc_id])
    return {"tool": "delete_resolution", "deleted": doc_id}

@app.get("/tools/list_tools")
async def list_tools():
    return {
        "service": "alert-resolution-mcp",
        "port":    MCP_PORT,
        "tools": [
            {"name": "search_alert_resolution",
             "method": "GET",
             "endpoint": "/tools/search_alert_resolution",
             "params": {"alert_name": "required", "service": "required",
                        "threshold": "optional float (default 0.75)"},
             "description": "Agent 1 — check cache before running full pipeline"},
            {"name": "index_alert_resolution",
             "method": "POST",
             "endpoint": "/tools/index_alert_resolution",
             "description": "Agent 3 — populate cache after successful resolution"},
            {"name": "list_cached_resolutions",
             "method": "GET",
             "endpoint": "/tools/list_cached_resolutions",
             "description": "List all cached resolutions with TTL status"},
            {"name": "delete_resolution",
             "method": "DELETE",
             "endpoint": "/tools/delete_resolution/{alert_name}/{service}",
             "description": "Manually delete a cached resolution"},
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
