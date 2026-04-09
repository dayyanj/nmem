"""
Built-in demo dataset — "Acme Corp Support Team".

3 agents (support, engineering, sales) with interleaved memories
showing cross-tier promotion and multi-agent patterns.
"""

JOURNAL_ENTRIES = [
    {
        "agent_id": "support",
        "entry_type": "session_summary",
        "title": "Customer reported checkout timeout",
        "content": (
            "Customer acme-42 experienced 30s timeout during checkout. "
            "Root cause: payment gateway latency spike. Workaround: retry. "
            "Escalated to engineering team for permanent fix."
        ),
        "importance": 7,
    },
    {
        "agent_id": "engineering",
        "entry_type": "investigation",
        "title": "Payment gateway latency traced to DNS resolution",
        "content": (
            "The checkout timeouts reported by support were caused by DNS "
            "resolution delays to the payment gateway. The TTL on the DNS "
            "record had expired and the resolver was hitting upstream. "
            "Switched to direct IP with local TTL cache as interim fix."
        ),
        "importance": 8,
    },
    {
        "agent_id": "support",
        "entry_type": "lesson_learned",
        "title": "Always check provider status page before escalating",
        "content": (
            "The payment provider had posted a status update 10 minutes "
            "before the first ticket arrived. Checking the status page "
            "first would have saved an unnecessary engineering escalation."
        ),
        "importance": 6,
    },
    {
        "agent_id": "sales",
        "entry_type": "observation",
        "title": "Enterprise prospect Globex asks about uptime SLA",
        "content": (
            "Prospect Globex Corp (500 seats, $150K ARR potential) asked "
            "for 99.95% uptime SLA during demo. Current standard SLA is "
            "99.9%. Need to discuss with engineering about feasibility of "
            "higher-tier SLA offering."
        ),
        "importance": 7,
    },
    {
        "agent_id": "engineering",
        "entry_type": "decision",
        "title": "Adopted circuit breaker pattern for payment calls",
        "content": (
            "Implemented Hystrix-style circuit breaker for all payment "
            "gateway calls. Opens after 3 consecutive failures, enters "
            "half-open state with retry after 30 seconds. Timeout set to "
            "10s with exponential backoff. Deployed to production."
        ),
        "importance": 9,
    },
    {
        "agent_id": "support",
        "entry_type": "observation",
        "title": "Three customers hit the same onboarding bug",
        "content": (
            "Three separate customers this week failed to complete "
            "onboarding because the email verification link expired "
            "after 15 minutes. The default session timeout is too "
            "aggressive for enterprise customers with email relay delays."
        ),
        "importance": 7,
    },
]

LTM_ENTRIES = [
    {
        "agent_id": "support",
        "category": "procedure",
        "key": "checkout_troubleshooting",
        "content": (
            "Step 1: Check payment provider status page (status.paygate.example.com). "
            "Step 2: Verify customer's payment method is valid. "
            "Step 3: Check server logs for timeout errors (grep 'payment_timeout'). "
            "Step 4: If provider is healthy, escalate to engineering with log snippet."
        ),
        "importance": 8,
    },
    {
        "agent_id": "engineering",
        "category": "architecture",
        "key": "payment_resilience_patterns",
        "content": (
            "Payment calls use circuit breaker (3 failures to open, 30s half-open). "
            "DNS resolved via direct IP cache (bypasses resolver latency). "
            "Timeout: 10s with 2 retries and exponential backoff. "
            "All payment errors logged to #payment-alerts Slack channel."
        ),
        "importance": 9,
    },
    {
        "agent_id": "sales",
        "category": "pricing",
        "key": "enterprise_sla_tiers",
        "content": (
            "Standard plan: 99.9% uptime SLA, $49/mo. "
            "Business plan: 99.95% uptime, $149/mo, priority support. "
            "Enterprise: 99.99% uptime (requires dedicated infrastructure), custom pricing. "
            "SLA credits: 10% per 0.1% below target, capped at one month."
        ),
        "importance": 7,
    },
]

SHARED_ENTRIES = [
    {
        "key": "payment_provider_contact",
        "category": "vendor",
        "content": (
            "Payment gateway (PayGate): support@paygate.example.com. "
            "Escalation line: +1-555-0199 (mention account #PG-42). "
            "SLA: 4-hour response for P1 incidents. "
            "Status page: status.paygate.example.com"
        ),
        "importance": 7,
    },
    {
        "key": "platform_uptime_target",
        "category": "policy",
        "content": (
            "Platform uptime target: 99.95% measured monthly. "
            "Excludes scheduled maintenance (max 4h/month, announced 72h ahead). "
            "Monitoring: Datadog synthetic checks every 60s from 5 regions. "
            "Incident response: P1 pages on-call within 5 minutes."
        ),
        "importance": 9,
    },
    {
        "key": "escalation_matrix",
        "category": "procedure",
        "content": (
            "P1 (service down): Page on-call engineer immediately via PagerDuty. "
            "P2 (degraded performance): Post in #incidents Slack, 15-minute response. "
            "P3 (minor issue): Create Jira ticket, address in next sprint. "
            "All incidents get a post-mortem within 48 hours."
        ),
        "importance": 8,
    },
]

DEMO_SEARCHES = [
    ("support", "checkout timeout payment gateway"),
    ("engineering", "circuit breaker resilience pattern"),
    ("sales", "enterprise uptime SLA pricing"),
]
