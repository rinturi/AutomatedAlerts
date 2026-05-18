"""
pagerduty.py — AlertConnector for PagerDuty.

Fetches TRIGGERED and ACKNOWLEDGED incidents from PagerDuty REST API v2.
Maps PagerDuty incident structure to the standard Alert dataclass.

Config keys:
    api_key     -- PagerDuty REST API key (or ${PAGERDUTY_API_KEY})
    service_ids -- Optional list of PagerDuty service IDs to filter
                   e.g. ["P1ABC23", "P4DEF56"]
                   If empty, returns all incidents for the account.
    subdomain   -- PagerDuty subdomain (used to build web URLs)
"""

import json
import urllib.parse
import urllib.request
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from platform.connectors.base import AlertConnector
from platform.connectors.models import Alert

SEVERITY_MAP = {
    "P1": "critical",
    "P2": "critical",
    "P3": "warning",
    "P4": "warning",
    "P5": "info",
}


class PagerDutyConnector(AlertConnector):

    def validate_config(self):
        if not self.config.get("api_key"):
            import os
            self.config["api_key"] = os.getenv("PAGERDUTY_API_KEY", "")
        if not self.config.get("api_key"):
            raise ValueError(
                "PagerDuty connector requires api_key in config "
                "or PAGERDUTY_API_KEY env var"
            )
        # Normalise service_ids to list
        svc_ids = self.config.get("service_ids", [])
        if isinstance(svc_ids, str):
            self.config["service_ids"] = [
                s.strip() for s in svc_ids.split(",") if s.strip()
            ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Token token={self.config['api_key']}",
            "Content-Type":  "application/json",
            "Accept":        "application/vnd.pagerduty+json;version=2",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        base = "https://api.pagerduty.com"
        url  = f"{base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _make_alert_id(self, incident: dict) -> str:
        key = incident.get("id", str(incident.get("incident_number", "")))
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _parse_incident(self, inc: dict) -> Alert:
        """Map PagerDuty incident to standard Alert dataclass."""
        urgency      = inc.get("urgency", "low")
        priority_obj = inc.get("priority", {})
        pd_priority  = priority_obj.get("name", "") if isinstance(priority_obj, dict) else ""

        # Determine severity from urgency and priority
        if urgency == "high" or pd_priority in ("P1", "P2"):
            severity = "critical"
        elif urgency == "low" or pd_priority in ("P3", "P4"):
            severity = "warning"
        else:
            severity = "info"

        # Extract service name from service object
        service_obj  = inc.get("service", {})
        service_name = service_obj.get("summary", service_obj.get("id", "unknown"))

        # Normalise service name to match microservice naming convention
        # e.g. "Payment Service" -> "payment-svc"
        svc_clean = (
            service_name.lower()
            .replace(" service", "-svc")
            .replace(" ", "-")
        )

        created_at = inc.get("created_at", "")
        try:
            ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            timestamp = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            timestamp = created_at

        pd_status = inc.get("status", "triggered")
        status    = "firing" if pd_status in ("triggered", "acknowledged") else "resolved"

        return Alert(
            alert_id   = self._make_alert_id(inc),
            alert_name = inc.get("title", "PagerDutyIncident"),
            service    = svc_clean,
            severity   = severity,
            timestamp  = timestamp,
            summary    = inc.get("summary", inc.get("title", "")),
            status     = status,
            labels     = {
                "incident_number": str(inc.get("incident_number", "")),
                "urgency":         urgency,
                "pd_status":       pd_status,
                "service_id":      service_obj.get("id", ""),
                "html_url":        inc.get("html_url", ""),
            },
            source = "pagerduty",
        )

    def list_active_alerts(
        self,
        severity: Optional[str] = None,
        service:  Optional[str] = None,
        limit:    int = 20,
    ) -> List[Alert]:
        params = {
            "statuses[]": ["triggered", "acknowledged"],
            "limit":      min(limit, 100),
            "sort_by":    "urgency:desc",
        }

        service_ids = self.config.get("service_ids", [])
        if service_ids:
            params["service_ids[]"] = service_ids

        try:
            data      = self._get("/incidents", params)
            incidents = data.get("incidents", [])
        except Exception as e:
            raise ConnectionError(f"PagerDuty API error: {e}")

        alerts = [self._parse_incident(inc) for inc in incidents]

        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if service:
            alerts = [a for a in alerts
                      if service.lower() in a.service.lower()]

        return alerts[:limit]

    def get_alert_detail(self, alert_id: str) -> Optional[Alert]:
        alerts = self.list_active_alerts(limit=100)
        return next((a for a in alerts if a.alert_id == alert_id), None)

    def get_alert_history(self, limit: int = 10) -> List[Alert]:
        try:
            data      = self._get("/incidents", {
                "statuses[]": ["resolved"],
                "limit":      min(limit, 25),
                "sort_by":    "resolved_at:desc",
            })
            incidents = data.get("incidents", [])
            return [self._parse_incident(inc) for inc in incidents[:limit]]
        except Exception:
            return []
