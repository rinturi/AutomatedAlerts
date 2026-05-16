"""
Shared logging utilities for all banking mock microservices.
Produces structured JSON logs compatible with ELK stack.
"""
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
 
 
class JsonFormatter(logging.Formatter):
    """Formats log records as JSON for Elasticsearch ingestion."""
 
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name
 
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.thread,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            log_obj["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        if hasattr(record, "extra"):
            log_obj.update(record.extra)
        return json.dumps(log_obj)
 
 
def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    logger.handlers = [handler]
    logger.propagate = False
    return logger