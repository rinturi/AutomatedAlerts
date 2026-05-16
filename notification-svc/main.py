"""
notification-svc: Mock banking notification microservice.
Simulates: SMS gateway failures, email SMTP errors,
           push notification queue overflow, template rendering errors.
"""
import random
import sys
import time
import threading
from datetime import datetime, timezone

sys.path.insert(0, "/app/shared")
from log_utils import get_logger

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import uvicorn

SERVICE = "notification-svc"
logger = get_logger(SERVICE)
app = FastAPI(title="Notification Service", version="1.0.0")

# ---------- Prometheus metrics ----------
NOTIF_SENT = Counter("notification_sent_total", "Notifications sent", ["channel", "status"])
NOTIF_LATENCY = Histogram("notification_duration_seconds", "Notification latency")
ERROR_COUNT = Counter("notification_errors_total", "Notification errors", ["error_type"])
QUEUE_DEPTH = Gauge("notification_queue_depth", "Current queue depth")
SMS_GATEWAY_UP = Gauge("notification_sms_gateway_up", "SMS gateway health (1=up 0=down)")

# ---------- Simulated state ----------
queue_depth = 0
sms_gateway_up = True

ERROR_SCENARIOS = [
    ("sms_gateway_down", 0.12),
    ("smtp_connection_refused", 0.10),
    ("queue_overflow", 0.09),
    ("template_render_error", 0.07),
    ("push_token_invalid", 0.08),
    ("none", 0.54),
]


class NotificationRequest(BaseModel):
    notification_id: str
    customer_id: str
    channel: str = "SMS"       # SMS | EMAIL | PUSH
    template_id: str
    recipient: str
    payload: dict = {}


def pick_error():
    scenarios, weights = zip(*ERROR_SCENARIOS)
    return random.choices(scenarios, weights=weights, k=1)[0]


@app.get("/health")
def health():
    return {"status": "up", "service": SERVICE, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notifications/send")
def send_notification(req: NotificationRequest):
    global queue_depth, sms_gateway_up
    start = time.time()
    queue_depth += 1
    QUEUE_DEPTH.set(queue_depth)
    error = pick_error()

    try:
        if error == "sms_gateway_down":
            sms_gateway_up = False
            SMS_GATEWAY_UP.set(0)
            ERROR_COUNT.labels(error_type="sms_gateway_down").inc()
            logger.error(
                f"SMSGatewayException: SMS gateway at sms.vodafone-biz.internal:443 is unreachable. "
                f"HTTPConnectionPool(host='sms.vodafone-biz.internal', port=443): Max retries exceeded. "
                f"notification_id={req.notification_id}, customer_id={req.customer_id}, "
                f"recipient={req.recipient[:5]}***. Notification queued for retry.",
                extra={"notification_id": req.notification_id, "customer_id": req.customer_id, "channel": "SMS"}
            )
            raise HTTPException(status_code=503, detail="SMS gateway unavailable")

        elif error == "smtp_connection_refused":
            ERROR_COUNT.labels(error_type="smtp_connection_refused").inc()
            logger.error(
                f"SMTPConnectionException: Connection refused by SMTP relay smtp.bankcore.internal:587 "
                f"for notification_id={req.notification_id}. recipient={req.recipient}. "
                f"TLS handshake failed: certificate expired 2024-12-31. "
                f"Email delivery failed for customer_id={req.customer_id}.",
                extra={"notification_id": req.notification_id, "customer_id": req.customer_id, "channel": "EMAIL"}
            )
            raise HTTPException(status_code=503, detail="SMTP relay unavailable")

        elif error == "queue_overflow":
            ERROR_COUNT.labels(error_type="queue_overflow").inc()
            logger.error(
                f"QueueOverflowException: Notification queue depth {queue_depth} exceeded max capacity 500. "
                f"Dropping notification_id={req.notification_id} for customer_id={req.customer_id}. "
                f"channel={req.channel}, template={req.template_id}. "
                f"Kafka topic bank.notifications.outbound partition lag: 12000 messages.",
                extra={"notification_id": req.notification_id, "queue_depth": queue_depth}
            )
            raise HTTPException(status_code=429, detail="Queue capacity exceeded")

        elif error == "template_render_error":
            ERROR_COUNT.labels(error_type="template_render_error").inc()
            logger.error(
                f"TemplateRenderException: Jinja2 template '{req.template_id}' failed to render. "
                f"KeyError: 'account_balance' missing in payload for notification_id={req.notification_id}. "
                f"customer_id={req.customer_id}, channel={req.channel}. "
                f"Template requires: ['account_balance', 'transaction_id', 'timestamp'].",
                extra={"notification_id": req.notification_id, "template_id": req.template_id}
            )
            raise HTTPException(status_code=500, detail="Template rendering failed")

        elif error == "push_token_invalid":
            ERROR_COUNT.labels(error_type="push_token_invalid").inc()
            logger.error(
                f"FCMTokenException: Firebase push token for customer_id={req.customer_id} is invalid or expired. "
                f"FCM error_code=UNREGISTERED. notification_id={req.notification_id}. "
                f"Device token deregistered — removing from customer profile.",
                extra={"notification_id": req.notification_id, "customer_id": req.customer_id}
            )
            raise HTTPException(status_code=400, detail="Push token invalid")

        # Happy path
        sms_gateway_up = True
        SMS_GATEWAY_UP.set(1)
        NOTIF_SENT.labels(channel=req.channel, status="sent").inc()
        logger.info(
            f"Notification delivered: notification_id={req.notification_id}, "
            f"customer_id={req.customer_id}, channel={req.channel}, "
            f"template={req.template_id}, recipient={req.recipient[:5]}***"
        )
        return {"status": "delivered", "notification_id": req.notification_id}

    finally:
        NOTIF_LATENCY.observe(time.time() - start)
        queue_depth = max(0, queue_depth - 1)
        QUEUE_DEPTH.set(queue_depth)


def synthetic_load():
    import requests as req_lib
    time.sleep(8)
    channels = ["SMS", "EMAIL", "PUSH"]
    templates = ["TXN_ALERT", "LOGIN_OTP", "LOAN_STATUS", "EMI_REMINDER", "BALANCE_ALERT"]
    nid = 9000
    while True:
        try:
            cust = f"CUST{random.randint(1000, 9999)}"
            req_lib.post("http://localhost:8004/notifications/send", json={
                "notification_id": f"NOTIF{nid:08d}",
                "customer_id": cust,
                "channel": random.choice(channels),
                "template_id": random.choice(templates),
                "recipient": f"+91{random.randint(7000000000, 9999999999)}",
                "payload": {"account_balance": random.uniform(1000, 100000), "transaction_id": f"TXN{nid}"}
            }, timeout=5)
            nid += 1
        except Exception:
            pass
        time.sleep(random.uniform(1, 3))


if __name__ == "__main__":
    threading.Thread(target=synthetic_load, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8004)