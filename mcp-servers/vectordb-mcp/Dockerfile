FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pin numpy first — chromadb 0.4.24 uses np.float_ removed in numpy 2.0
RUN pip install --no-cache-dir numpy==1.26.4

# Install remaining dependencies — no openai package needed (using urllib)
RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    "uvicorn[standard]==0.30.1" \
    pydantic==2.7.1 \
    chromadb==0.4.24 \
    "overrides==7.4.0"

COPY server.py .
COPY jira_store.json .

VOLUME ["/app/chroma_data"]
EXPOSE 9004
CMD ["python", "server.py"]
