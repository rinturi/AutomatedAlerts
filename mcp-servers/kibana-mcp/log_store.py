"""
Synthetic log store for kibana-mcp.
Pre-seeded with realistic banking error log entries for all 4 services.
In production this is replaced by real Elasticsearch queries.
"""
from datetime import datetime, timezone, timedelta
import random

def get_synthetic_logs() -> list:
    """
    Returns 40 pre-seeded log entries covering all error scenarios
    across all 4 banking microservices.
    """
    now = datetime.now(timezone.utc)

    def ts(minutes_ago: float) -> str:
        return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    logs = [

        # ── payment-svc errors ─────────────────────────────────────────────
        {
            "@timestamp": ts(1.2),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.db",
            "message": "java.sql.SQLException: Connection pool exhausted - no available connections in pool "
                       "(pool_size=10, available=0, transaction_id=TXN00000042, account_id=ACC000001)",
            "exception_type": "SQLException",
            "pool_available": 0,
            "transaction_id": "TXN00000042",
            "account_id": "ACC000001",
        },
        {
            "@timestamp": ts(1.5),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.gateway",
            "message": "PaymentGatewayException: Upstream gateway timeout after 30000ms - "
                       "merchant_id=MERCHANT_HDFC, transaction_id=TXN00000039, amount=45000.0 INR. "
                       "Gateway host: pg.bankcore.internal:8443",
            "exception_type": "PaymentGatewayException",
            "transaction_id": "TXN00000039",
            "merchant_id": "MERCHANT_HDFC",
        },
        {
            "@timestamp": ts(2.1),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.gateway",
            "message": "PaymentGatewayException: Upstream gateway timeout after 30000ms - "
                       "merchant_id=MERCHANT_ICICI, transaction_id=TXN00000035, amount=12500.0 INR. "
                       "Gateway host: pg.bankcore.internal:8443",
            "exception_type": "PaymentGatewayException",
            "transaction_id": "TXN00000035",
            "merchant_id": "MERCHANT_ICICI",
        },
        {
            "@timestamp": ts(2.8),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.db",
            "message": "java.sql.SQLException: Connection pool exhausted - no available connections in pool "
                       "(pool_size=10, available=1, transaction_id=TXN00000031, account_id=ACC000007)",
            "exception_type": "SQLException",
            "pool_available": 1,
            "transaction_id": "TXN00000031",
        },
        {
            "@timestamp": ts(3.4),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.processor",
            "message": "DuplicateTransactionException: Transaction TXN00000028 already processed "
                       "at 2026-04-07T04:12:33+00:00. Rejecting duplicate for account_id=ACC000003, amount=8500.0",
            "exception_type": "DuplicateTransactionException",
            "transaction_id": "TXN00000028",
            "account_id": "ACC000003",
        },
        {
            "@timestamp": ts(4.0),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.gateway",
            "message": "PaymentGatewayException: Upstream gateway timeout after 30000ms - "
                       "merchant_id=MERCHANT_SBI, transaction_id=TXN00000025, amount=75000.0 INR. "
                       "Gateway host: pg.bankcore.internal:8443",
            "exception_type": "PaymentGatewayException",
            "transaction_id": "TXN00000025",
        },
        {
            "@timestamp": ts(4.5),
            "level": "INFO",
            "service": "payment-svc",
            "logger": "payment-svc.processor",
            "message": "Payment processed successfully: transaction_id=TXN00000024, "
                       "account_id=ACC000012, amount=3200.0 INR",
            "transaction_id": "TXN00000024",
        },
        {
            "@timestamp": ts(5.1),
            "level": "ERROR",
            "service": "payment-svc",
            "logger": "payment-svc.account",
            "message": "InsufficientFundsException: Account ACC000019 balance insufficient "
                       "for debit of 95000.0 INR. Available: 12340.55",
            "exception_type": "InsufficientFundsException",
            "account_id": "ACC000019",
        },

        # ── auth-svc errors ────────────────────────────────────────────────
        {
            "@timestamp": ts(0.8),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.session",
            "message": "RedisConnectionException: Failed to connect to session store at "
                       "redis.bankcore.internal:6379 after 3 retries. ECONNREFUSED. "
                       "user_id=USER00042, client_ip=10.0.2.15. Session persistence unavailable.",
            "exception_type": "RedisConnectionException",
            "user_id": "USER00042",
            "client_ip": "10.0.2.15",
        },
        {
            "@timestamp": ts(1.1),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.session",
            "message": "RedisConnectionException: Failed to connect to session store at "
                       "redis.bankcore.internal:6379 after 3 retries. ECONNREFUSED. "
                       "user_id=USER00018, client_ip=10.0.3.22. Session persistence unavailable.",
            "exception_type": "RedisConnectionException",
            "user_id": "USER00018",
        },
        {
            "@timestamp": ts(1.9),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.security",
            "message": "BruteForceException: Account USER00007 locked after 5 consecutive "
                       "failed login attempts from IP 10.0.5.100. Lock duration: 30 minutes. SecurityEvent raised.",
            "exception_type": "BruteForceException",
            "user_id": "USER00007",
            "client_ip": "10.0.5.100",
            "failed_attempts": 5,
        },
        {
            "@timestamp": ts(2.5),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.ldap",
            "message": "LDAPTimeoutException: Directory service timeout while validating user USER00033. "
                       "ldap.bankcore.internal:389 did not respond within 5000ms. "
                       "Falling back to local auth cache — cache miss for user.",
            "exception_type": "LDAPTimeoutException",
            "user_id": "USER00033",
        },
        {
            "@timestamp": ts(3.0),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.token",
            "message": "JWTSignatureException: Token signature verification failed for user USER00021. "
                       "Expected HMAC-SHA256 with key_id=bank-jwt-key-v3, got mismatched signature. "
                       "Possible token tampering or key rotation mismatch.",
            "exception_type": "JWTSignatureException",
            "user_id": "USER00021",
        },
        {
            "@timestamp": ts(3.8),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.session",
            "message": "RedisConnectionException: Failed to connect to session store at "
                       "redis.bankcore.internal:6379 after 3 retries. ECONNREFUSED. "
                       "user_id=USER00055, client_ip=10.0.1.8.",
            "exception_type": "RedisConnectionException",
            "user_id": "USER00055",
        },
        {
            "@timestamp": ts(4.3),
            "level": "INFO",
            "service": "auth-svc",
            "logger": "auth-svc.login",
            "message": "Login successful: user_id=USER00011, client_ip=10.0.0.5",
            "user_id": "USER00011",
        },
        {
            "@timestamp": ts(4.9),
            "level": "ERROR",
            "service": "auth-svc",
            "logger": "auth-svc.token",
            "message": "TokenRaceConditionException: Refresh token for user USER00029 "
                       "already invalidated by concurrent refresh. Race condition detected. Forcing re-authentication.",
            "exception_type": "TokenRaceConditionException",
            "user_id": "USER00029",
        },

        # ── loan-svc errors ────────────────────────────────────────────────
        {
            "@timestamp": ts(1.0),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.bureau",
            "message": "CreditBureauTimeoutException: CIBIL API call timed out after 15000ms for "
                       "customer_id=CUST4521, application_id=LOAN00005001. "
                       "Endpoint: https://cibil.bankcore.internal/v2/score. "
                       "bureau_timeout_count_last_5min=8. Cannot proceed without credit score.",
            "exception_type": "CreditBureauTimeoutException",
            "customer_id": "CUST4521",
            "application_id": "LOAN00005001",
        },
        {
            "@timestamp": ts(1.7),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.bureau",
            "message": "CreditBureauTimeoutException: CIBIL API call timed out after 15000ms for "
                       "customer_id=CUST3318, application_id=LOAN00005008. "
                       "Endpoint: https://cibil.bankcore.internal/v2/score. "
                       "bureau_timeout_count_last_5min=9.",
            "exception_type": "CreditBureauTimeoutException",
            "customer_id": "CUST3318",
            "application_id": "LOAN00005008",
        },
        {
            "@timestamp": ts(2.3),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.risk",
            "message": "RiskEngineException: Risk scoring engine at risk.bankcore.internal:9090 returned "
                       "HTTP 503 for application_id=LOAN00005012. "
                       "RiskEngine pod count=0 (all replicas crashed). loan_amount=2500000.0, loan_type=HOME_LOAN.",
            "exception_type": "RiskEngineException",
            "application_id": "LOAN00005012",
            "loan_amount": 2500000.0,
        },
        {
            "@timestamp": ts(3.1),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.calculator",
            "message": "ArithmeticException: EMI calculation overflow for application_id=LOAN00005019. "
                       "loan_amount=5000000.0, tenure=0 months, computed_emi exceeded long integer boundary. "
                       "Possible cause: invalid tenure_months=0 passed in request. "
                       "StackTrace: LoanCalculator.computeEMI(LoanCalculator.java:142)",
            "exception_type": "ArithmeticException",
            "application_id": "LOAN00005019",
            "tenure_months": 0,
        },
        {
            "@timestamp": ts(3.7),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.kyc",
            "message": "KYCVerificationException: Name mismatch in KYC for customer_id=CUST7741. "
                       "Aadhar name does not match PAN card name. "
                       "application_id=LOAN00005023. Regulatory hold placed on application.",
            "exception_type": "KYCVerificationException",
            "customer_id": "CUST7741",
            "application_id": "LOAN00005023",
        },
        {
            "@timestamp": ts(4.2),
            "level": "ERROR",
            "service": "loan-svc",
            "logger": "loan-svc.documents",
            "message": "DocumentParseException: Failed to extract data from INCOME_PROOF document for "
                       "customer_id=CUST2219, application_id=LOAN00005027. "
                       "OCR confidence score=0.31 (threshold=0.75). Document may be blurred or tampered.",
            "exception_type": "DocumentParseException",
            "customer_id": "CUST2219",
            "doc_type": "INCOME_PROOF",
        },
        {
            "@timestamp": ts(4.8),
            "level": "INFO",
            "service": "loan-svc",
            "logger": "loan-svc.processor",
            "message": "Loan application approved: application_id=LOAN00005031, "
                       "customer_id=CUST5512, loan_amount=1000000.0, emi=9856.21, tenure=120 months",
            "application_id": "LOAN00005031",
        },

        # ── notification-svc errors ────────────────────────────────────────
        {
            "@timestamp": ts(0.9),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.sms",
            "message": "SMSGatewayException: SMS gateway at sms.vodafone-biz.internal:443 is unreachable. "
                       "HTTPConnectionPool(host='sms.vodafone-biz.internal', port=443): Max retries exceeded. "
                       "notification_id=NOTIF00009001, customer_id=CUST1234. Notification queued for retry.",
            "exception_type": "SMSGatewayException",
            "notification_id": "NOTIF00009001",
            "customer_id": "CUST1234",
            "channel": "SMS",
        },
        {
            "@timestamp": ts(1.4),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.sms",
            "message": "SMSGatewayException: SMS gateway at sms.vodafone-biz.internal:443 is unreachable. "
                       "HTTPConnectionPool: Max retries exceeded. "
                       "notification_id=NOTIF00009007, customer_id=CUST5678. Notification queued for retry.",
            "exception_type": "SMSGatewayException",
            "notification_id": "NOTIF00009007",
            "customer_id": "CUST5678",
            "channel": "SMS",
        },
        {
            "@timestamp": ts(2.0),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.email",
            "message": "SMTPConnectionException: Connection refused by SMTP relay smtp.bankcore.internal:587 "
                       "for notification_id=NOTIF00009014. recipient=user@example.com. "
                       "TLS handshake failed: certificate expired 2024-12-31. "
                       "Email delivery failed for customer_id=CUST9012.",
            "exception_type": "SMTPConnectionException",
            "notification_id": "NOTIF00009014",
            "customer_id": "CUST9012",
            "channel": "EMAIL",
        },
        {
            "@timestamp": ts(2.6),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.queue",
            "message": "QueueOverflowException: Notification queue depth 523 exceeded max capacity 500. "
                       "Dropping notification_id=NOTIF00009021 for customer_id=CUST3344. "
                       "channel=PUSH, template=TXN_ALERT. "
                       "Kafka topic bank.notifications.outbound partition lag: 12000 messages.",
            "exception_type": "QueueOverflowException",
            "notification_id": "NOTIF00009021",
            "queue_depth": 523,
            "channel": "PUSH",
        },
        {
            "@timestamp": ts(3.2),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.template",
            "message": "TemplateRenderException: Jinja2 template 'TXN_ALERT' failed to render. "
                       "KeyError: 'account_balance' missing in payload for notification_id=NOTIF00009028. "
                       "customer_id=CUST6677, channel=EMAIL.",
            "exception_type": "TemplateRenderException",
            "notification_id": "NOTIF00009028",
            "template_id": "TXN_ALERT",
            "channel": "EMAIL",
        },
        {
            "@timestamp": ts(3.9),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.push",
            "message": "FCMTokenException: Firebase push token for customer_id=CUST8899 is invalid or expired. "
                       "FCM error_code=UNREGISTERED. notification_id=NOTIF00009035. "
                       "Device token deregistered — removing from customer profile.",
            "exception_type": "FCMTokenException",
            "notification_id": "NOTIF00009035",
            "customer_id": "CUST8899",
            "channel": "PUSH",
        },
        {
            "@timestamp": ts(4.6),
            "level": "ERROR",
            "service": "notification-svc",
            "logger": "notification-svc.sms",
            "message": "SMSGatewayException: SMS gateway at sms.vodafone-biz.internal:443 is unreachable. "
                       "HTTPConnectionPool: Max retries exceeded. "
                       "notification_id=NOTIF00009041, customer_id=CUST2211.",
            "exception_type": "SMSGatewayException",
            "notification_id": "NOTIF00009041",
            "customer_id": "CUST2211",
            "channel": "SMS",
        },
        {
            "@timestamp": ts(5.0),
            "level": "INFO",
            "service": "notification-svc",
            "logger": "notification-svc.sms",
            "message": "Notification delivered: notification_id=NOTIF00009044, "
                       "customer_id=CUST4455, channel=SMS, template=LOGIN_OTP",
            "notification_id": "NOTIF00009044",
        },
    ]

    return sorted(logs, key=lambda x: x["@timestamp"], reverse=True)
