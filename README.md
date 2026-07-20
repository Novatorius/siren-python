# Siren SDK for Python

Affiliate, referral, and incentive tracking for any commerce stack — record sales, verify signed webhooks in one call, and reconcile Siren's ledger against your own.

[![PyPI version](https://img.shields.io/pypi/v/siren-sdk.svg)](https://pypi.org/project/siren-sdk/)
[![CI](https://github.com/Novatorius/siren-python/actions/workflows/ci.yml/badge.svg)](https://github.com/Novatorius/siren-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/siren-sdk.svg)](https://pypi.org/project/siren-sdk/)

## What is Siren?

[Siren](https://sirenaffiliates.com) is an affiliate, referral, and incentive
platform. You register commerce events — sales, refunds, referred visits — and
Siren attributes them to collaborators, computes rewards, and pays out.

This SDK is the official Python client for the Siren API. It lets you:

- Record sales, refunds, referred visits, and custom events (`events`)
- Verify signed webhook deliveries in a single, constant-time call (`webhooks`)
- Manage webhook subscriptions and API keys
- Reconcile Siren's ledger against your own with paginated readers

The full developer surface is described in the
[OpenAPI specification](./openapi.yaml).

## Install

```bash
pip install siren-sdk
```

The distribution is named **`siren-sdk`**, but the import name is **`siren`**:

```python
import siren
```

Requires Python 3.9+. The only dependency is [`httpx`](https://www.python-httpx.org/).

## Quickstart

### Record a sale

```python
import siren

client = siren.Siren(api_key="sk_live_...")  # Settings -> API Keys in the Siren dashboard

result = client.events.sale(
    source="stripe",                 # your commerce source
    external_id="cs_test_a1b2c3",    # your order id; used to match refunds later
    total=49.99,                     # major units: 49.99 means $49.99
    tracking_id=4021,                # opportunity id from the Siren tracking cookie
)

print(result.opportunity_id)  # read from the X-Siren-OID response header
```

With line items (reward pools compute `amount x quantity` per line; a missing
`quantity` defaults to `1`):

```python
client.events.sale(
    source="stripe",
    external_id="ord_77",
    total=109.97,
    tracking_id=4021,
    currency="USD",
    items=[
        {"external_id": "sku_1", "name": "Pro Plan (annual)", "amount": 49.99},
        {"name": "Add-on", "quantity": 3, "amount": 19.99},
    ],
)
```

Refunds and referred visits:

```python
client.events.refund(source="stripe", external_id="cs_test_a1b2c3")
client.events.site_visited(collaborator_id=88, user_id=12345)

# Custom event types registered on your org:
client.events.ingest("loyalty-points-earned", {"points": 120, "memberId": "m_9"})
```

### Verify a webhook

Siren signs every delivery with `X-Siren-Signature: sha256=<hex hmac>` — an
HMAC-SHA256 of the **raw request body** keyed by your subscription's signing
secret. `construct_event` verifies the signature (constant-time) and parses
the event in one call:

```python
import siren
from flask import Flask, request

app = Flask(__name__)
client = siren.Siren(api_key="sk_live_...")


@app.post("/webhooks/siren")
def handle_webhook():
    try:
        event = client.webhooks.construct_event(
            request.get_data(),                        # RAW body bytes — critical
            request.headers.get("X-Siren-Signature"),
            "whsec_...",                               # your signing secret
        )
    except siren.SignatureVerificationError:
        return "invalid signature", 400

    if event.type == siren.WebhookEventType.CONVERSION_APPROVED:
        handle_conversion(event.data)

    return "", 204
```

> **Pass the raw body bytes.** The HMAC is computed over the exact bytes Siren
> sent. If you parse the JSON and re-serialize it, key order and whitespace
> change and verification will fail. Use `request.get_data()` (Flask),
> `request.body` (Django), `await request.body()` (FastAPI/Starlette) — never
> `json.dumps(request.json)`.

To just check a signature without parsing:

```python
if client.webhooks.verify_signature(raw_body, signature_header, secret):
    ...
```

#### Manage subscriptions

```python
subscription = client.webhooks.subscriptions.create(
    target_url="https://example.com/webhooks/siren",
    events=[
        siren.WebhookEventType.CONVERSION_APPROVED,
        siren.WebhookEventType.PAYOUT_PAID,
    ],
    # or events=[siren.WebhookEventType.ALL] to subscribe to everything
)
print(subscription.signing_secret)  # returned ONCE — store it now

client.webhooks.subscriptions.list()
client.webhooks.subscriptions.delete(subscription.id)
```

## Features

- **Event ingestion** — `events.sale`, `events.refund`, `events.site_visited`,
  and `events.ingest` for custom event types.
- **One-call webhook verification** — `webhooks.construct_event` verifies the
  HMAC-SHA256 signature in constant time and parses the payload.
- **Subscription and API-key management** — create, list, delete subscriptions;
  create, list, revoke API keys. One-time secrets are never auto-retried.
- **Reconciliation readers** — paginated access to conversions, transactions,
  obligations, and payouts.
- **Typed, correct errors** — every failure subclasses `siren.SirenError` with
  `message`, `code`, and `status_code`; `ValidationError.field_errors` and
  `RateLimitError.retry_after` where relevant.
- **Automatic retries** — exponential backoff on network errors and 429/5xx for
  idempotent reads and event ingestion (never for secret-minting writes).
- **Fully typed** — ships `py.typed`; works with mypy and Pyright out of the box.
- **Typed taxonomy** — Siren's domain vocabulary as enums, so no magic strings
  cross the boundary: `WebhookEventType`, `EventSlug`, and the status
  vocabularies (`ConversionStatus`, `TransactionStatus`, `ObligationStatus`,
  `PayoutStatus`, `FulfillmentStatus`, `OpportunityStatus`, `ApiKeyStatus`,
  `WebhookSubscriptionStatus`).

### API keys

```python
key = client.api_keys.create(label="Production server")
print(key.raw_key)  # sk_live_... — returned ONCE, cannot be retrieved later

client.api_keys.list()      # no raw key material
client.api_keys.revoke(key.id)
```

### Reconciliation

Thin paginated readers over Siren's ledger:

```python
page = client.conversions.list(page=1, per_page=50)
for conversion in page:
    print(conversion["id"])
print(page.total)

client.transactions.list()
client.obligations.list()
# Extra filters pass through as query args; use the typed taxonomy for values.
client.payouts.list(status=siren.PayoutStatus.PAID)
```

### Errors

All errors subclass `siren.SirenError` and carry `message`, `code`, and
`status_code`:

| HTTP status | Exception |
|---|---|
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` |
| 403 | `PermissionError` |
| 404 | `NotFoundError` |
| 409 | `ConflictError` |
| 422 | `ValidationError` (`.field_errors`) |
| 429 | `RateLimitError` (`.retry_after`) |
| 5xx | `ApiError` |
| network/timeout | `ConnectionError` |

Webhook verification failures raise `SignatureVerificationError`.

```python
try:
    client.events.refund(source="stripe", external_id="unknown")
except siren.NotFoundError:
    print("no matching sale")
except siren.RateLimitError as err:
    print(f"retry in {err.retry_after}s")
```

### Configuration

```python
client = siren.Siren(
    api_key="sk_live_...",
    base_url="https://api.sirenaffiliates.com/siren/v1",  # default; point at staging/local to test
    timeout=30.0,       # seconds
    max_retries=2,      # exponential backoff on network errors and 429/5xx
)
```

## Other SDKs

Siren maintains official SDKs for other stacks:

- **Node.js** — [Novatorius/siren-node](https://github.com/Novatorius/siren-node)
- **PHP** — [Novatorius/siren-php](https://github.com/Novatorius/siren-php)

## Links

- Website: [sirenaffiliates.com](https://sirenaffiliates.com)
- API reference: [OpenAPI specification](./openapi.yaml)
- Issues: [github.com/Novatorius/siren-python/issues](https://github.com/Novatorius/siren-python/issues)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to
set up a development environment and run the test suite. By participating you
agree to the [Code of Conduct](./CODE_OF_CONDUCT.md).

## License

MIT — see [LICENSE](./LICENSE).
