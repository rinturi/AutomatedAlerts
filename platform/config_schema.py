"""
config_schema.py — Validates config.yml on startup.

Called by connector_loader before instantiating any connector.
Raises clear errors if required fields are missing.
"""

VALID_ALERT_CONNECTORS  = {"grafana_alertmanager", "pagerduty", "datadog",
                            "opsgenie", "custom_webhook"}
VALID_LOG_CONNECTORS    = {"loki", "splunk", "elasticsearch",
                            "cloudwatch", "datadog_logs"}
VALID_ISSUE_CONNECTORS  = {"jira_cloud", "servicenow", "linear",
                            "github_issues", "azure_devops"}
VALID_LLM_PROVIDERS     = {"openai", "anthropic", "azure_openai",
                            "bedrock", "ollama"}
VALID_EMBED_PROVIDERS   = {"openai", "cohere", "ollama", "huggingface"}
VALID_VECTORDB_PROVIDERS= {"chromadb", "qdrant", "pinecone", "weaviate"}


def validate(config: dict) -> None:
    """
    Validate the full config.yml dict.
    Raises ValueError with a clear message on any issue.
    """
    errors = []

    # Organisation
    if not config.get("organisation", {}).get("name"):
        errors.append("organisation.name is required")

    # Alerts
    alert_conn = config.get("alerts", {}).get("connector")
    if not alert_conn:
        errors.append("alerts.connector is required")
    elif alert_conn not in VALID_ALERT_CONNECTORS:
        errors.append(
            f"alerts.connector '{alert_conn}' is not valid. "
            f"Choose from: {sorted(VALID_ALERT_CONNECTORS)}"
        )

    # Logs
    log_conn = config.get("logs", {}).get("connector")
    if not log_conn:
        errors.append("logs.connector is required")
    elif log_conn not in VALID_LOG_CONNECTORS:
        errors.append(
            f"logs.connector '{log_conn}' is not valid. "
            f"Choose from: {sorted(VALID_LOG_CONNECTORS)}"
        )

    # Issues
    issue_conn = config.get("issues", {}).get("connector")
    if not issue_conn:
        errors.append("issues.connector is required")
    elif issue_conn not in VALID_ISSUE_CONNECTORS:
        errors.append(
            f"issues.connector '{issue_conn}' is not valid. "
            f"Choose from: {sorted(VALID_ISSUE_CONNECTORS)}"
        )

    # LLM
    llm_provider = config.get("llm", {}).get("provider")
    if not llm_provider:
        errors.append("llm.provider is required")
    elif llm_provider not in VALID_LLM_PROVIDERS:
        errors.append(
            f"llm.provider '{llm_provider}' is not valid. "
            f"Choose from: {sorted(VALID_LLM_PROVIDERS)}"
        )

    # Embeddings
    embed_provider = config.get("embeddings", {}).get("provider")
    if not embed_provider:
        errors.append("embeddings.provider is required")
    elif embed_provider not in VALID_EMBED_PROVIDERS:
        errors.append(
            f"embeddings.provider '{embed_provider}' is not valid. "
            f"Choose from: {sorted(VALID_EMBED_PROVIDERS)}"
        )

    # Dimension mismatch warning
    embed_dims = config.get("embeddings", {}).get("dimensions")
    if embed_dims:
        known_dims = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "embed-english-v3.0":     1024,
            "nomic-embed-text":        768,
            "all-MiniLM-L6-v2":        384,
        }
        model = config.get("embeddings", {}).get("model", "")
        expected = known_dims.get(model)
        if expected and int(embed_dims) != expected:
            errors.append(
                f"embeddings.dimensions is {embed_dims} but "
                f"model '{model}' produces {expected}-dim vectors. "
                f"Fix: set dimensions: {expected}"
            )

    if errors:
        raise ValueError(
            "config.yml validation failed:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )
