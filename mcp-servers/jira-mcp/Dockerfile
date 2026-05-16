FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY jira_store.json .

EXPOSE 9003

CMD ["python", "server.py"]
