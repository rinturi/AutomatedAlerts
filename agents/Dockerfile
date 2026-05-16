FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY state.py .
COPY mcp_client.py .
COPY llm_client.py .
COPY agent_alert_monitor.py .
COPY agent_jira_resolver.py .
COPY agent_dashboard_writer.py .
COPY orchestrator.py .

CMD ["python", "orchestrator.py"]
