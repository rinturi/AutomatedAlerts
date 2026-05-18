FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY mcp-servers/logs-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY platform/ /app/platform/
COPY config.yml /app/config.yml
COPY mcp-servers/logs-mcp/server.py .

EXPOSE 9002
CMD ["python", "server.py"]
