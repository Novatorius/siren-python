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
- Read Siren's ledger and manage webhook subscriptions with an API key that holds the ledger and webhook scopes
- Manage API keys with a signed-in dashboard session
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
print(result.status)          # created, recorded, duplicate, unattributed, ignored, or queued
```

Siren answers each sale with what came of it, in `result.body`. Sending the
same `source` and `external_id` again records nothing new and answers
`duplicate`. While your organization's processing is paused, Siren queues the
event and answers `202`, and `result.queued` is `True`.

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
the event in one call.

The body is the event's payload alone, such as `{"conversionId": 991}`. The
event type arrives in the `X-Siren-Event` header and the event id in
`X-Siren-Delivery`, so pass both headers along. Without `event_type`,
`construct_event` raises `SignatureVerificationError`, because the body alone
does not name the event. The event id stays the same when Siren retries a
delivery, so use it to drop repeats.

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
            event_type=request.headers.get("X-Siren-Event"),
            delivery_id=request.headers.get("X-Siren-Delivery"),
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

The API key needs the `webhook-subscriptions:manage` scope. A key with
`webhook-subscriptions:read` can call `list()` only.

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
- **Subscription management** — create, list, and delete subscriptions with an
  API key that holds a webhook scope, or a dashboard session.
- **API-key management** — create, list, and revoke API keys with a signed-in
  dashboard session. One-time secrets are never auto-retried.
- **Reconciliation readers** — paginated access to conversions, transactions,
  obligations, and payouts, with an API key that holds the ledger scopes.
- **Typed, correct errors** — every failure subclasses `siren.SirenError` with
  `message`, `code`, and `status_code`; `BadRequestError.field_errors` and
  `RateLimitError.retry_after` where relevant.
- **Automatic retries** — exponential backoff on network errors and 429/5xx for
  idempotent reads and event ingestion (never for secret-minting writes).
- **Fully typed** — ships `py.typed`; works with mypy and Pyright out of the box.

### Credentials

An API key (`sk_live_...`) does what its scopes allow. `siren.ApiKeyScope`
names each one:

| Scope | What the key can do |
|---|---|
| `ApiKeyScope.ALL_EVENTS` (`event:*`), `event_scope(slug)` | Send events |
| `ApiKeyScope.LEDGER_CONVERSIONS_READ` (`ledger:conversions:read`) | `conversions.list()` |
| `ApiKeyScope.LEDGER_TRANSACTIONS_READ` (`ledger:transactions:read`) | `transactions.list()` |
| `ApiKeyScope.LEDGER_OBLIGATIONS_READ` (`ledger:obligations:read`) | `obligations.list()` |
| `ApiKeyScope.LEDGER_PAYOUTS_READ` (`ledger:payouts:read`) | `payouts.list()` |
| `ApiKeyScope.WEBHOOK_SUBSCRIPTIONS_READ` (`webhook-subscriptions:read`) | `webhooks.subscriptions.list()` |
| `ApiKeyScope.WEBHOOK_SUBSCRIPTIONS_MANAGE` (`webhook-subscriptions:manage`) | Every `webhooks.subscriptions` call, and a test delivery (`POST /webhooks/{id}/test`, which the SDK has no method for) |

Siren grants the ledger and webhook scopes only by their exact names. `*`,
`event:*`, wildcards such as `ledger:*`, and older names such as
`conversions:read` grant none of them, so a key made before these scopes
existed reads nothing until you replace it with one that holds them. A key's
requests count against the organization's API rate limit.

`api_keys` takes a signed-in dashboard session's token, passed as `api_key`.
No API key scope reaches it, and Siren answers a key there with
`ApiKeyNotAcceptedError`. The reconciliation readers also take a dashboard
session's token or a connected site's token.

A key created with a `property_id` is bound to that store. It reads only its
own store's conversions, transactions and obligations, and Siren refuses it
on payouts and webhook subscriptions with `StoreKeyNotAllowedError`. An
organization-wide key reads the whole organization, and narrows a ledger read
to one store with `property_id`:

```python
client.conversions.list(property_id=5, status="approved")
```

### API keys

```python
from siren import ApiKeyScope

key = client.api_keys.create(
    label="Accounting export",
    scopes=[ApiKeyScope.LEDGER_CONVERSIONS_READ, ApiKeyScope.LEDGER_PAYOUTS_READ],
)
print(key.raw_key)  # sk_live_... — returned ONCE, cannot be retrieved later

client.api_keys.list()      # no raw key material
client.api_keys.revoke(key.id)
```

### Reconciliation

Thin paginated readers over Siren's ledger. `page` and `per_page` become
Siren's `number` and `offset`, and a page holds at most 200 records:

```python
page = client.conversions.list(page=1, per_page=50)
for conversion in page:
    print(conversion["id"])
print(page.total, page.total_pages)

client.transactions.list()
client.obligations.list()
client.payouts.list(status="paid")  # extra filters pass through as query args
```

### Errors

All errors subclass `siren.SirenError` and carry `message`, `code`, and
`status_code`:

| HTTP status | Exception |
|---|---|
| 400 | `BadRequestError` (`.field_errors` when a field failed its check) |
| 401 | `AuthenticationError`, or its subclass `ApiKeyNotAcceptedError` (`code` `api_key_not_accepted`) when the route takes a session and not an API key |
| 403 | `PermissionError`, or its subclasses `MissingScopeError` (`code` `missing_scope`, with `.scope` and `.any_of`) and `StoreKeyNotAllowedError` (`code` `store_key_not_allowed`, with `.property_id`) |
| 404 | `NotFoundError` |
| 409 | `ConflictError` |
| 422 | `ValidationError` (`.field_errors`), from the few routes that still use 422 |
| 429 | `RateLimitError` (`.retry_after`) |
| 5xx | `ApiError` |
| network/timeout | `ConnectionError` |

Webhook verification failures raise `SignatureVerificationError`.
`siren.ErrorCode` holds the codes the SDK raises a dedicated error for.

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
