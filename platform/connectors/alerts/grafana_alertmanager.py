"""
grafana_alertmanager.py — AlertConnector for Grafana Alertmanager.

Moves all Alertmanager-specific code out of the MCP server
into this connector. The alert-mcp server calls the standard
AlertConnector interface and never knows which system is underneath.

Config keys (from config.yml alerts.config):
    url   -- Alertmanager base URL (default: http://alertmanager:9093)
    auth  -- "none" | {"type": "basic", "username": "...", "password": "..."}
"""

import hashlib
import httpx
from datetime import datetime, timezone
from typing import List, Optional

from platform.connectors.base import AlertConnector
from platform.connectors.models import Alert

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class GrafanaAlertmanagerConnector(AlertConnector):

    def validate_config(self):
        if not self.config.get("url"):
            self.config["url"] = "http://alertmanager:9093"

    @property
    def url(self) -> str:
        return self.config.get("url", "http://alertmanager:9093").rstrip("/")

    def _make_alert_id(self, raw: dict) -> str:
        key = str(sorted(raw.get("labels", {}).items()))
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _parse_raw(self, raw: dict) -> Alert:
        labels      = raw.get("labels", {})
        annotations = raw.get("annotations", {})
        starts_at   = raw.get("startsAt", "")

        try:
            ts = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            timestamp = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            timestamp = starts_at

        return Alert(
            alert_id    = self._make_alert_id(raw),
            alert_name  = labels.get("alertname", "unknown"),
            service     = labels.get("service", labels.get("job", "unknown")),
            severity    = labels.get("severity", "unknown"),
            timestamp   = timestamp,
            summary     = annotations.get("summary", ""),
            status      = raw.get("status", {}).get("state", "firing"),
            labels      = labels,
            source      = "grafana_alertmanager",
        )

    def list_active_alerts(
        self,
        severity: Optional[str] = None,
        service:  Optional[str] = None,
        limit:    int = 20,
    ) -> List[Alert]:
        try:
            resp = httpx.get(
                f"{self.url}/api/v2/alerts",
                params={"active": "true", "silenced": "false", "inhibited": "false"},
                timeout=10.0
            )
            resp.raise_for_status()
            raw_list = resp.json()
        except httpx.ConnectError:
            raise ConnectionError(f"Cannot reach Alertmanager at {self.url}")
        except Exception as e:
            raise RuntimeError(f"Alertmanager error: {e}")

        alerts = [self._parse_raw(a) for a in raw_list]

        if severity:
            alerts = [a for a in alerts if a.severity.lower() == severity.lower()]
        if service:
            alerts = [a for a in alerts if service.lower() in a.service.lower()]

        alerts.sort(key=lambda a: SEVERITY_ORDER.get(a.severity, 99))
        return alerts[:limit]

    def get_alert_detail(self, alert_id: str) -> Optional[Alert]:
        alerts = self.list_active_alerts(limit=100)
        return next((a for a in alerts if a.alert_id == alert_id), None)

    def get_alert_history(self, limit: int = 10) -> List[Alert]:
        try:
            resp = httpx.get(
                f"{self.url}/api/v2/alerts",
                params={"active": "false", "silenced": "true", "inhibited": "true"},
                timeout=10.0
            )
            resp.raise_for_status()
            raw_list = resp.json()
        except Exception:
            return []

        resolved = [self._parse_raw(a) for a in raw_list
                    if a.get("status", {}).get("state") != "active"]
        return resolved[:limit]
