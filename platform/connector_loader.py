"""
connector_loader.py — Reads config.yml and instantiates the correct connector.

Usage (in any MCP server):
    from platform.connector_loader import load_alert_connector
    from platform.connector_loader import load_log_connector
    from platform.connector_loader import load_issue_connector

    alert_connector = load_alert_connector()   # reads CONFIG_PATH env var
    log_connector   = load_log_connector()
    issue_connector = load_issue_connector()
"""

import os
import yaml
import logging
from typing import Optional

logger = logging.getLogger("connector_loader")

# Default config path — override via CONFIG_PATH env var
DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.yml")


def _load_config(config_path: Optional[str] = None) -> dict:
    """Load and parse config.yml."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"config.yml not found at {path}. "
            f"Set CONFIG_PATH env var or place config.yml at {path}"
        )
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    logger.info(f"Loaded config from {path}")
    return config


def load_alert_connector(config_path: Optional[str] = None):
    """
    Load and return the AlertConnector implementation specified in config.yml.

    Supported connector values (config.yml alerts.connector):
        grafana_alertmanager  -- Grafana Alertmanager (default)
        pagerduty             -- PagerDuty Events API
        datadog               -- Datadog Monitors API
        opsgenie              -- OpsGenie Alerts API
        custom_webhook        -- Generic webhook receiver

    Returns:
        An instance of the appropriate AlertConnector subclass.
    """
    config      = _load_config(config_path)
    alerts_cfg  = config.get("alerts", {})
    connector   = alerts_cfg.get("connector", "grafana_alertmanager")
    conn_config = alerts_cfg.get("config", {})

    logger.info(f"Loading alert connector: {connector}")

    if connector == "grafana_alertmanager":
        from platform.connectors.alerts.grafana_alertmanager import GrafanaAlertmanagerConnector
        return GrafanaAlertmanagerConnector(conn_config)

    elif connector == "pagerduty":
        from platform.connectors.alerts.pagerduty import PagerDutyConnector
        return PagerDutyConnector(conn_config)

    elif connector == "datadog":
        from platform.connectors.alerts.datadog import DatadogAlertConnector
        return DatadogAlertConnector(conn_config)

    elif connector == "opsgenie":
        from platform.connectors.alerts.opsgenie import OpsGenieConnector
        return OpsGenieConnector(conn_config)

    elif connector == "custom_webhook":
        from platform.connectors.alerts.custom_webhook import CustomWebhookConnector
        return CustomWebhookConnector(conn_config)

    else:
        raise ValueError(
            f"Unknown alert connector: '{connector}'. "
            f"Supported: grafana_alertmanager, pagerduty, datadog, opsgenie, custom_webhook"
        )


def load_log_connector(config_path: Optional[str] = None):
    """
    Load and return the LogConnector implementation specified in config.yml.

    Supported connector values (config.yml logs.connector):
        loki           -- Grafana Loki (default)
        splunk         -- Splunk REST API
        elasticsearch  -- Elasticsearch / OpenSearch
        cloudwatch     -- AWS CloudWatch Logs
        datadog_logs   -- Datadog Logs API

    Returns:
        An instance of the appropriate LogConnector subclass.
    """
    config     = _load_config(config_path)
    logs_cfg   = config.get("logs", {})
    connector  = logs_cfg.get("connector", "loki")
    conn_config = logs_cfg.get("config", {})

    logger.info(f"Loading log connector: {connector}")

    if connector == "loki":
        from platform.connectors.logs.loki import LokiConnector
        return LokiConnector(conn_config)

    elif connector == "splunk":
        from platform.connectors.logs.splunk import SplunkConnector
        return SplunkConnector(conn_config)

    elif connector == "elasticsearch":
        from platform.connectors.logs.elasticsearch import ElasticsearchConnector
        return ElasticsearchConnector(conn_config)

    elif connector == "cloudwatch":
        from platform.connectors.logs.cloudwatch import CloudWatchConnector
        return CloudWatchConnector(conn_config)

    elif connector == "datadog_logs":
        from platform.connectors.logs.datadog_logs import DatadogLogsConnector
        return DatadogLogsConnector(conn_config)

    else:
        raise ValueError(
            f"Unknown log connector: '{connector}'. "
            f"Supported: loki, splunk, elasticsearch, cloudwatch, datadog_logs"
        )


def load_issue_connector(config_path: Optional[str] = None):
    """
    Load and return the IssueConnector implementation specified in config.yml.

    Supported connector values (config.yml issues.connector):
        jira_cloud    -- Jira Cloud REST API v3 (default)
        servicenow    -- ServiceNow Table API
        linear        -- Linear GraphQL API
        github_issues -- GitHub REST API
        azure_devops  -- Azure DevOps Work Items

    Returns:
        An instance of the appropriate IssueConnector subclass.
    """
    config      = _load_config(config_path)
    issues_cfg  = config.get("issues", {})
    connector   = issues_cfg.get("connector", "jira_cloud")
    conn_config = issues_cfg.get("config", {})

    logger.info(f"Loading issue connector: {connector}")

    if connector == "jira_cloud":
        from platform.connectors.issues.jira_cloud import JiraCloudConnector
        return JiraCloudConnector(conn_config)

    elif connector == "servicenow":
        from platform.connectors.issues.servicenow import ServiceNowConnector
        return ServiceNowConnector(conn_config)

    elif connector == "linear":
        from platform.connectors.issues.linear import LinearConnector
        return LinearConnector(conn_config)

    elif connector == "github_issues":
        from platform.connectors.issues.github_issues import GitHubIssuesConnector
        return GitHubIssuesConnector(conn_config)

    elif connector == "azure_devops":
        from platform.connectors.issues.azure_devops import AzureDevOpsConnector
        return AzureDevOpsConnector(conn_config)

    else:
        raise ValueError(
            f"Unknown issue connector: '{connector}'. "
            f"Supported: jira_cloud, servicenow, linear, github_issues, azure_devops"
        )


def get_platform_config(config_path: Optional[str] = None) -> dict:
    """
    Return the full parsed config.yml as a dict.
    Useful for reading pipeline settings, LLM config etc.
    """
    return _load_config(config_path)


def get_llm_config(config_path: Optional[str] = None) -> dict:
    """Return the llm block from config.yml."""
    return _load_config(config_path).get("llm", {})


def get_embedding_config(config_path: Optional[str] = None) -> dict:
    """Return the embeddings block from config.yml."""
    return _load_config(config_path).get("embeddings", {})


def get_pipeline_config(config_path: Optional[str] = None) -> dict:
    """Return the pipeline block from config.yml."""
    return _load_config(config_path).get("pipeline", {})
