"""
auth-svc: Mock banking authentication microservice.
Simulates: JWT validation failures, Redis session store down,
           brute-force lockouts, token refresh race conditions.
"""
import os
import random
import sys
import time
import threading
from datetime import datetime, timezone

sys.path.insert(0, "/app/shared")
from log_utils import get_logger

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import uvicorn

SERVICE = "auth-svc"
logger = get_logger(SERVICE)
app = FastAPI(title="Auth Service", version="1.0.0")

# ---------- Prometheus metrics ----------
AUTH_ATTEMPTS = Counter("auth_attempts_total", "Auth attempts", ["result"])
AUTH_LATENCY = Histogram("auth_duration_seconds", "Auth latency")
ERROR_COUNT = Counter("auth_errors_total", "Auth errors", ["error_type"])
REDIS_UP = Gauge("auth_redis_connection_up", "Redis session store up (1=up, 0=down)")
LOCKED_ACCOUNTS = Gauge("auth_locked_accounts_total", "Currently locked accounts")

# ---------- Simulated state ----------
failed_attempts: dict[str, int] = {}
locked_accounts: set[str] = set()
redis_available = True

ERROR_SCENARIOS = [
    ("redis_down", 0.10),
    ("jwt_invalid_signature", 0.12),
    ("brute_force_lockout", 0.08),
    ("token_refresh_race", 0.07),
    ("ldap_timeout", 0.08),
    ("none", 0.55),
]


class LoginRequest(BaseModel):
    user_id: str
    password: str
    client_ip: str = "10.0.0.1"


class TokenRefreshRequest(BaseModel):
    refresh_token: str
    user_id: str


def pick_error():
    scenarios, weights = zip(*ERROR_SCENARIOS)
    return random.choices(scenarios, weights=weights, k=1)[0]


@app.get("/health")
def health():
    return {"status": "up", "service": SERVICE, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/auth/login")
def login(req: LoginRequest):
    global redis_available
    start = time.time()
    error = pick_error()

    try:
        if error == "redis_down":
            redis_available = False
            REDIS_UP.set(0)
            ERROR_COUNT.labels(error_type="redis_down").inc()
            logger.error(
                f"RedisConnectionException: Failed to connect to session store at redis.bankcore.internal:6379 "
                f"after 3 retries. ECONNREFUSED. user_id={req.user_id}, client_ip={req.client_ip}. "
                f"Session persistence unavailable — cannot complete login.",
                extra={"user_id": req.user_id, "client_ip": req.client_ip}
            )
            raise HTTPException(status_code=503, detail="Session store unavailable")

        elif error == "brute_force_lockout":
            failed_attempts[req.user_id] = failed_attempts.get(req.user_id, 0) + 5
            if failed_attempts[req.user_id] >= 5:
                locked_accounts.add(req.user_id)
                LOCKED_ACCOUNTS.set(len(locked_accounts))
                ERROR_COUNT.labels(error_type="brute_force_lockout").inc()
                logger.error(
                    f"BruteForceException: Account {req.user_id} locked after {failed_attempts[req.user_id]} "
                    f"consecutive failed login attempts from IP {req.client_ip}. "
                    f"Lock duration: 30 minutes. SecurityEvent raised.",
                    extra={"user_id": req.user_id, "client_ip": req.client_ip, "failed_attempts": failed_attempts[req.user_id]}
                )
                AUTH_ATTEMPTS.labels(result="locked").inc()
                raise HTTPException(status_code=423, detail="Account locked")

        elif error == "ldap_timeout":
            ERROR_COUNT.labels(error_type="ldap_timeout").inc()
            logger.error(
                f"LDAPTimeoutException: Directory service timeout while validating user {req.user_id}. "
                f"ldap.bankcore.internal:389 did not respond within 5000ms. "
                f"Falling back to local auth cache — cache miss for user.",
                extra={"user_id": req.user_id}
            )
            raise HTTPException(status_code=503, detail="Directory service timeout")

        # Happy path
        redis_available = True
        REDIS_UP.set(1)
        failed_attempts.pop(req.user_id, None)
        locked_accounts.discard(req.user_id)
        LOCKED_ACCOUNTS.set(len(locked_accounts))
        AUTH_ATTEMPTS.labels(result="success").inc()
        token = f"eyJ.mock.{req.user_id}.{int(time.time())}"
        logger.info(f"Login successful: user_id={req.user_id}, client_ip={req.client_ip}")
        return {"status": "success", "token": token, "expires_in": 3600}

    finally:
        AUTH_LATENCY.observe(time.time() - start)


@app.post("/auth/refresh")
def refresh_token(req: TokenRefreshRequest):
    error = pick_error()

    if error == "jwt_invalid_signature":
        ERROR_COUNT.labels(error_type="jwt_invalid_signature").inc()
        logger.error(
            f"JWTSignatureException: Token signature verification failed for user {req.user_id}. "
            f"Expected HMAC-SHA256 with key_id=bank-jwt-key-v3, got mismatched signature. "
            f"Possible token tampering or key rotation mismatch. Token prefix: {req.refresh_token[:20]}...",
            extra={"user_id": req.user_id}
        )
        raise HTTPException(status_code=401, detail="Invalid token signature")

    elif error == "token_refresh_race":
        ERROR_COUNT.labels(error_type="token_refresh_race").inc()
        logger.error(
            f"TokenRaceConditionException: Refresh token {req.refresh_token[:16]}... for user {req.user_id} "
            f"already invalidated by concurrent refresh. Race condition detected in token rotation. "
            f"Forcing re-authentication.",
            extra={"user_id": req.user_id}
        )
        raise HTTPException(status_code=409, detail="Token race condition")

    logger.info(f"Token refreshed: user_id={req.user_id}")
    return {"status": "success", "token": f"eyJ.mock.refreshed.{req.user_id}.{int(time.time())}"}


def synthetic_load():
    import requests as req_lib
    time.sleep(6)
    users = [f"USER{i:05d}" for i in range(1, 30)]
    while True:
        try:
            uid = random.choice(users)
            req_lib.post("http://localhost:8002/auth/login",
                        json={"user_id": uid, "password": "mock_pass", "client_ip": f"10.0.{random.randint(0,5)}.{random.randint(1,254)}"},
                        timeout=5)
        except Exception:
            pass
        time.sleep(random.uniform(1, 4))


if __name__ == "__main__":
    threading.Thread(target=synthetic_load, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8002)