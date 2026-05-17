"""
base.py — Abstract base classes for all connectors.

Every connector (grafana_alertmanager, pagerduty, loki, splunk, jira_cloud, servicenow...)
must implement one of these three abstract classes.

The MCP servers (alert-mcp, logs-mcp, issues-mcp) load the correct
connector at startup via connector_loader.py and call these methods.
The AI agents never know which connector is underneath.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from .models import Alert, RootCause, Issue, CreatedIssue


# ── Alert Source Connector ────────────────────────────────────────────────────

class AlertConnector(ABC):
    """
    Abstract base class for all alert source connectors.

    Implementations:
        grafana_alertmanager  -- Grafana Alertmanager REST API
        pagerduty             -- PagerDuty Events API v2
        datadog               -- Datadog Monitors API
        opsgenie              -- OpsGenie Alerts API
        custom_webhook        -- Generic webhook receiver

    The alert-mcp server loads one implementation at startup
    based on config.yml alerts.connector value.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The alerts.config block from config.yml.
                    e.g. {"url": "http://alertmanager:9093"}
                    or   {"api_key": "...", "service_ids": ["P1ABC"]}
        """
        self.config = config
        self.validate_config()

    def validate_config(self):
        """
        Override to validate required config keys on startup.
        Raise ValueError with a clear message if anything is missing.
        """
        pass

    @abstractmethod
    def list_active_alerts(
        self,
        severity: Optional[str] = None,
        service: Optional[str] = None,
        limit: int = 20,
    ) -> List[Alert]:
        """
        Return all currently FIRING alerts.

        Args:
            severity: Filter by severity ("critical" | "warning" | None for all)
            service:  Filter by service name (None for all)
            limit:    Maximum number of alerts to return

        Returns:
            List of Alert objects, newest first.
            Empty list if no alerts firing.
        """
        ...

    @abstractmethod
    def get_alert_detail(self, alert_id: str) -> Optional[Alert]:
        """
        Return full detail for a specific alert.

        Args:
            alert_id: The alert_id from list_active_alerts()

        Returns:
            Alert object, or None if not found.
        """
        ...

    def get_alert_history(self, limit: int = 20) -> List[Alert]:
        """
        Return recently resolved alerts.
        Optional — connectors that don't support history can return [].
        """
        return []

    @property
    def connector_name(self) -> str:
        """Return the connector identifier string."""
        return self.__class__.__name__.lower().replace("connector", "")


# ── Log Source Connector ──────────────────────────────────────────────────────

class LogConnector(ABC):
    """
    Abstract base class for all log source connectors.

    Implementations:
        loki           -- Grafana Loki HTTP API (LogQL)
        splunk         -- Splunk REST API (SPL queries)
        elasticsearch  -- Elasticsearch REST API (Query DSL)
        cloudwatch     -- AWS CloudWatch Logs (filter pattern)
        datadog_logs   -- Datadog Logs API

    The logs-mcp server loads one implementation at startup
    based on config.yml logs.connector value.
    """

    def __init__(self, config: dict):
        self.config = config
        self.validate_config()

    def validate_config(self):
        pass

    @abstractmethod
    def extract_root_cause(
        self,
        service: str,
        alert_name: str,
        alert_timestamp: Optional[str] = None,
        window_minutes: int = 5,
    ) -> RootCause:
        """
        Find the most relevant ERROR log entry for a firing alert.

        Args:
            service:         Service name (e.g. "payment-svc")
            alert_name:      Alert name for exception type hint
                             (e.g. "PaymentGatewayTimeouts")
            alert_timestamp: ISO 8601 timestamp to search around.
                             If None, use current time.
            window_minutes:  Search window: alert_timestamp +/- window_minutes

        Returns:
            RootCause with found=True if a matching log found,
            found=False with empty fields if nothing found.
        """
        ...

    @abstractmethod
    def search_logs(
        self,
        service: str,
        level: str = "ERROR",
        alert_timestamp: Optional[str] = None,
        window_minutes: int = 5,
        limit: int = 20,
    ) -> List[dict]:
        """
        Return raw log entries matching the criteria.

        Returns:
            List of dicts with at minimum:
            {"timestamp": str, "level": str, "message": str, "service": str}
        """
        ...

    def get_error_summary(self, service: str) -> dict:
        """
        Return grouped error counts by exception type for a service.
        Optional — returns empty dict if not implemented.
        """
        return {}

    @staticmethod
    def extract_exception_type(message: str) -> str:
        """
        Parse exception class name from the start of a log message.
        e.g. "PaymentGatewayException: Upstream gateway timeout..."
              -> "PaymentGatewayException"

        Shared utility — all log connectors can use this.
        """
        import re
        match = re.match(
            r'^([A-Z][a-zA-Z]+Exception|[A-Z][a-zA-Z]+Error)',
            message or ""
        )
        return match.group(1) if match else ""

    @property
    def connector_name(self) -> str:
        return self.__class__.__name__.lower().replace("connector", "")


# ── Issue Tracker Connector ───────────────────────────────────────────────────

class IssueConnector(ABC):
    """
    Abstract base class for all issue tracker connectors.

    Implementations:
        jira_cloud    -- Jira Cloud REST API v3
        servicenow    -- ServiceNow Table API
        linear        -- Linear GraphQL API
        github_issues -- GitHub REST API
        azure_devops  -- Azure DevOps Work Items API

    The issues-mcp server loads one implementation at startup
    based on config.yml issues.connector value.
    """

    def __init__(self, config: dict):
        self.config = config
        self.validate_config()

    def validate_config(self):
        pass

    @abstractmethod
    def search_issues(
        self,
        query: str,
        service: Optional[str] = None,
        min_score: float = 0.25,
        limit: int = 5,
    ) -> List[Issue]:
        """
        Search for existing issues matching the error description.

        Args:
            query:     Error message or exception type to search for
            service:   Filter by service label/component (None for all)
            min_score: Minimum relevance score (0.0-1.0)
            limit:     Maximum results to return

        Returns:
            List of Issue objects sorted by relevance, best match first.
        """
        ...

    @abstractmethod
    def get_issue(self, issue_id: str) -> Optional[Issue]:
        """
        Fetch full detail for a specific issue.

        Args:
            issue_id: Provider-specific ID (e.g. "KAN-5" / "INC0012345")

        Returns:
            Issue object, or None if not found.
        """
        ...

    @abstractmethod
    def create_issue(
        self,
        summary: str,
        description: str,
        service: str,
        priority: str = "High",
        labels: Optional[List[str]] = None,
    ) -> CreatedIssue:
        """
        Create a new issue in the tracker.

        Args:
            summary:     Issue title (one line)
            description: Full incident description (markdown)
            service:     Affected service name
            priority:    "Critical" | "High" | "Medium" | "Low"
            labels:      List of label strings to apply

        Returns:
            CreatedIssue with the new issue_id and url.
        """
        ...

    def list_issues(
        self,
        service: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Issue]:
        """
        List issues with optional filters.
        Optional — returns [] if not implemented.
        """
        return []

    @property
    def connector_name(self) -> str:
        return self.__class__.__name__.lower().replace("connector", "")
