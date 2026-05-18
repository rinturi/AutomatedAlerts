"""
servicenow.py — IssueConnector for ServiceNow.

Uses ServiceNow Table API to search, retrieve, and create incidents.
Maps ServiceNow incident fields to the standard Issue dataclass.

Config keys:
    url       -- ServiceNow instance URL e.g. https://yourorg.service-now.com
    username  -- ServiceNow username (or ${SNOW_USERNAME})
    password  -- ServiceNow password (or ${SNOW_PASSWORD})
    table     -- Table name (default: incident)
                 Use "problem" for known errors, "incident" for L1 incidents.
"""

import base64
import json
import os
import urllib.parse
import urllib.request
from typing import List, Optional

from platform.connectors.base import IssueConnector
from platform.connectors.models import Issue, CreatedIssue

PRIORITY_MAP = {
    "1": "Critical",
    "2": "High",
    "3": "Medium",
    "4": "Low",
    "5": "Planning",
}

STATE_MAP = {
    "1": "Open",
    "2": "In Progress",
    "3": "On Hold",
    "6": "Resolved",
    "7": "Closed",
    "8": "Canceled",
}


class ServiceNowConnector(IssueConnector):

    def validate_config(self):
        if not self.config.get("username"):
            self.config["username"] = os.getenv("SNOW_USERNAME", "")
        if not self.config.get("password"):
            self.config["password"] = os.getenv("SNOW_PASSWORD", "")

        missing = [k for k in ["url", "username", "password"]
                   if not self.config.get(k)]
        if missing:
            raise ValueError(
                f"ServiceNow connector missing config keys: {missing}"
            )
        self.config.setdefault("table", "incident")

    @property
    def base_url(self) -> str:
        return self.config["url"].rstrip("/")

    @property
    def table(self) -> str:
        return self.config.get("table", "incident")

    def _headers(self) -> dict:
        creds = base64.b64encode(
            f"{self.config['username']}:{self.config['password']}".encode()
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

    def _parse_record(self, rec: dict) -> Issue:
        """Map a ServiceNow incident record to Issue dataclass."""
        priority_code = str(rec.get("priority", "3"))
        state_code    = str(rec.get("state", "1"))

        short_desc     = rec.get("short_description", "")
        description    = rec.get("description", "")
        close_notes    = rec.get("close_notes", "")
        resolution     = close_notes or description

        # Extract service from category or assignment group
        category     = rec.get("category", "")
        subcategory  = rec.get("subcategory", "")
        service_hint = subcategory or category

        # Try to find exception type in description
        import re
        exc_match = re.search(
            r'([A-Z][a-zA-Z]+Exception|[A-Z][a-zA-Z]+Error)',
            description
        )
        exception_type = exc_match.group(1) if exc_match else ""

        return Issue(
            issue_id         = rec.get("number", rec.get("sys_id", "")),
            summary          = short_desc,
            description      = description,
            resolution       = resolution,
            resolution_steps = [s.strip() for s in resolution.split(".")
                                if len(s.strip()) > 20][:5],
            status           = STATE_MAP.get(state_code, state_code),
            priority         = PRIORITY_MAP.get(priority_code, "Medium"),
            labels           = [category, subcategory] if category else [],
            service          = service_hint,
            exception_type   = exception_type,
            created_at       = rec.get("sys_created_on", ""),
            updated_at       = rec.get("sys_updated_on", ""),
            source           = "servicenow",
        )

    def search_issues(
        self,
        query:     str,
        service:   Optional[str] = None,
        min_score: float = 0.25,
        limit:     int = 5,
    ) -> List[Issue]:
        # ServiceNow text search using encoded query
        encoded_query = f"short_descriptionLIKE{query}^ORdescriptionLIKE{query}"
        if service:
            encoded_query += f"^subcategoryLIKE{service}"

        # Only open/in-progress incidents
        encoded_query += "^state!=6^state!=7^state!=8"

        params = {
            "sysparm_query":  encoded_query,
            "sysparm_limit":  limit,
            "sysparm_fields": (
                "number,short_description,description,close_notes,"
                "priority,state,category,subcategory,"
                "sys_created_on,sys_updated_on,sys_id"
            ),
        }

        try:
            data    = self._get(f"/api/now/table/{self.table}", params)
            records = data.get("result", [])
        except Exception as e:
            raise RuntimeError(f"ServiceNow search failed: {e}")

        issues = [self._parse_record(r) for r in records]

        # Score by query word overlap
        query_words = set(query.lower().split())
        def score(issue: Issue) -> float:
            text = f"{issue.summary} {issue.description}".lower()
            return sum(1.0 for w in query_words if w in text)

        issues.sort(key=score, reverse=True)
        return [i for i in issues if score(i) >= min_score]

    def get_issue(self, issue_id: str) -> Optional[Issue]:
        # Search by incident number
        try:
            data    = self._get(f"/api/now/table/{self.table}", {
                "sysparm_query":  f"number={issue_id}",
                "sysparm_limit":  1,
                "sysparm_fields": (
                    "number,short_description,description,close_notes,"
                    "priority,state,category,subcategory,"
                    "sys_created_on,sys_updated_on,sys_id"
                ),
            })
            records = data.get("result", [])
            if not records:
                return None
            return self._parse_record(records[0])
        except Exception as e:
            raise RuntimeError(f"ServiceNow get_issue({issue_id}) failed: {e}")

    def create_issue(
        self,
        summary:     str,
        description: str,
        service:     str,
        priority:    str = "High",
        labels:      Optional[List[str]] = None,
    ) -> CreatedIssue:
        # Map priority string to ServiceNow priority code
        priority_reverse = {v: k for k, v in PRIORITY_MAP.items()}
        priority_code    = priority_reverse.get(priority, "2")

        payload = {
            "short_description": summary,
            "description":       description,
            "category":          "Software",
            "subcategory":       service,
            "priority":          priority_code,
            "impact":            "2",
            "urgency":           "2",
        }

        try:
            data   = self._post(f"/api/now/table/{self.table}", payload)
            result = data.get("result", {})
            number = result.get("number", "UNKNOWN")
            sys_id = result.get("sys_id", "")
            return CreatedIssue(
                issue_id = number,
                url      = f"{self.base_url}/{self.table}.do?sys_id={sys_id}",
                source   = "servicenow",
            )
        except Exception as e:
            raise RuntimeError(f"ServiceNow create_issue failed: {e}")

    def list_issues(
        self,
        service: Optional[str] = None,
        status:  Optional[str] = None,
        limit:   int = 50,
    ) -> List[Issue]:
        query = "active=true"
        if service:
            query += f"^subcategoryLIKE{service}"
        if status:
            state_reverse = {v: k for k, v in STATE_MAP.items()}
            state_code    = state_reverse.get(status, "")
            if state_code:
                query += f"^state={state_code}"

        try:
            data    = self._get(f"/api/now/table/{self.table}", {
                "sysparm_query":  query,
                "sysparm_limit":  limit,
                "sysparm_fields": (
                    "number,short_description,description,close_notes,"
                    "priority,state,category,subcategory,"
                    "sys_created_on,sys_updated_on"
                ),
            })
            return [self._parse_record(r) for r in data.get("result", [])]
        except Exception:
            return []
