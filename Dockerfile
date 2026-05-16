FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities
COPY shared/ ./shared/

# Copy service code (passed via build context per service)
ARG SERVICE_DIR
COPY ${SERVICE_DIR}/main.py ./main.py

EXPOSE 8000

CMD ["python", "main.py"]