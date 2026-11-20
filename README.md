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
```

### Send an app's custom event

A custom event belongs to one app, and Siren takes only that app's
short-lived token at its address. Build the client with the app's key and
send. The SDK trades the key for a token the first time, keeps it, gets a new
one when fewer than two minutes of its 20 are left, and on a 401 gets a new
one and sends once more. Threads that need a token at once share one request.
You write no token code.

<!-- readme-test: server-send -->
```python
import os

import siren

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],  # the app's key
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

result = client.events.ingest("signup-completed", {"collaboratorId": 12, "plan": "pro"})
print(result.status)
```

`sale`, `refund`, `site_visited` and every other call keep sending the key. A
key that belongs to no app sends custom events with the key.

### Send a custom event from a browser

A page never holds the app's key. It asks the app's own server for a browser
token, which lasts ten minutes, works for one event only, and works only from
a domain the event allows. Add the site's domain to the event in Siren first,
because an event with no domains refuses every browser.

Make the handler once, beside the client:

<!-- readme-test: handler -->
```python
import os

import siren

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])
```

Then mount it in one line. It needs no framework, and `tokens.wsgi` and
`tokens.asgi` answer at whatever path you mount them on.

FastAPI or Starlette:

```python
app.add_route("/siren/token", tokens.asgi, methods=["GET", "POST"])
```

Flask, through Werkzeug, which Flask ships with:

```python
from werkzeug.middleware.dispatcher import DispatcherMiddleware

app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/siren/token": tokens.wsgi})
```

Django, as a view, with `path("siren/token", siren_token)` in `urls.py`.
Calling the handler with the body and query gives the status and JSON, which
is also the way into any other framework:

```python
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def siren_token(request):
    status, body = tokens(request.body, request.GET)
    return JsonResponse(body, status=status)
```

Each reads the event from a JSON body `{"event": "..."}` or the query
`?event=`, and answers:

| Status | Body | When |
|---|---|---|
| 200 | `{"token", "expiresIn", "url"}` | Siren gave a browser token. |
| 400 | `{"error"}` | The request names no event, names one outside `events`, or Siren refuses the event (the app has no such event, or it allows no domain). |
| 502 | `{"error"}` | Siren can't be reached, or refuses the app's key. |

The handler reuses one token per event until fewer than 60 seconds are left,
so a busy page costs one grant every nine minutes or so.
`client.events.browser_token("signup-completed")` gives the `BrowserToken`
itself.

The page asks for the token, then posts the event's data to `url` with the
token in the query. Siren needs `collaboratorId`, the collaborator who gets
credit, at the top of the body:

<!-- readme-test: page -->
```html
<script>
async function sirenTrack(event, data) {
  const asked = await fetch('/siren/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event }),
  });
  if (!asked.ok) return null;
  const { token, url } = await asked.json();
  const sent = await fetch(url + '?access_token=' + encodeURIComponent(token), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return sent.ok ? sent.json() : null;
}

sirenTrack('signup-completed', { collaboratorId: 12, plan: 'pro' });
</script>
```

A page built with JavaScript tools can use `track()` from
`@novatorius/siren/storefront`, which keeps the token and tries once more
after a 401.

This keeps other websites from firing the event from their visitors'
browsers, and a leaked token dies within minutes. It does not stop a script
outside a browser, which can send any `Origin`, and anyone who fetches a
token from your page can send that event for ten minutes. A browser send
carries whatever the page's script puts in it, so don't let a page name who
gets credit unless you check it on your server.

The [examples](examples) folder holds each of these as a file.

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

### Answer Siren in a few lines

An app's feature, such as a distribution resolver or an action after a
conversion, gets requests from Siren at the app's address. Make a request
handler with the app's signing secret, register a function for each feature
by its kind and key, and mount the handler. Make the client with the app's
own key, since the handler posts an ask's answer with it.

```python
import os
import siren
from flask import Flask

client = siren.Siren(api_key=os.environ["SIREN_API_KEY"], base_url="https://cloud.example.com/siren/v1")
answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])

@answers.on("postConversionAction", "tell-the-crm")
def tell_the_crm(request):
    # Siren may send it twice, so add it once, keyed by request.id.
    add_to_crm(request.id, request.data["conversion"], request.data["reward"])

app = Flask(__name__)
app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])
```

Each other feature changes only the function. An action after an
allocation:

```python
@answers.on("postAllocationAction", "note-the-share")
def note_the_share(request):
    # Siren may send it twice, so note it once, keyed by request.id.
    note_share(request.id, request.data["allocation"])
```

A distribution resolver, an ask, returns its answer:

```python
@answers.on("distributionResolver", "split-by-score")
def split_by_score(request):
    pool, metrics = request.data["pool"], request.data["metrics"]
    # Whole currency steps, never more than the pool, split evenly when every score is 0.
    shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in metrics})
    return {"outcome": "allocated", "units": pool["units"], "shares": shares}
```

An incentive resolver does the same with the conversions:

```python
@answers.on("incentiveResolver", "split-the-reward")
def split_the_reward(request):
    pool, conversions = request.data["pool"], request.data["conversions"]
    rewards = client.apps.split_pool(pool, {c["conversionId"]: c["score"] for c in conversions})
    return {"outcome": "rewards", "units": pool["units"], "rewards": rewards}
```

The handler checks each request the way `apps.verify_request()` does, then
replies:

| Request | Reply |
|---|---|
| Not a POST | 405 |
| A body over 1 MiB, more than Siren sends | 413, read no further |
| A body a parser already read, so the signed bytes are gone | 500, and `on_error` gets a `SirenError` that says to mount the handler before the JSON parser |
| A signature that is missing or wrong, or a time outside `tolerance_seconds` | 401 |
| A signed body that is not JSON, or not an app request of version 1 | 400 |
| A tell with a function | 200 once the function returns, 500 when it raises, so Siren sends it again |
| An ask with a function | 200 at once. The function runs after the reply, and the handler posts what it returns |
| A kind and key with no function | 404 `No function handles <kind> <key>.`, so Siren shows the request under Needs attention once its sends run out |
| Another `X-Siren-Event`, such as `conversion.approved` | 200 once the `on_event` function returns, 500 when it raises, and 200 when there is none |

Every reply is JSON with `Cache-Control: no-store`.

An ask's answer is checked the way `apps.requests.answer()` checks one, then
posted to `POST /apps/requests/{id}/answer`. The post is sent again after a
lost connection, a 429 or a 5xx, up to the client's `max_retries`, since the
same answer goes each time and Siren takes it again as a duplicate. A
refusal, such as 422 `app_answer_invalid` or 409
`app_request_already_answered`, goes to `on_error` and is not sent again. A
function that returns `None` posts nothing, and the app answers later with
`apps.requests.answer()`.

Register functions three ways:

```python
answers.on("distributionResolver", "split-by-score", split)  # one feature, and calls chain
answers.on("distributionResolver", split)                    # every feature of the kind
answers.on_event("conversion.approved", noted)               # a webhook at the same address
```

Each also works as a decorator, as above. A function for the key wins over
one for the kind. An `async def` function is awaited.

Mount the handler on any of these:

```python
app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])  # Flask
path("siren", answers.django)                                           # Django, already csrf_exempt
app.add_route("/siren", answers.asgi, methods=["POST"])                 # FastAPI, Starlette
answers.wsgi                                                            # any WSGI server
reply = answers(raw_body, headers, method="POST")                       # anything else
```

The plain call returns an `AppRequestReply` with `status`, `body`,
`headers`, `content` and `after`. For an ask, send the reply, then call
`reply.after()`. Flask and WSGI run an ask's work once the server has sent
the reply, ASGI runs it after the reply in the same call, and Django starts
it on a thread. On a host that stops work once it has replied, pass
`run_later`, which is handed the work as a callable with no arguments, such
as a queue's add. `on_error(error, request)` gets every failure the reply
can't show, and logs to the `siren` logger by default.

`apps.split_pool(pool, weights)` splits the pool a resolver is offered.
`weights` maps each id to a weight, or is a list of `(id, weight)` pairs. It
returns `{str(id): amount}`, each a whole step of the currency (a cent for
dollars, a yen for yen), rounded down so the sum never passes
`pool["value"]`, and splits evenly when every weight is 0. It raises
`ValueError` for a negative or non-finite weight. The question and answer
of each kind have types, such as `siren.AppDistributionQuestion` and
`siren.AppRewardAnswer`.

#### Check and answer by hand

An app that queues the work, or answers after a person decides, checks a
request with `apps.verify_request()` and answers an ask with
`apps.requests.answer()`. The handler calls both inside.

```python
import siren

client = siren.Siren(api_key=APP_API_KEY)


def siren_request(raw_body: bytes, headers) -> None:
    request = client.apps.verify_request(raw_body, headers, APP_SIGNING_SECRET)
    if not request.is_ask:
        return
    receipt = client.apps.requests.answer(request.id, {"outcome": "paid", "reference": "TX-1"})
    if receipt.duplicate:
        print("Siren already had this answer.")
```

`verify_request()` reads nothing over the network. It checks, in this order,
that `X-Siren-Event` is `app.request` when present, that
`X-Siren-Signature-V2` (`t=<seconds>,v1=<hex>`) signs `"{t}."` and the raw
body, that `X-Siren-Timestamp` matches `t`, that `t` is within
`tolerance_seconds` of the clock (300 by default, at most 3600), and that the
body is an app request of version 1. Each failure raises
`SignatureVerificationError` with the reason. Pass the body exactly as
received. Siren may send one request more than once, so drop a repeat by
`request.id`.

`answer()` sends `POST /apps/requests/{id}/answer` once and never retries
it. It raises `ValueError` before sending for an id that is not a request
key, an answer that is not a mapping, one with no text `outcome`, and one over
16384 bytes as JSON. A different second answer raises `ConflictError` with
code `app_request_already_answered`, and the first answer stands. When Siren
takes no answer from the key, `AuthenticationError` carries
`ErrorCode.APP_ANSWER_UNAUTHORIZED`.

`AppItemKind` names each kind as `request.item["kind"]` carries it. The SDK
checks only what every answer needs, so Siren checks the rest of each kind's
answer and refuses one that doesn't fit with 422. A metric calculation
(`AppItemKind.METRIC_CALCULATION`) scores a metric for a distributor and
answers a score for each collaborator. Siren takes at most 50 results, each
collaborator once and each in the organization and not in the trash, and each
score a whole number of 0 or more. An empty list scores nobody. A metric
filter (`AppItemKind.METRIC_FILTER`) answers whether one collaborator's metric
counts: allow it, or suppress it with a plain text reason of 1 to 200
characters.

```python
from siren import AppItemKind

if request.is_ask and request.item["kind"] == AppItemKind.METRIC_CALCULATION:
    client.apps.requests.answer(request.id, {
        "outcome": "scores",
        "results": [{"collaboratorId": 7, "score": 10}],
    })

if request.is_ask and request.item["kind"] == AppItemKind.METRIC_FILTER:
    collaborator_id = request.data["collaborator"]["id"]
    if owns_customer(collaborator_id):
        answer = {"outcome": "suppress", "reason": "Customer already owned by a partner"}
    else:
        answer = {"outcome": "allow"}
    client.apps.requests.answer(request.id, answer)
```

## Features

- **Event ingestion** — `events.sale`, `events.refund`, `events.site_visited`,
  and `events.ingest` for custom event types.
- **App tokens** — `events.ingest` trades the app's key for a short-lived
  token and keeps it fresh, and `events.browser_token_handler()` gives a page
  a browser token for one event, as a plain call, a WSGI app or an ASGI app.
- **App request handler** — `apps.request_handler()` checks what Siren sends
  an app, runs the function for each feature, replies, and posts an ask's
  answer, as a Flask or Django view, an ASGI or WSGI app, or a plain call.
  `apps.split_pool()` splits a resolver's pool in whole currency steps.
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
| `ApiKeyScope.ALL_EVENTS` (`event:*`), `event_scope(slug)` | Send built-in events. A custom event takes only its app's token, which the SDK gets from the app's key. |
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
