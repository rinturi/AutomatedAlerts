"""
vectordb-mcp: MCP server providing semantic similarity search for Agent 2.

Uses ChromaDB (in-process, persisted to disk) with sentence-transformers
to embed error messages and find semantically similar past JIRA defects.

Agent 2 calls this FIRST before jira-mcp keyword search.
If semantic score is high (>= 0.75), skip keyword search entirely.
If medium (0.50-0.75), use as hint to guide keyword search.
If low (< 0.50), fall through to jira-mcp keyword search.

Tools:
  GET  /tools/embed_and_search    → semantic search for similar errors
  POST /tools/index_document      → add a document to the vector store
  GET  /tools/get_collection_info → stats about the vector store
  POST /tools/bulk_index          → index multiple documents at once
  GET  /tools/list_tools          → tool manifest
  GET  /health                    → server health
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="vectordb-mcp", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
MCP_PORT        = int(os.getenv("MCP_PORT", "9004"))
CHROMA_PATH     = os.getenv("CHROMA_PATH", "/app/chroma_data")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "banking_errors")
JIRA_STORE_PATH = Path(os.getenv("JIRA_STORE_PATH", "/app/jira_store.json"))
MODEL_NAME      = os.getenv("EMBED_MODEL", "text-embedding-3-small")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")

HIGH_THRESHOLD   = float(os.getenv("HIGH_THRESHOLD",   "0.75"))
MEDIUM_THRESHOLD = float(os.getenv("MEDIUM_THRESHOLD", "0.50"))

# ── ChromaDB client and collection ────────────────────────────────────────────
# chromadb 0.4.x uses Settings object to disable telemetry
print(f"[vectordb-mcp] Initialising ChromaDB at {CHROMA_PATH}")
print(f"[vectordb-mcp] Loading embedding model: {MODEL_NAME}")

_settings = chromadb.Settings(
    anonymized_telemetry=False,
    allow_reset=True,
    is_persistent=True,
    persist_directory=CHROMA_PATH,
)
_client = chromadb.Client(_settings)
_embed_fn = None  # Using OpenAI embeddings via custom function below

def _openai_embed(texts: list) -> list:
    """Call OpenAI text-embedding-3-small to embed a list of texts."""
    import urllib.request, urllib.parse, json, base64
    payload = json.dumps({"model": MODEL_NAME, "input": texts}).encode()
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
_collection = _client.get_or_create_collection(
    name=COLLECTION_NAME,
)

print(f"[vectordb-mcp] Collection '{COLLECTION_NAME}' ready. "
      f"Documents: {_collection.count()}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def chroma_distance_to_similarity(distance: float) -> float:
    """
    ChromaDB cosine distance → similarity score.
    distance=0 means identical (similarity=1.0).
    distance=2 means opposite (similarity=0.0).
    """
    return round(max(0.0, 1.0 - (distance / 2.0)), 4)


def classify_match(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "high"
    elif score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def build_document_text(issue: dict) -> str:
    """
    Concatenate issue fields into a single text for embedding.
    Weights important fields by repetition.
    """
    parts = [
        issue.get("summary", ""),
        issue.get("exception_type", ""),
        issue.get("exception_type", ""),      # repeated for weight
        " ".join(issue.get("error_keywords", [])),
        " ".join(issue.get("error_keywords", [])),  # repeated for weight
        issue.get("description", ""),
        issue.get("service", ""),
    ]
    return " ".join(p for p in parts if p).strip()


INDEXABLE_STATUSES = {"Done", "Resolved", "Closed"}

def load_jira_from_cloud() -> list:
    """
    Fetch only RESOLVED/DONE issues from Jira Cloud.
    Falls back to jira_store.json if Jira Cloud unreachable.
    """
    import base64, urllib.request, urllib.parse

    jira_url   = os.getenv("JIRA_URL", "")
    jira_email = os.getenv("JIRA_EMAIL", "")
    jira_token = os.getenv("JIRA_API_TOKEN", "")
    board_id   = os.getenv("JIRA_BOARD_ID", "2")

    if jira_url and jira_email and jira_token:
        try:
            creds   = base64.b64encode(f"{jira_email}:{jira_token}".encode()).decode()
            headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
            all_issues = []
            start_at   = 0
            while True:
                params = urllib.parse.urlencode({
                    "maxResults": 50, "startAt": start_at,
                    "fields": "summary,labels,status,priority,description",
                })
                req = urllib.request.Request(
                    f"{jira_url}/rest/agile/1.0/board/{board_id}/issue?{params}",
                    headers=headers
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                batch = data.get("issues", [])
                all_issues.extend(batch)
                if len(batch) < 50:
                    break
                start_at += 50

            parsed = []
            for raw in all_issues:
                fields  = raw.get("fields", {})
                status  = fields.get("status", {}).get("name", "")
                if status not in INDEXABLE_STATUSES:
                    continue
                labels         = fields.get("labels", [])
                exception_type = next((l for l in labels if "Exception" in l or "Error" in l), "")
                service        = next((l for l in labels if l.endswith("-svc")), "")
                desc_obj       = fields.get("description") or {}
                desc_text      = ""
                if isinstance(desc_obj, dict):
                    for block in desc_obj.get("content", []):
                        for item in block.get("content", []):
                            if item.get("type") == "text":
                                desc_text += item.get("text", "")
                parsed.append({
                    "id":               raw.get("key"),
                    "summary":          fields.get("summary", ""),
                    "description":      desc_text,
                    "status":           status,
                    "priority":         fields.get("priority", {}).get("name", "Medium"),
                    "labels":           labels,
                    "service":          service,
                    "exception_type":   exception_type,
                    "resolution":       desc_text,
                    "resolution_steps": [s.strip() for s in desc_text.split(".")
                                        if len(s.strip()) > 20][:5],
                    "error_keywords":   labels,
                })
            print(f"[vectordb-mcp] Fetched {len(parsed)} Done/Resolved issues from Jira Cloud")
            return parsed
        except Exception as e:
            print(f"[vectordb-mcp] Jira Cloud fetch failed: {e} — falling back to jira_store.json")

    return load_jira_store()


def load_jira_store() -> list:
    if not JIRA_STORE_PATH.exists():
        return []
    with open(JIRA_STORE_PATH) as f:
        return json.load(f).get("issues", [])


# ── Auto-index on startup ─────────────────────────────────────────────────────

# Only index issues with these statuses — open/in-progress have no proven resolution
def seed_collection_from_jira():
    """
    On startup, index all JIRA defects that are not yet in ChromaDB.
    Idempotent — skips documents already indexed by ID.
    """
    issues = load_jira_from_cloud()
    if not issues:
        print("[vectordb-mcp] No JIRA store found — skipping seed")
        return

    existing_ids = set()
    try:
        existing = _collection.get(include=[])
        existing_ids = set(existing.get("ids", []))
    except Exception:
        pass

    to_add_ids, to_add_docs, to_add_metas = [], [], []

    for issue in issues:
        doc_id = issue["id"]
        if doc_id in existing_ids:
            continue
        # Skip issues not in INDEXABLE_STATUSES
        issue_status = issue.get("status", "")
        if issue_status not in INDEXABLE_STATUSES:
            print(f"[vectordb-mcp] Skipping {doc_id} (status={issue_status})")
            continue
        doc_text = build_document_text(issue)
        metadata = {
            "issue_id":       issue["id"],
            "summary":        issue.get("summary", "")[:500],
            "exception_type": issue.get("exception_type", ""),
            "service":        issue.get("service", ""),
            "status":         issue.get("status", ""),
            "priority":       issue.get("priority", ""),
            "resolution_short": (issue.get("resolution") or "")[:300],
        }
        to_add_ids.append(doc_id)
        to_add_docs.append(doc_text)
        to_add_metas.append(metadata)

    if to_add_ids:
        print(f"[vectordb-mcp] Generating OpenAI embeddings for {len(to_add_ids)} documents...")
        embeddings = _openai_embed(to_add_docs)
        _collection.add(
            ids=to_add_ids,
            embeddings=embeddings,
            documents=to_add_docs,
            metadatas=to_add_metas
        )
        print(f"[vectordb-mcp] Indexed {len(to_add_ids)} documents from JIRA store")
    else:
        print(f"[vectordb-mcp] All {len(issues)} documents already indexed")


# Seed on import (runs when uvicorn starts the app)
try:
    seed_collection_from_jira()
except Exception as e:
    print(f"[vectordb-mcp] Seed warning: {e}")


# ── Jira polling thread — auto-index newly resolved tickets ──────────────────
def _poll_jira_for_new_resolved():
    """Poll Jira every 10 minutes for Done issues not yet in ChromaDB."""
    import time, base64
    import urllib.request as _ur
    import urllib.parse as _up

    print("[vectordb-mcp] Jira polling thread started (interval: 10 minutes)")
    while True:
        time.sleep(600)
        try:
            jira_url   = os.getenv("JIRA_URL", "")
            jira_email = os.getenv("JIRA_EMAIL", "")
            jira_token = os.getenv("JIRA_API_TOKEN", "")
            board_id   = os.getenv("JIRA_BOARD_ID", "2")
            if not (jira_url and jira_email and jira_token):
                continue
            creds   = base64.b64encode(f"{jira_email}:{jira_token}".encode()).decode()
            headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
            all_issues = []
            start_at   = 0
            while True:
                params = _up.urlencode({
                    "maxResults": 50, "startAt": start_at,
                    "fields": "summary,labels,status,priority,description"
                })
                req = _ur.Request(
                    f"{jira_url}/rest/agile/1.0/board/{board_id}/issue?{params}",
                    headers=headers
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                batch = data.get("issues", [])
                all_issues.extend(batch)
                if len(batch) < 50:
                    break
                start_at += 50
            existing     = _collection.get(include=[])
            existing_ids = set(existing.get("ids", []))
            new_count    = 0
            for raw in all_issues:
                key    = raw.get("key")
                fields = raw.get("fields", {})
                status = fields.get("status", {}).get("name", "")
                if status not in INDEXABLE_STATUSES:
                    continue
                if key in existing_ids:
                    continue
                labels         = fields.get("labels", [])
                exception_type = next((l for l in labels if "Exception" in l or "Error" in l), "")
                service        = next((l for l in labels if l.endswith("-svc")), "")
                desc_obj       = fields.get("description") or {}
                desc_text      = ""
                if isinstance(desc_obj, dict):
                    for block in desc_obj.get("content", []):
                        for item in block.get("content", []):
                            if item.get("type") == "text":
                                desc_text += item.get("text", "")
                issue = {
                    "id": key, "summary": fields.get("summary",""),
                    "description": desc_text, "status": status,
                    "priority": fields.get("priority",{}).get("name","Medium"),
                    "labels": labels, "service": service,
                    "exception_type": exception_type, "resolution": desc_text,
                    "error_keywords": labels,
                    "resolution_steps": [s.strip() for s in desc_text.split(".")
                                        if len(s.strip()) > 20][:5],
                }
                doc_text = build_document_text(issue)
                metadata = {
                    "issue_id": key, "summary": fields.get("summary","")[:500],
                    "exception_type": exception_type, "service": service,
                    "status": status,
                    "priority": fields.get("priority",{}).get("name","Medium"),
                    "resolution_short": desc_text[:300],
                }
                try:
                    embeddings = _openai_embed([doc_text])
                    _collection.add(
                        ids=[key], embeddings=embeddings,
                        documents=[doc_text], metadatas=[metadata]
                    )
                    print(f"[vectordb-mcp] Poll: indexed {key} service={service}")
                    new_count += 1
                except Exception as idx_err:
                    print(f"[vectordb-mcp] Poll: failed {key}: {idx_err}")
            print(f"[vectordb-mcp] Poll complete: {new_count} new issues indexed")
        except Exception as poll_err:
            print(f"[vectordb-mcp] Poll error: {poll_err}")

import threading as _threading
_jira_poll_thread = _threading.Thread(
    target=_poll_jira_for_new_resolved, daemon=True, name="jira-poll"
)
_jira_poll_thread.start()

# ── Request models ────────────────────────────────────────────────────────────

class IndexDocumentRequest(BaseModel):
    doc_id:         str
    text:           str
    issue_id:       Optional[str] = None
    summary:        Optional[str] = None
    exception_type: Optional[str] = None
    service:        Optional[str] = None
    status:         Optional[str] = None
    resolution:     Optional[str] = None
    extra_metadata: Optional[dict] = {}


class BulkIndexRequest(BaseModel):
    documents: list[IndexDocumentRequest]


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    count = _collection.count()
    return {
        "status": "up",
        "service": "vectordb-mcp",
        "collection": COLLECTION_NAME,
        "embed_model": MODEL_NAME,
        "indexed_documents": count,
        "chroma_path": CHROMA_PATH,
        "thresholds": {
            "high":   HIGH_THRESHOLD,
            "medium": MEDIUM_THRESHOLD
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/embed_and_search")
async def embed_and_search(
    query:          str,
    top_k:          int = 5,
    service_filter: Optional[str] = None,
    min_score:      float = 0.30,
):
    """
    Tool: embed_and_search
    Embeds the error message query and finds semantically similar
    past JIRA defects using cosine similarity.

    Agent 2 calls this first with the error message from kibana-mcp.
    The match_quality field tells Agent 2 whether to proceed to jira-mcp.

    Query params:
      query          required — error message from kibana-mcp extract_root_cause
      top_k          optional — number of results to return (default 5)
      service_filter optional — restrict to a specific service
      min_score      optional — minimum similarity score (default 0.30)
    """
    if not query.strip():
        raise HTTPException(
            status_code=400,
            detail="query is required and cannot be empty"
        )

    if _collection.count() == 0:
        return {
            "tool": "embed_and_search",
            "query": query,
            "total_results": 0,
            "match_quality": "none",
            "results": [],
            "agent_instruction": "Vector store is empty. Fall through to jira-mcp keyword search.",
            "queried_at": datetime.now(timezone.utc).isoformat()
        }

    # Build ChromaDB where filter
    where_filter = None
    if service_filter:
        where_filter = {"service": {"$eq": service_filter}}

    # Query ChromaDB
    try:
        query_embedding = _openai_embed([query])
        query_params = {
            "query_embeddings": query_embedding,
            "n_results":        min(top_k, _collection.count()),
            "include":          ["documents", "metadatas", "distances"]
        }
        if where_filter:
            query_params["where"] = where_filter

        results = _collection.query(**query_params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ChromaDB query failed: {str(e)}")

    # Parse results
    ids        = results.get("ids", [[]])[0]
    distances  = results.get("distances", [[]])[0]
    metadatas  = results.get("metadatas", [[]])[0]
    documents  = results.get("documents", [[]])[0]

    matches = []
    for doc_id, distance, meta, doc in zip(ids, distances, metadatas, documents):
        score = chroma_distance_to_similarity(distance)
        if score < min_score:
            continue
        matches.append({
            "doc_id":         doc_id,
            "issue_id":       meta.get("issue_id", doc_id),
            "similarity_score": score,
            "match_quality":  classify_match(score),
            "summary":        meta.get("summary", ""),
            "exception_type": meta.get("exception_type", ""),
            "service":        meta.get("service", ""),
            "status":         meta.get("status", ""),
            "priority":       meta.get("priority", ""),
            "resolution_short": meta.get("resolution_short", ""),
        })

    # Sort by score descending
    matches.sort(key=lambda x: -x["similarity_score"])

    # Overall match quality based on best result
    if matches:
        best = matches[0]["similarity_score"]
        overall_quality = classify_match(best)
    else:
        overall_quality = "none"

    # Agent instructions based on match quality
    if overall_quality == "high":
        instruction = (
            f"High confidence match found ({matches[0]['issue_id']}). "
            "Call jira-mcp /tools/get_issue with this issue_id to retrieve full resolution. "
            "No need for keyword search."
        )
    elif overall_quality == "medium":
        instruction = (
            "Medium confidence match. Call jira-mcp /tools/get_issue to verify resolution, "
            "and also run jira-mcp /tools/search_issues for confirmation."
        )
    elif matches:
        instruction = (
            "Low confidence matches only. Fall through to jira-mcp /tools/search_issues "
            "for keyword-based search. Use these results as hints only."
        )
    else:
        instruction = (
            "No semantic match found. Proceed to jira-mcp /tools/search_issues "
            "for keyword search. If no match there either, call jira-mcp /tools/create_issue."
        )

    return {
        "tool": "embed_and_search",
        "query": query,
        "total_results": len(matches),
        "match_quality": overall_quality,
        "results": matches,
        "agent_instruction": instruction,
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.post("/tools/index_document")
async def index_document(req: IndexDocumentRequest):
    """
    Tool: index_document
    Adds a single document to the vector store.
    Agent 2 calls this after creating a new JIRA issue so future
    searches can find it semantically.

    Body fields:
      doc_id          required — unique ID (use JIRA issue ID)
      text            required — document text to embed
      issue_id        optional — JIRA issue ID
      summary         optional
      exception_type  optional
      service         optional
      status          optional
      resolution      optional
      extra_metadata  optional — any additional key-value pairs
    """
    # Check for duplicate
    try:
        existing = _collection.get(ids=[req.doc_id], include=[])
        if existing["ids"]:
            # Update existing document
            _collection.update(
                ids=[req.doc_id],
                embeddings=_openai_embed([req.text]),
                documents=[req.text],
                metadatas=[{
                    "issue_id":         req.issue_id or req.doc_id,
                    "summary":          (req.summary or "")[:500],
                    "exception_type":   req.exception_type or "",
                    "service":          req.service or "",
                    "status":           req.status or "",
                    "resolution_short": (req.resolution or "")[:300],
                    **(req.extra_metadata or {})
                }]
            )
            action = "updated"
        else:
            raise Exception("not found")
    except Exception:
        # Add new document
        _collection.add(
            ids=[req.doc_id],
            embeddings=_openai_embed([req.text]),
            documents=[req.text],
            metadatas=[{
                "issue_id":         req.issue_id or req.doc_id,
                "summary":          (req.summary or "")[:500],
                "exception_type":   req.exception_type or "",
                "service":          req.service or "",
                "status":           req.status or "",
                "resolution_short": (req.resolution or "")[:300],
                **(req.extra_metadata or {})
            }]
        )
        action = "indexed"

    return {
        "tool": "index_document",
        "status": action,
        "doc_id": req.doc_id,
        "collection": COLLECTION_NAME,
        "total_documents": _collection.count(),
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.post("/tools/bulk_index")
async def bulk_index(req: BulkIndexRequest):
    """
    Tool: bulk_index
    Indexes multiple documents at once.
    Useful for re-seeding the collection or adding resolved issues in batch.
    """
    if not req.documents:
        raise HTTPException(status_code=400, detail="documents list cannot be empty")

    ids, texts, metas = [], [], []
    for doc in req.documents:
        ids.append(doc.doc_id)
        texts.append(doc.text)
        metas.append({
            "issue_id":         doc.issue_id or doc.doc_id,
            "summary":          (doc.summary or "")[:500],
            "exception_type":   doc.exception_type or "",
            "service":          doc.service or "",
            "status":           doc.status or "",
            "resolution_short": (doc.resolution or "")[:300],
            **(doc.extra_metadata or {})
        })

    _collection.upsert(ids=ids, documents=texts, metadatas=metas)

    return {
        "tool": "bulk_index",
        "status": "indexed",
        "documents_indexed": len(ids),
        "total_documents": _collection.count(),
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/get_collection_info")
async def get_collection_info():
    """
    Tool: get_collection_info
    Returns stats and a sample of indexed documents.
    Useful for Agent 2 to understand what is in the vector store.
    """
    count = _collection.count()

    # Get a sample of up to 5 documents
    sample = []
    if count > 0:
        try:
            result = _collection.get(
                limit=5,
                include=["metadatas"]
            )
            sample = [
                {
                    "id":   doc_id,
                    "meta": meta
                }
                for doc_id, meta in zip(
                    result.get("ids", []),
                    result.get("metadatas", [])
                )
            ]
        except Exception:
            pass

    return {
        "tool": "get_collection_info",
        "collection": COLLECTION_NAME,
        "embed_model": MODEL_NAME,
        "total_documents": count,
        "thresholds": {
            "high":   HIGH_THRESHOLD,
            "medium": MEDIUM_THRESHOLD,
            "low":    f"< {MEDIUM_THRESHOLD}"
        },
        "chroma_path": CHROMA_PATH,
        "sample_documents": sample,
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/list_tools")
async def list_tools():
    """MCP tool manifest — Agent 2 calls this on startup."""
    return {
        "mcp_server": "vectordb-mcp",
        "version": "1.0.0",
        "description": (
            "Semantic similarity search over past JIRA defects using "
            "sentence-transformers embeddings and ChromaDB vector store. "
            "Agent 2 calls embed_and_search FIRST before jira-mcp keyword search."
        ),
        "embed_model": MODEL_NAME,
        "indexed_documents": _collection.count(),
        "tools": [
            {
                "name": "embed_and_search",
                "endpoint": "GET /tools/embed_and_search",
                "description": (
                    "Primary tool for Agent 2. Embeds error message and finds "
                    "semantically similar past defects. Use match_quality to decide next step."
                ),
                "params": {
                    "query":          "required — error message from kibana-mcp extract_root_cause",
                    "top_k":          "optional — number of results (default 5)",
                    "service_filter": "optional — restrict to one service",
                    "min_score":      "optional — minimum similarity threshold (default 0.30)"
                },
                "match_quality_guide": {
                    "high (>= 0.75)":   "Call jira-mcp get_issue directly — skip keyword search",
                    "medium (0.50-0.75)": "Verify with jira-mcp get_issue + search_issues",
                    "low (< 0.50)":     "Fall through to jira-mcp search_issues keyword search",
                    "none":             "No match — call jira-mcp create_issue"
                }
            },
            {
                "name": "index_document",
                "endpoint": "POST /tools/index_document",
                "description": "Index a single document. Call after creating a new JIRA issue.",
                "params": {
                    "doc_id":         "required — unique ID (use JIRA issue ID)",
                    "text":           "required — document text to embed",
                    "exception_type": "optional",
                    "service":        "optional",
                    "resolution":     "optional"
                }
            },
            {
                "name": "get_collection_info",
                "endpoint": "GET /tools/get_collection_info",
                "description": "Vector store stats and sample documents",
                "params": {}
            }
        ]
    }


# ── Jira Webhook — auto-index newly resolved tickets ──────────────────────────
@app.post("/webhook/jira")
async def jira_webhook(payload: dict):
    """
    Called by Jira Cloud when any issue transitions to Done/Resolved/Closed.
    Fetches the issue, checks if already indexed, embeds and indexes if new.

    Configure in Jira: Project Settings → Automation → Webhook
    URL: http://EC2_IP:9004/webhook/jira
    Trigger: Issue transitioned → Status = Done
    """
    import base64, urllib.request as _ureq, urllib.parse as _uparse

    # Extract issue key from Jira webhook payload
    issue_key = (
        payload.get("issue", {}).get("key")
        or payload.get("issueKey")
        or payload.get("key")
    )

    if not issue_key:
        print(f"[webhook/jira] No issue key in payload: {list(payload.keys())}")
        return {"status": "ignored", "reason": "no issue key in payload"}

    # Check transition status
    transition = payload.get("transition", {})
    new_status  = (
        transition.get("to", {}).get("name", "")
        or payload.get("issue", {}).get("fields", {})
                  .get("status", {}).get("name", "")
    )

    print(f"[webhook/jira] Received: {issue_key}  status={new_status}")

    if new_status not in INDEXABLE_STATUSES:
        print(f"[webhook/jira] Skipping {issue_key} — status '{new_status}' not indexable")
        return {"status": "ignored", "reason": f"status '{new_status}' not in INDEXABLE_STATUSES"}

    # Check if already indexed
    try:
        existing = _collection.get(ids=[issue_key], include=[])
        if existing.get("ids"):
            print(f"[webhook/jira] {issue_key} already indexed — skipping")
            return {"status": "already_indexed", "issue_id": issue_key}
    except Exception:
        pass

    # Fetch full issue from Jira Cloud
    jira_url   = os.getenv("JIRA_URL", "")
    jira_email = os.getenv("JIRA_EMAIL", "")
    jira_token = os.getenv("JIRA_API_TOKEN", "")

    if not (jira_url and jira_email and jira_token):
        return {"status": "error", "reason": "Jira env vars not set"}

    try:
        creds   = base64.b64encode(f"{jira_email}:{jira_token}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}
        params  = _uparse.urlencode({"fields": "summary,labels,status,description,priority"})
        req     = _ureq.Request(
            f"{jira_url}/rest/api/3/issue/{issue_key}?{params}",
            headers=headers
        )
        with _ureq.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[webhook/jira] Failed to fetch {issue_key} from Jira: {e}")
        return {"status": "error", "reason": str(e)}

    fields         = data.get("fields", {})
    status_name    = fields.get("status", {}).get("name", "")
    labels         = fields.get("labels", [])
    exception_type = next((l for l in labels if "Exception" in l or "Error" in l), "")
    service        = next((l for l in labels if l.endswith("-svc")), "")
    summary        = fields.get("summary", "")
    priority       = fields.get("priority", {}).get("name", "Medium")

    # Parse ADF description
    desc_obj  = fields.get("description") or {}
    desc_text = ""
    if isinstance(desc_obj, dict):
        for block in desc_obj.get("content", []):
            for item in block.get("content", []):
                if item.get("type") == "text":
                    desc_text += item.get("text", "")

    # Final status check from Jira
    if status_name not in INDEXABLE_STATUSES:
        print(f"[webhook/jira] {issue_key} Jira status='{status_name}' — not indexable")
        return {"status": "ignored", "reason": f"Jira status '{status_name}' not indexable"}

    # Build issue dict and index
    issue = {
        "id":               issue_key,
        "summary":          summary,
        "description":      desc_text,
        "status":           status_name,
        "priority":         priority,
        "labels":           labels,
        "service":          service,
        "exception_type":   exception_type,
        "resolution":       desc_text,
        "resolution_steps": [s.strip() for s in desc_text.split(".")
                             if len(s.strip()) > 20][:5],
        "error_keywords":   labels,
    }

    doc_text = build_document_text(issue)
    metadata = {
        "issue_id":         issue_key,
        "summary":          summary[:500],
        "exception_type":   exception_type,
        "service":          service,
        "status":           status_name,
        "priority":         priority,
        "resolution_short": desc_text[:300],
    }

    try:
        embeddings = _openai_embed([doc_text])
        _collection.add(
            ids=[issue_key],
            embeddings=embeddings,
            documents=[doc_text],
            metadatas=[metadata]
        )
        print(f"[webhook/jira] Indexed {issue_key} — service={service} exception={exception_type}")
        return {
            "status":    "indexed",
            "issue_id":  issue_key,
            "service":   service,
            "exception": exception_type,
            "summary":   summary,
        }
    except Exception as e:
        print(f"[webhook/jira] Failed to index {issue_key}: {e}")
        return {"status": "error", "reason": str(e)}


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
