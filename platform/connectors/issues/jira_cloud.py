"""
jira_cloud.py — IssueConnector implementation for Jira Cloud.

Moves all Jira-specific code from jira-mcp into this connector.
issues-mcp loads this at startup when config.yml issues.connector = jira_cloud.

Config keys:
    url         -- Jira site URL e.g. https://yourorg.atlassian.net
    email       -- Atlassian account email
    api_token   -- API token from id.atlassian.com
    project_key -- Jira project key (default: KAN)

Workaround: Jira free tier returns HTTP 410 for JQL /search.
Uses GET /rest/agile/1.0/board/2/issue instead, filters in memory.
"""

import base64
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from platform.connectors.base import IssueConnector
from platform.connectors.models import Issue, CreatedIssue


class JiraCloudConnector(IssueConnector):

    def validate_config(self):
        required = ["url", "email", "api_token"]
        missing  = [k for k in required if not self.config.get(k)]
        if missing:
            raise ValueError(
                f"jira_cloud connector missing config keys: {missing}. "
                f"Set in config.yml issues.config or as env vars."
            )
        self.config.setdefault("project_key", "KAN")
        self.config.setdefault("board_id", "2")

    @property
    def base_url(self) -> str:
        return self.config["url"].rstrip("/")

    @property
    def project_key(self) -> str:
        return self.config.get("project_key", "KAN")

    @property
    def board_id(self) -> str:
        return self.config.get("board_id", "2")

    def _headers(self) -> dict:
        creds = base64.b64encode(
            f"{self.config['email']}:{self.config['api_token']}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _parse_issue(self, raw: dict) -> Issue:
        fields     = raw.get("fields", {})
        desc_obj   = fields.get("description") or {}
        desc_text  = ""
        if isinstance(desc_obj, dict):
            for block in desc_obj.get("content", []):
                for item in block.get("content", []):
                    if item.get("type") == "text":
                        desc_text += item.get("text", "")

        labels         = fields.get("labels", [])
        exception_type = next(
            (l for l in labels if "Exception" in l or "Error" in l), ""
        )
        service = next((l for l in labels if l.endswith("-svc")), "")

        return Issue(
            issue_id         = raw.get("key", ""),
            summary          = fields.get("summary", ""),
            description      = desc_text,
            resolution       = desc_text,
            resolution_steps = [s.strip() for s in desc_text.split(".")
                                if len(s.strip()) > 20][:5],
            status           = fields.get("status", {}).get("name", "Open"),
            priority         = fields.get("priority", {}).get("name", "Medium"),
            labels           = labels,
            service          = service,
            exception_type   = exception_type,
            source           = "jira_cloud",
        )

    def _fetch_all_from_board(self) -> List[dict]:
        """Fetch all issues from agile board (JQL is 410 on free tier)."""
        all_issues = []
        start_at   = 0
        batch_size = 50
        while True:
            data = self._get(
                f"/rest/agile/1.0/board/{self.board_id}/issue",
                {
                    "maxResults": batch_size,
                    "startAt":    start_at,
                    "fields":     "summary,labels,status,priority,description",
                },
            )
            batch = data.get("issues", [])
            all_issues.extend(batch)
            if len(batch) < batch_size:
                break
            start_at += batch_size
        return all_issues

    def search_issues(
        self,
        query:     str,
        service:   Optional[str] = None,
        min_score: float = 0.25,
        limit:     int = 5,
    ) -> List[Issue]:
        raw_issues   = self._fetch_all_from_board()
        query_lower  = query.lower()
        query_words  = set(query_lower.split())
        scored       = []

        for raw in raw_issues:
            fields  = raw.get("fields", {})
            labels  = [l.lower() for l in fields.get("labels", [])]
            summary = fields.get("summary", "").lower()

            if service and service.lower() not in labels:
                continue

            score = 0.0
            for word in query_words:
                if word in labels:
                    score += 3.0
                if word in summary:
                    score += 1.0

            if score >= min_score:
                scored.append((score, raw))

        scored.sort(key=lambda x: -x[0])
        return [self._parse_issue(raw) for _, raw in scored[:limit]]

    def get_issue(self, issue_id: str) -> Optional[Issue]:
        try:
            raw = self._get(
                f"/rest/api/3/issue/{issue_id}",
                {"fields": "summary,labels,status,priority,description"},
            )
            return self._parse_issue(raw)
        except Exception as e:
            raise RuntimeError(f"Jira get_issue({issue_id}) failed: {e}")

    def create_issue(
        self,
        summary:     str,
        description: str,
        service:     str,
        priority:    str = "High",
        labels:      Optional[List[str]] = None,
    ) -> CreatedIssue:
        payload = {
            "fields": {
                "project":     {"key": self.project_key},
                "summary":     summary,
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": description or summary}
                    ]}],
                },
                "issuetype": {"name": "Bug"},
                "labels":    labels or [service],
                "priority":  {"name": priority},
            }
        }
        data = self._post("/rest/api/3/issue", payload)
        key  = data.get("key", "UNKNOWN")
        return CreatedIssue(
            issue_id = key,
            url      = f"{self.base_url}/browse/{key}",
            source   = "jira_cloud",
        )

    def list_issues(
        self,
        service: Optional[str] = None,
        status:  Optional[str] = None,
        limit:   int = 50,
    ) -> List[Issue]:
        raw_issues = self._fetch_all_from_board()
        issues     = [self._parse_issue(r) for r in raw_issues]
        if service:
            issues = [i for i in issues if service.lower() in i.service.lower()]
        if status:
            issues = [i for i in issues if status.lower() == i.status.lower()]
        return issues[:limit]
