"""
loki.py — LogConnector implementation for Grafana Loki.

Moves all Loki-specific code from kibana-mcp into this connector.
logs-mcp loads this at startup when config.yml logs.connector = loki.

Config keys:
    url   -- Loki base URL (default: http://loki:3100)
    auth  -- none | {"type": "basic", "username": "...", "password": "..."}
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from platform.connectors.base import LogConnector
from platform.connectors.models import RootCause

ALERT_EXCEPTION_MAP = {
    "PaymentDBPoolExhausted":      "SQLException",
    "PaymentGatewayTimeouts":      "PaymentGatewayException",
    "PaymentHighErrorRate":        ["DuplicateTransactionException", "InsufficientFundsException"],
    "AuthRedisDown":               "RedisConnectionException",
    "AuthBruteForceDetected":      "BruteForceException",
    "AuthHighErrorRate":           ["LDAPTimeoutException", "JWTSignatureException"],
    "LoanCreditBureauDown":        "CreditBureauTimeoutException",
    "LoanHighPendingApplications": ["RiskEngineException", "ArithmeticException"],
    "NotificationSMSGatewayDown":  "SMSGatewayException",
    "NotificationQueueOverflow":   "QueueOverflowException",
    "NotificationHighErrorRate":   ["SMTPConnectionException", "TemplateRenderException"],
}


class LokiConnector(LogConnector):

    def validate_config(self):
        if not self.config.get("url"):
            self.config["url"] = "http://loki:3100"

    @property
    def url(self) -> str:
        return self.config.get("url", "http://loki:3100").rstrip("/")

    def _query_loki(
        self,
        service: str,
        start_ts: datetime,
        end_ts: datetime,
        level_filter: Optional[str] = None,
        alert_name: Optional[str] = None,
    ) -> List[dict]:
        label_selector = f'{{service="{service}"}}'
        if level_filter:
            label_selector += f' |= "{level_filter}"'
        if alert_name and alert_name in ALERT_EXCEPTION_MAP:
            target  = ALERT_EXCEPTION_MAP[alert_name]
            targets = [target] if isinstance(target, str) else target
            label_selector += f' |= "{targets[0]}"'

        params = urllib.parse.urlencode({
            "query":     label_selector,
            "start":     str(int(start_ts.timestamp() * 1e9)),
            "end":       str(int(end_ts.timestamp() * 1e9)),
            "limit":     "50",
            "direction": "backward",
        })

        try:
            req = urllib.request.Request(
                f"{self.url}/loki/api/v1/query_range?{params}"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            return []

        logs = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                try:
                    entry = json.loads(line)
                except Exception:
                    entry = {"message": line}

                logs.append({
                    "timestamp":      datetime.fromtimestamp(
                                        int(ts_ns) / 1e9, tz=timezone.utc
                                      ).isoformat(),
                    "level":          entry.get("level", labels.get("level", "INFO")),
                    "service":        service,
                    "exception_type": (
                                      entry.get("exception_type")
                                      or labels.get("exception_type")
                                      or self.extract_exception_type(
                                           entry.get("message", line))
                                      or ""
                                      ),
                    "message":        entry.get("message", line[:200]),
                    "full_message":   line[:500],
                    "source":         "loki",
                })
        return logs

    def search_logs(
        self,
        service: str,
        level: str = "ERROR",
        alert_timestamp: Optional[str] = None,
        window_minutes: int = 5,
        limit: int = 20,
    ) -> List[dict]:
        if alert_timestamp:
            try:
                center = datetime.fromisoformat(
                    alert_timestamp.replace("Z", "+00:00")
                )
            except Exception:
                center = datetime.now(timezone.utc)
        else:
            center = datetime.now(timezone.utc)

        delta    = timedelta(minutes=window_minutes)
        start_ts = center - delta
        end_ts   = center + delta

        logs = self._query_loki(service, start_ts, end_ts, level_filter=level)
        return logs[:limit]

    def extract_root_cause(
        self,
        service: str,
        alert_name: str,
        alert_timestamp: Optional[str] = None,
        window_minutes: int = 5,
    ) -> RootCause:
        if alert_timestamp:
            try:
                center = datetime.fromisoformat(
                    alert_timestamp.replace("Z", "+00:00")
                )
            except Exception:
                center = datetime.now(timezone.utc)
        else:
            center = datetime.now(timezone.utc)

        delta = timedelta(minutes=window_minutes)
        logs  = self._query_loki(
            service,
            center - delta,
            center + delta,
            alert_name=alert_name,
        )

        # Widen window if nothing found
        if not logs:
            logs = self._query_loki(
                service,
                center - timedelta(minutes=window_minutes * 3),
                center + timedelta(minutes=window_minutes * 3),
                alert_name=alert_name,
            )

        # Filter to exception matching this alert
        if alert_name and alert_name in ALERT_EXCEPTION_MAP:
            target  = ALERT_EXCEPTION_MAP[alert_name]
            targets = [target] if isinstance(target, str) else target
            filtered = [l for l in logs if l.get("exception_type") in targets]
            if filtered:
                logs = filtered

        error_logs = [l for l in logs if l.get("level") == "ERROR"]
        if not error_logs:
            return RootCause(found=False, service=service, source="loki")

        error_logs.sort(key=lambda x: x.get("timestamp", ""))
        first = error_logs[0]

        return RootCause(
            found            = True,
            service          = service,
            exception_type   = first.get("exception_type", ""),
            full_message     = first.get("message", ""),
            short_error      = first.get("message", "")[:200],
            timestamp        = first.get("timestamp"),
            level            = "ERROR",
            recurrence_count = len(error_logs),
            source           = "loki",
        )
