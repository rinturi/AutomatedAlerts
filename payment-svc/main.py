"""
payment-svc: Mock banking payment microservice.
Simulates: DB connection pool exhaustion, payment gateway timeouts,
           duplicate transaction detection, insufficient funds errors.
"""
import os
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

SERVICE = "payment-svc"
logger = get_logger(SERVICE)
app = FastAPI(title="Payment Service", version="1.0.0")

# ---------- Prometheus metrics ----------
REQUEST_COUNT = Counter("payment_requests_total", "Total payment requests", ["status"])
REQUEST_LATENCY = Histogram("payment_request_duration_seconds", "Request latency")
ERROR_COUNT = Counter("payment_errors_total", "Total errors", ["error_type"])
DB_POOL_GAUGE = Gauge("payment_db_pool_available", "Available DB connections")
ACTIVE_TRANSACTIONS = Gauge("payment_active_transactions", "Active transactions")

# ---------- Simulated state ----------
DB_POOL_SIZE = 10
db_pool_available = DB_POOL_SIZE
transaction_store: dict = {}

# ---------- Error scenarios ----------
ERROR_SCENARIOS = [
    ("db_pool_exhausted", 0.15),
    ("payment_gateway_timeout", 0.12),
    ("duplicate_transaction", 0.08),
    ("insufficient_funds", 0.10),
    ("card_expired", 0.07),
    ("none", 0.48),
]


class PaymentRequest(BaseModel):
    transaction_id: str
    account_id: str
    amount: float
    currency: str = "INR"
    merchant_id: str


def pick_error():
    scenarios, weights = zip(*ERROR_SCENARIOS)
    return random.choices(scenarios, weights=weights, k=1)[0]


@app.get("/health")
def health():
    return {"status": "up", "service": SERVICE, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/payments/process")
def process_payment(req: PaymentRequest):
    global db_pool_available
    start = time.time()
    ACTIVE_TRANSACTIONS.inc()

    error = pick_error()

    try:
        if error == "db_pool_exhausted":
            db_pool_available = max(0, db_pool_available - random.randint(3, 6))
            DB_POOL_GAUGE.set(db_pool_available)
            ERROR_COUNT.labels(error_type="db_pool_exhausted").inc()
            logger.error(
                "java.sql.SQLException: Connection pool exhausted - no available connections in pool "
                f"(pool_size={DB_POOL_SIZE}, available={db_pool_available}, "
                f"transaction_id={req.transaction_id}, account_id={req.account_id})",
                extra={"transaction_id": req.transaction_id, "pool_available": db_pool_available}
            )
            raise HTTPException(status_code=503, detail="DB connection pool exhausted")

        elif error == "payment_gateway_timeout":
            ERROR_COUNT.labels(error_type="gateway_timeout").inc()
            logger.error(
                f"PaymentGatewayException: Upstream gateway timeout after 30000ms - "
                f"merchant_id={req.merchant_id}, transaction_id={req.transaction_id}, "
                f"amount={req.amount} {req.currency}. Gateway host: pg.bankcore.internal:8443",
                extra={"transaction_id": req.transaction_id, "merchant_id": req.merchant_id}
            )
            raise HTTPException(status_code=504, detail="Payment gateway timeout")

        elif error == "duplicate_transaction":
            if req.transaction_id in transaction_store:
                ERROR_COUNT.labels(error_type="duplicate_transaction").inc()
                logger.error(
                    f"DuplicateTransactionException: Transaction {req.transaction_id} already processed "
                    f"at {transaction_store[req.transaction_id]}. Rejecting duplicate for "
                    f"account_id={req.account_id}, amount={req.amount}",
                    extra={"transaction_id": req.transaction_id}
                )
                raise HTTPException(status_code=409, detail="Duplicate transaction")

        elif error == "insufficient_funds":
            ERROR_COUNT.labels(error_type="insufficient_funds").inc()
            logger.error(
                f"InsufficientFundsException: Account {req.account_id} balance insufficient "
                f"for debit of {req.amount} {req.currency}. Available: {random.uniform(10, req.amount - 1):.2f}",
                extra={"account_id": req.account_id, "amount": req.amount}
            )
            raise HTTPException(status_code=402, detail="Insufficient funds")

        elif error == "card_expired":
            ERROR_COUNT.labels(error_type="card_expired").inc()
            logger.error(
                f"CardExpiredException: Card linked to account {req.account_id} has expired. "
                f"transaction_id={req.transaction_id}",
                extra={"account_id": req.account_id}
            )
            raise HTTPException(status_code=402, detail="Card expired")

        # Happy path
        transaction_store[req.transaction_id] = datetime.now(timezone.utc).isoformat()
        db_pool_available = min(DB_POOL_SIZE, db_pool_available + 1)
        DB_POOL_GAUGE.set(db_pool_available)
        REQUEST_COUNT.labels(status="success").inc()
        logger.info(
            f"Payment processed successfully: transaction_id={req.transaction_id}, "
            f"account_id={req.account_id}, amount={req.amount} {req.currency}"
        )
        return {"status": "success", "transaction_id": req.transaction_id}

    finally:
        REQUEST_LATENCY.observe(time.time() - start)
        ACTIVE_TRANSACTIONS.dec()


def synthetic_load():
    """Continuously generate synthetic payment traffic."""
    import requests as req_lib
    time.sleep(5)
    accounts = [f"ACC{i:06d}" for i in range(1, 20)]
    merchants = ["MERCHANT_HDFC", "MERCHANT_ICICI", "MERCHANT_SBI", "MERCHANT_AXIS"]
    tid = 1000
    while True:
        try:
            payload = {
                "transaction_id": f"TXN{tid:08d}",
                "account_id": random.choice(accounts),
                "amount": round(random.uniform(100, 50000), 2),
                "currency": "INR",
                "merchant_id": random.choice(merchants)
            }
            req_lib.post("http://localhost:8001/payments/process", json=payload, timeout=5)
            tid += 1
        except Exception:
            pass
        time.sleep(random.uniform(2, 6))


if __name__ == "__main__":
    threading.Thread(target=synthetic_load, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8001)