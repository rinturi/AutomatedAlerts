FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY mcp-servers/alert-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy platform layer (connectors + loader)
COPY platform/ /app/platform/

# Copy config
COPY config.yml /app/config.yml

# Copy server
COPY mcp-servers/alert-mcp/server.py .

EXPOSE 9001
CMD ["python", "server.py"]
