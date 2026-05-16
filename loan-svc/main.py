"""
loan-svc: Mock banking loan processing microservice.
Simulates: Credit bureau API timeouts, EMI calculation overflow,
           risk engine failures, document parsing errors.
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

SERVICE = "loan-svc"
logger = get_logger(SERVICE)
app = FastAPI(title="Loan Service", version="1.0.0")

# ---------- Prometheus metrics ----------
LOAN_REQUESTS = Counter("loan_requests_total", "Loan application requests", ["status"])
LOAN_LATENCY = Histogram("loan_duration_seconds", "Loan processing latency")
ERROR_COUNT = Counter("loan_errors_total", "Loan errors", ["error_type"])
BUREAU_TIMEOUT_RATE = Gauge("loan_bureau_timeout_rate", "Credit bureau timeout rate (last 5 min)")
PENDING_APPLICATIONS = Gauge("loan_pending_applications", "Applications pending processing")

ERROR_SCENARIOS = [
    ("credit_bureau_timeout", 0.14),
    ("risk_engine_unavailable", 0.10),
    ("emi_calculation_overflow", 0.06),
    ("document_parse_failure", 0.09),
    ("kyc_verification_failed", 0.08),
    ("none", 0.53),
]

pending_count = 0
bureau_timeouts = 0


class LoanApplication(BaseModel):
    application_id: str
    customer_id: str
    loan_amount: float
    tenure_months: int
    loan_type: str = "HOME_LOAN"
    annual_income: float = 600000.0


def pick_error():
    scenarios, weights = zip(*ERROR_SCENARIOS)
    return random.choices(scenarios, weights=weights, k=1)[0]


@app.get("/health")
def health():
    return {"status": "up", "service": SERVICE, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/loans/apply")
def apply_loan(req: LoanApplication):
    global pending_count, bureau_timeouts
    start = time.time()
    pending_count += 1
    PENDING_APPLICATIONS.set(pending_count)
    error = pick_error()

    try:
        if error == "credit_bureau_timeout":
            bureau_timeouts += 1
            rate = min(1.0, bureau_timeouts / 10)
            BUREAU_TIMEOUT_RATE.set(rate)
            ERROR_COUNT.labels(error_type="credit_bureau_timeout").inc()
            logger.error(
                f"CreditBureauTimeoutException: CIBIL API call timed out after 15000ms for "
                f"customer_id={req.customer_id}, application_id={req.application_id}. "
                f"Endpoint: https://cibil.bankcore.internal/v2/score. "
                f"bureau_timeout_count_last_5min={bureau_timeouts}. Cannot proceed without credit score.",
                extra={"customer_id": req.customer_id, "application_id": req.application_id}
            )
            raise HTTPException(status_code=504, detail="Credit bureau timeout")

        elif error == "risk_engine_unavailable":
            ERROR_COUNT.labels(error_type="risk_engine_unavailable").inc()
            logger.error(
                f"RiskEngineException: Risk scoring engine at risk.bankcore.internal:9090 returned "
                f"HTTP 503 for application_id={req.application_id}. "
                f"RiskEngine pod count=0 (all replicas crashed). loan_amount={req.loan_amount}, "
                f"loan_type={req.loan_type}. Application queued for retry.",
                extra={"application_id": req.application_id, "loan_amount": req.loan_amount}
            )
            raise HTTPException(status_code=503, detail="Risk engine unavailable")

        elif error == "emi_calculation_overflow":
            ERROR_COUNT.labels(error_type="emi_calculation_overflow").inc()
            logger.error(
                f"ArithmeticException: EMI calculation overflow for application_id={req.application_id}. "
                f"loan_amount={req.loan_amount}, tenure={req.tenure_months} months, "
                f"computed_emi exceeded long integer boundary. "
                f"Possible cause: invalid tenure_months=0 passed in request. StackTrace: "
                f"LoanCalculator.computeEMI(LoanCalculator.java:142)",
                extra={"application_id": req.application_id, "tenure_months": req.tenure_months}
            )
            raise HTTPException(status_code=422, detail="EMI calculation error")

        elif error == "document_parse_failure":
            doc_types = ["AADHAR", "PAN", "INCOME_PROOF", "BANK_STATEMENT"]
            doc = random.choice(doc_types)
            ERROR_COUNT.labels(error_type="document_parse_failure").inc()
            logger.error(
                f"DocumentParseException: Failed to extract data from {doc} document for "
                f"customer_id={req.customer_id}, application_id={req.application_id}. "
                f"OCR confidence score=0.31 (threshold=0.75). "
                f"Document may be blurred or tampered. Rejecting application.",
                extra={"customer_id": req.customer_id, "doc_type": doc}
            )
            raise HTTPException(status_code=422, detail=f"Document parse failure: {doc}")

        elif error == "kyc_verification_failed":
            ERROR_COUNT.labels(error_type="kyc_verification_failed").inc()
            logger.error(
                f"KYCVerificationException: Name mismatch in KYC for customer_id={req.customer_id}. "
                f"Aadhar name does not match PAN card name. "
                f"application_id={req.application_id}. Regulatory hold placed on application.",
                extra={"customer_id": req.customer_id, "application_id": req.application_id}
            )
            raise HTTPException(status_code=400, detail="KYC verification failed")

        # Happy path
        bureau_timeouts = max(0, bureau_timeouts - 1)
        BUREAU_TIMEOUT_RATE.set(bureau_timeouts / 10)
        emi = (req.loan_amount * 0.009) / (1 - (1.009 ** -req.tenure_months))
        LOAN_REQUESTS.labels(status="approved").inc()
        logger.info(
            f"Loan application approved: application_id={req.application_id}, "
            f"customer_id={req.customer_id}, loan_amount={req.loan_amount}, "
            f"emi={emi:.2f}, tenure={req.tenure_months} months"
        )
        return {"status": "approved", "application_id": req.application_id, "emi": round(emi, 2)}

    finally:
        LOAN_LATENCY.observe(time.time() - start)
        pending_count = max(0, pending_count - 1)
        PENDING_APPLICATIONS.set(pending_count)


def synthetic_load():
    import requests as req_lib
    time.sleep(7)
    loan_types = ["HOME_LOAN", "PERSONAL_LOAN", "VEHICLE_LOAN", "EDUCATION_LOAN"]
    app_id = 5000
    while True:
        try:
            req_lib.post("http://localhost:8003/loans/apply", json={
                "application_id": f"LOAN{app_id:08d}",
                "customer_id": f"CUST{random.randint(1000, 9999)}",
                "loan_amount": random.choice([500000, 1000000, 2500000, 5000000]),
                "tenure_months": random.choice([12, 24, 60, 120, 240]),
                "loan_type": random.choice(loan_types),
                "annual_income": random.uniform(300000, 2000000)
            }, timeout=5)
            app_id += 1
        except Exception:
            pass
        time.sleep(random.uniform(3, 8))


if __name__ == "__main__":
    threading.Thread(target=synthetic_load, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8003)