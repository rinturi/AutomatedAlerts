"""
models.py — Shared data models used across all connectors.

These are the normalised data shapes that all connectors must return.
The AI pipeline only ever sees these models — never the raw API responses
from Grafana, PagerDuty, Splunk, Jira etc.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class Alert:
    """
    Normalised alert from any alert source.
    AlertConnector implementations must return this shape.
    """
    alert_id:        str              # Unique ID (MD5 hash or provider ID)
    alert_name:      str              # e.g. "PaymentGatewayTimeouts"
    service:         str              # e.g. "payment-svc"
    severity:        str              # "critical" | "warning" | "info"
    timestamp:       str              # ISO 8601: "2026-04-26T01:03:47+00:00"
    summary:         str              # Human-readable description
    status:          str              # "firing" | "resolved"
    labels:          dict = field(default_factory=dict)   # Raw labels from source
    source:          str  = ""        # e.g. "grafana_alertmanager" | "pagerduty"


@dataclass
class RootCause:
    """
    Normalised root cause extracted from any log source.
    LogConnector implementations must return this shape.
    """
    found:           bool             # True if a matching log entry was found
    service:         str              # Service the log came from
    exception_type:  str  = ""        # e.g. "PaymentGatewayException"
    full_message:    str  = ""        # Full log line
    short_error:     str  = ""        # First sentence / truncated message
    timestamp:       Optional[str] = None   # ISO 8601 of the log entry
    level:           str  = "ERROR"
    recurrence_count: int = 0         # How many times seen in window
    source:          str  = ""        # e.g. "loki" | "splunk" | "cloudwatch"


@dataclass
class Issue:
    """
    Normalised issue/ticket from any issue tracker.
    IssueConnector implementations must return this shape.
    """
    issue_id:        str              # e.g. "KAN-5" | "INC0012345"
    summary:         str              # Issue title
    description:     str  = ""        # Full description
    resolution:      str  = ""        # Resolution text
    resolution_steps: List[str] = field(default_factory=list)
    status:          str  = ""        # "Done" | "Open" | "In Progress"
    priority:        str  = ""        # "High" | "Medium" | "Low"
    labels:          List[str] = field(default_factory=list)
    service:         str  = ""        # Service this issue belongs to
    exception_type:  str  = ""        # Exception class mapped to this issue
    created_at:      str  = ""        # ISO 8601
    updated_at:      str  = ""        # ISO 8601
    source:          str  = ""        # e.g. "jira_cloud" | "servicenow"


@dataclass
class CreatedIssue:
    """
    Result of creating a new issue in any tracker.
    """
    issue_id:   str   # Newly created issue ID
    url:        str   # Direct link to the issue
    source:     str   # Which connector created it
