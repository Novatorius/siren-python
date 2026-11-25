# Siren SDK for Python

Answer Siren Cloud from your own app with one request handler, and record sales, verify signed webhooks and reconcile Siren's ledger against your own.

[![PyPI version](https://img.shields.io/pypi/v/siren-sdk.svg)](https://pypi.org/project/siren-sdk/)
[![CI](https://github.com/Novatorius/siren-python/actions/workflows/ci.yml/badge.svg)](https://github.com/Novatorius/siren-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/siren-sdk.svg)](https://pypi.org/project/siren-sdk/)

## How Siren talks to your app

- **An app** is your own code that works with Siren. It holds a key, which it calls Siren with, and an address, where Siren sends it signed requests.
- **The signing secret** belongs to the address. Siren signs every request with it, and the handler checks each one.
- **A feature** is what the app adds to one of Siren's pickers. Its kind says what it does, such as `distributionResolver`, and its key names it in your app.
- **A tell** lets the app know something happened, such as an approved conversion. The handler runs your function and replies 200.
- **An ask** needs an answer, and the work it's about waits for it. The handler replies 200 at once, runs your function and posts what it returns.
- **A custom event** goes the other way: the app sends it to Siren.

## What you can do

- Answer Siren Cloud from your own app with one request handler
  (`apps.request_handler()`), mounted on Flask, Django, FastAPI, Starlette,
  any WSGI server or a plain call. Register a function for each feature and
  the handler checks the signature, replies and posts the answer.
- Try the handler without Siren: `siren.testing` signs a request for any
  kind with Siren's own sample question, and `simulate()` runs it and hands
  back the answer.
- Build every kind of app feature, each with typed questions and answers:
  post-conversion and post-allocation actions, distribution resolvers,
  attribution models, incentive structures, metric calculations, customer
  filters for distributors and for programs, transaction filters, engagement
  calculations, program group structures, collaborator group structures and
  payout methods.
- Split a resolver's pool in whole currency steps (`apps.split_pool()`).
- Send an app's custom events with short-lived tokens the SDK gets for you,
  from your server or from a browser page (`events.ingest()`,
  `events.browser_token_handler()`).
- Check a request and answer it by hand when a person or a queue decides
  (`apps.verify_request()`, `apps.requests.answer()`).
- Record sales, refunds and referred visits (`events`).
- Verify signed webhook deliveries in one constant-time call (`webhooks`).
- Read Siren's ledger with an API key that holds the ledger scopes, and read
  an app's webhook address and its deliveries with a signed-in dashboard
  session.
- Add, read, change and remove an app's features with a signed-in dashboard
  session (`apps.features`).
- Reconcile Siren's ledger against your own with paginated readers.

The full developer surface is described in the
[OpenAPI specification](./openapi.yaml).

## Install

```bash
pip install siren-sdk
```

The distribution is named **`siren-sdk`**, and the import name is **`siren`**.
It needs Python 3.9 or later, and its only dependency is
[`httpx`](https://www.python-httpx.org/).

**The apps code is unreleased.** The request handler, the feature kinds and
`siren.testing` live on the `feat/apps` branch until its pull request merges
and a release ships. The published `siren-sdk` 0.2.0 doesn't have them. Until
then, install the branch:

```bash
pip install "git+https://github.com/Novatorius/siren-python@feat/apps"
```

Or install a local checkout of it:

```bash
pip install /path/to/siren-python
```

## Answer Siren in a few lines

A Siren app is a service you host that acts as part of Siren Cloud's engine.
Each of its features joins one of Siren's pickers, and Siren sends the app a
signed request at its address whenever an organization's records use the
feature. Make a request handler, register a function for each feature by its
kind and key, and mount the handler at the address.

<!-- readme-test: handler-flask -->
```python
import siren
from flask import Flask

client = siren.Siren()                   # reads SIREN_API_KEY, the app's own key
answers = client.apps.request_handler()  # reads SIREN_SIGNING_SECRET

@answers.on("postConversionAction", "tell-the-crm")
def tell_the_crm(request):
    if request.test:  # a test request, as siren trigger ask sends: write nothing
        return
    # Siren may send it twice, so add it once, keyed by request.id.
    add_to_crm(request.id, request.data["conversion"], request.data["reward"])

app = Flask(__name__)
app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])
```

That is a whole app for Flask. Set `SIREN_API_KEY` to the app's key and
`SIREN_SIGNING_SECRET` to its signing secret, the two secrets Siren showed
once when it made the app, or pass them as `siren.Siren(api_key=...)` and
`request_handler(signing_secret)`. The handler posts an ask's answer with the
app's key.

The client talks to Siren Cloud, or to the Siren in `SIREN_BASE_URL` when
it's set, which `siren apps apply` writes for a sandbox. A `base_url` passed
in code wins. An ask names the address Siren wants its answer at, and the handler
refuses an ask from any other Siren than the client's with a 500, and tells
`on_error` both addresses and the `base_url` to set. The answer only ever goes
to the client's own address, never to one a request names.

Each kind of feature changes only the function, as
[Feature kinds](#feature-kinds) shows. The function gets a
`siren.AppTell` or a `siren.AppAsk`, both kinds of `siren.AppRequest`: `request.id` is the request key, the same on every send,
`request.data` is the question, `request.feature` holds the feature's `kind`
and `key`, `request.is_ask` is true for an ask, `request.test` is true on a
[test request](#test-requests), and `request.answer_by` is when Siren wants an
ask's answer. A function that writes or pays returns early on a test request,
as the one above does. A tell's
function returns nothing. An ask's function returns the answer, or
`answers.later()` when the app will answer by itself with
`apps.requests.answer()`. An ask's function that returns nothing is an error,
which goes to `on_error`, so a forgotten `return` shows in the log.

Annotate the function and mypy or your editor checks it against its kind.
`on()` has an overload for each kind Siren sends, so a misspelled field or
outcome fails the type check, and so does another kind's question:

<!-- readme-test: typed -->
```python
from typing import Union

import siren
from siren import AppAnswerLater, AppAsk, DistributionResolverAnswer, DistributionResolverQuestion

@answers.on("distributionResolver", "split-by-score")
def split(request: AppAsk[DistributionResolverQuestion]) -> Union[DistributionResolverAnswer, AppAnswerLater]:
    pool, metrics = request.data["pool"], request.data["metrics"]  # request.data["pools"] fails the check
    if not metrics:
        return answers.later()  # nothing to share yet, answer later with apps.requests.answer()
    shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in metrics})
    return {"outcome": "allocated", "units": pool["units"], "shares": shares}
```

Each kind's types are named for it: `<Kind>Question` and `<Kind>Answer` for
an ask, and `<Kind>Tell` for a tell, such as `PayoutMethodQuestion` and
`PostConversionActionTell`. A function for a kind from `extra_kinds` takes
any request.

Siren may send a tell twice, which is why the function above writes once by
`request.id`. An ask's function can run more than once for one request too:
on a repeat delivery, and again on each send after its answer window closes.
Write once by `request.id` and return the same answer each time. Siren takes
the same answer again as a duplicate and refuses a different one with 409.

### Try it without Siren

`siren.testing` signs a request the way Siren does, for any kind, with that
kind's sample question from Siren's own fixtures. The handler's `simulate()`
runs it through every check and the function, and hands back the reply's
`status` and `body` and, for an ask, the `answer` it would post. It posts
nothing. It checks an ask's answer the way Siren's answer route does, rule
for rule, so an answer Siren would refuse comes back as an `error` with code
`app_answer_invalid` and Siren's own words. `error` also holds what the
function raised, or the error for an ask's function that returned nothing.
`simulate()` never raises for a failed function, so assert on `result.error`.

In a pytest file beside the app, with `SIREN_SIGNING_SECRET` set as for the
app, and the distribution resolver from
[Feature kinds](#distribution-resolver-distributionresolver) registered:

<!-- readme-test: try-it -->
```python
from siren.testing import app_request, sample_data

from my_app import answers  # the module that holds the handler


def test_tells_the_crm():
    assert answers.simulate(app_request("postConversionAction", "tell-the-crm")).status == 200


def test_splits_by_score():
    question = sample_data("distributionResolver")  # change it to try an edge case
    result = answers.simulate(app_request("distributionResolver", "split-by-score", data=question))
    assert result.error is None
    assert result.answer["shares"] == {"41": 6000, "43": 4000}
```

`app_request()` takes `data`, `secret`, `id`, `app`, `base_url`,
`timestamp` and `test` too, and returns the `method`, `headers` and
`raw_body`, so the same request can go to a running app with any HTTP
client. That app then posts its answer to Siren, which refuses a request id
it never sent, so `simulate()` is the way to see an answer before Siren sends
real requests.

A tell's or `on_event` function that runs longer than Siren's 8 seconds
comes back from `simulate()` with an `AppSlowTellError` in `error`, so a test
catches it.

#### Test requests

`siren trigger ask <app> <feature>` makes Siren send a feature's sample
request now, marked as a test with `"test": true`. On a live Siren it needs
`--live` and a yes, and it refuses a payout method. Your function reads it as `request.test`, which is `False` on every
real request. Siren checks a test ask's answer by the kind's own rules and
applies nothing. A handler must never move money or write records on a test
request. Answer it as usual, so Siren checks the answer, and stop there, as
the [payout method](#payout-method-payoutmethod) sample does.
`app_request(kind, key, test=True)` builds one for a test of your own.

#### Test an `on_event` function

`webhook_event(type, data=None)` signs a webhook event the way Siren signs
one to an app's address, with `X-Siren-Event` naming the type and a new
`evt_` delivery id. Left out, `data` is `sample_event_data(type)`, the payload
Siren's event reference documents for that type, so a test of an `on_event`
function needs no hand signing:

<!-- readme-test: on-event-test -->
```python
import siren
from siren.testing import sample_event_data, webhook_event

answers = siren.Siren().apps.request_handler()
approved = []
answers.on_event("conversion.approved", lambda event: approved.append(event.data["conversionId"]))

# A signed conversion.approved with Siren's sample data. Pass your own data as the second argument.
result = answers.simulate(webhook_event("conversion.approved"))
assert (result.status, result.error) == (200, None)
assert approved == [sample_event_data("conversion.approved")["conversionId"]]
```

Siren's reference documents no payload for `credit.issued`,
`credit.redeemed`, `currency.created` and `currency.deleted`, so pass their
data yourself. `siren.testing.EVENT_TYPES` lists the rest.

#### Replay a stored request

`app_request_from_record(record, key=None)` turns a request Siren stored into
a test input, signed again with the same id, kind, mode, `test`, data and
`answerBy`. Save the request with the command-line tool, which reads it with
your signed-in session, since Siren refuses an app's key on that route.
`--json` arrives with the tool's next release:

```sh
siren apps requests <id> --json > request.json
```

`record` is that file's text, or the same dict parsed. The file names the
app and the feature's key, so the helper needs nothing else. Once the app is
gone the file's `featureKey` is null, so pass `key`, which
`siren apps show <app>` lists beside each feature. A raw request body, as
your app logged it, names its key already.

<!-- readme-test: replay -->
```python
from pathlib import Path

from siren.testing import app_request_from_record

stored = Path("request.json").read_text()
result = answers.simulate(app_request_from_record(stored))
print(result.answer, result.error)
```

`simulate()` then runs your function on the exact question Siren asked, so a
refused or failed answer can be fixed in a test before you resend the request
with `siren apps resend <id>`.

### Mount it on your framework

The handler imports no framework until you use its face. For the other
frameworks, keep the first lines of the sample above, through the function,
and mount the handler in place of the Flask lines.

Django, as a view that is already `csrf_exempt`. Put it in `urls.py`:

<!-- readme-test: handler-django -->
```python
from django.urls import path

urlpatterns = [path("siren", answers.django)]
```

FastAPI or Starlette, as an ASGI app on one route:

<!-- readme-test: handler-asgi -->
```python
from fastapi import FastAPI

app = FastAPI()
app.add_route("/siren", answers.asgi, methods=["POST"])
```

Any WSGI server, such as gunicorn, uWSGI or mod_wsgi, as the module's
`application`:

<!-- readme-test: handler-wsgi -->
```python
application = answers.wsgi
```

Any other framework, by calling the handler with the raw body and the
headers. It returns an `AppRequestReply` with `status`, `body`, `headers`,
`content` and `after`. Send the reply first, then run `after`, which holds an
ask's work:

<!-- readme-test: handler-call -->
```python
def siren_view(raw_body, headers, method):
    reply = answers(raw_body, headers, method=method)
    send_reply(reply.status, reply.headers, reply.content)  # your framework's reply
    if reply.after:
        reply.after()  # an ask's answer, once the reply has gone
```

Flask and WSGI run an ask's work once the server has sent the reply. ASGI
runs it after the reply in the same call. Django starts it on a thread, since
Django has no public hook after the reply. Each face reads at most 1 MiB and
answers 413 for more, which is far more than Siren sends.

### Register functions

`on` works as a decorator, as above, or as a call that returns the handler,
so calls chain. `on_event` takes a webhook event sent to the same address:

<!-- readme-test: register -->
```python
answers.on("distributionResolver", "split-by-score", split)  # one feature
answers.on("distributionResolver", split)                    # every feature of the kind
answers.on_event("conversion.approved", noted)               # a webhook at the same address
```

A function for the key wins over one for the kind. An `async def` function is
awaited, and under ASGI a plain one runs on a worker thread. An `on_event`
function gets a `siren.WebhookEvent`, and
[`webhook_event()`](#test-an-on_event-function) signs one for a test.

`on` takes the 13 kinds Siren sends, listed in `siren.APP_REQUEST_KINDS`, and
refuses any other name at once with a `siren.AppHandlerError` that says what to write.
`on("distributionResolvr", split)` raises "distributionResolvr is not a
feature kind. Did you mean distributionResolver?" A screen name, snake_case,
`customEvent`, a webhook event's name and the old `fulfillmentMethod` each get
their own pointer, so a typo fails when the app starts, not as 404s for
hours.

### Options

`request_handler(signing_secret=None, *, tolerance_seconds, run_later, on_error, logger, extra_kinds)`
reads `SIREN_SIGNING_SECRET` when no secret is passed, and takes these
options:

- `tolerance_seconds` is how far the signed time may be from the clock, 300
  by default and at most 3600.
- `run_later` is handed an ask's work as a callable with no arguments, in
  place of running it after the reply. Pass it on a host that stops work once
  it has replied, such as a function host, or to send the work to a queue.
  A tell's function can call your `run_later` too, for the slow part of its
  work. A tell or an `on_event` function must finish within Siren's 8
  seconds (`siren.TELL_TIME_LIMIT_SECONDS`), so save what you need, hand the
  rest over and return.
- `on_error(error, request)` gets every failure the reply can't show: a
  function that raised, an answer Siren refused, a refused signature
  (`SignatureVerificationError`, still answered 401, with `request` `None`)
  and a tell or `on_event` function that took longer than 8 seconds
  (`AppSlowTellError`). By
  default it writes one line through `logger` with the kind, key and request
  id, or an event's type and delivery id, then the error's message and code.
  A traceback follows only when your own function raised. A failed tell's or
  ask's line says when Siren sends it again, such as "Siren sends it again
  after 16:58 UTC, when its answer window closes." A refused signature's line
  names what to check, such as "Siren request handler: refused a request with
  401. The request signature does not match. ... Check SIREN_SIGNING_SECRET:
  it must be this app's signing secret, and it changes when the secret is
  rotated." After five in one minute, the rest are counted into one line. It
  never writes the request's data or headers.
- `logger` is where the default `on_error` writes, and where the handler
  writes one `debug` line per request it handled, with its status and time.
  It takes a `logging.Logger`, or anything with `debug`, `info`, `warning`
  and `error`, such as a `LoggerAdapter`, and is the `siren` logger by
  default. Refused signatures and slow tells are warnings, and every other
  failure an error.
- `extra_kinds` lists kinds a newer Siren sends that this SDK doesn't know
  yet, such as `["refundPolicy"]`, for `on` to take.

<!-- readme-test: options -->
```python
import logging
from concurrent.futures import ThreadPoolExecutor

workers = ThreadPoolExecutor(max_workers=4)

def report(error, request):
    logging.getLogger("my-app").warning("Siren request %s failed: %s", getattr(request, "id", None), error)

answers = client.apps.request_handler(run_later=workers.submit, on_error=report)
```

### Split a pool

`apps.split_pool(pool, weights)` splits the pool a resolver is offered.
`weights` maps each id to a weight, or is a list of `(id, weight)` pairs. It
returns `{str(id): amount}`, each a whole step of the currency, which is
`10 ** (scale - decimalPlaces)` stored units (a cent for dollars, a yen for
yen). Each amount is rounded down, so the sum never passes `pool["value"]`,
and the pool is split evenly when every weight is 0. It raises `ValueError`
for a negative or non-finite weight. It reads nothing over the network.

<!-- readme-test: split-pool -->
```python
dollars = {"value": 10000, "units": "USD", "currency": {"scale": 2, "decimalPlaces": 2}}
print(client.apps.split_pool(dollars, {41: 30, 43: 20}))  # {'41': 6000, '43': 4000}

yen = {"value": 1000, "units": "JPY", "currency": {"scale": 2, "decimalPlaces": 0}}
print(client.apps.split_pool(yen, [(1, 1), (2, 1), (3, 1)]))  # {'1': 300, '2': 300, '3': 300}
```

### What the handler replies

The handler checks each request the way `apps.verify_request()` does, then
replies:

| Request | Reply |
|---|---|
| Not a POST | 405 |
| A body over 1 MiB, more than Siren sends | 413, read no further |
| A body a parser already read, so the signed bytes are gone | 500, and `on_error` gets a `SirenError` that says to mount the handler before the JSON parser |
| A signature that is missing or wrong, or a time outside `tolerance_seconds` | 401, and `on_error` gets a `SignatureVerificationError` whose `code` says which check failed. The default line warns with what to check |
| A signed body that is not JSON, or not an app request of version 1 | 400, naming the first field that fails, such as "The request's `id` must be `apr_` followed by 32 lowercase hex characters." |
| An ask whose `answerUrl` is on another Siren than the client's `base_url` | 500, and `on_error` gets a `SirenError` naming both addresses and the `base_url` to set |
| A tell with a function | 200 once the function returns, 500 when it raises, so Siren sends it again. A tell must finish within 8 seconds. Past that Siren has stopped waiting, counts a timeout and sends the tell again, so the function runs twice. `on_error` gets an `AppSlowTellError`, and `simulate()` returns it |
| An ask with a function | 200 at once. The function runs after the reply, and the handler posts what it returns, or nothing for `answers.later()` |
| When your function fails | A tell gets 500, and Siren tries again about 1, 2, 4 and 8 minutes later. An ask already got its 200, so Siren sends it again when its answer window closes, 60 minutes by default, and you can resend it sooner with `siren apps resend <id>` |
| A kind and key with no function | 404 `No function handles <kind> <key>.`, so Siren shows the request under Needs attention once its sends run out |
| Another `X-Siren-Event`, such as `conversion.approved` | 200 once the `on_event` function returns, 500 when it raises, and 200 when there is none. Like a tell, the function must finish within 8 seconds, and a slower one reaches `on_error` as an `AppSlowTellError` |

Every reply is JSON with `Cache-Control: no-store`, and its words never hold
the secret or the body. [Errors and retries](#errors-and-retries) says what
happens to an answer after the reply.

## Develop on your machine

Siren can send a sandbox app's requests to the handler on your laptop with no
tunnel and no public address. `listen` and `trigger`, and `siren apps apply`
declaring a sandbox app with no `url`, arrive with the command-line tool's
next release.

1. With a sandbox key, one that starts `sk_test_`, run `siren apps apply`.
   On a sandbox it declares the app with no `url` and writes the app's key,
   `SIREN_API_KEY`, and the sandbox's address, `SIREN_BASE_URL`, to
   `.env.sandbox`. The client reads `SIREN_BASE_URL`, so the handler posts a
   sandbox ask's answer to the sandbox. An app with no address has no signing
   secret yet.
2. In its own terminal, run `siren listen --forward-to localhost:3000/siren`
   and leave it open. When it opens a session it writes the signing secret,
   `SIREN_SIGNING_SECRET`, to `.env.sandbox`. While it runs, Siren sends that
   app's asks, tells and webhook events to your handler, signed with that
   secret, and prints each reply. Your handler posts an ask's answer to Siren
   as it always does. Listening works only on a sandbox Siren.
3. Start your app with the file loaded. The client and handler read the
   process environment only, so export the file's lines first:
   `set -a; . ./.env.sandbox; set +a; flask --app app run --port 3000`, or
   with python-dotenv, `dotenv -f .env.sandbox run -- flask --app app run --port 3000`.
   Restart the app whenever `listen` says it saved a new secret.
4. In another terminal, `siren trigger ask <app> <feature>` sends a real
   sample request for one feature through it, marked as a
   [test request](#test-requests). On a live Siren it needs `--live` and a
   yes, and it refuses a payout method.

The `siren` command ships with the Node package, and you don't need a Node
project to run it. With Node installed, run any `siren` command this README
or an error names as `npx @novatorius/siren <command>`, such as
`siren listen`, `siren apps resend <id>`, or `siren apps rotate-key <app key>`
when Siren refuses the app's key. Keep the scope, because the unscoped `siren`
on npm is an unrelated project.

Turn on the handler's debug lines while you work, and it writes one line per
request with its status and time, such as "Siren request handler
(distributionResolver split-by-score apr_...): 200 in 3 ms":

<!-- readme-test: debug-lines -->
```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("siren").setLevel(logging.DEBUG)
```

## Feature kinds

Each section names a kind as Siren Cloud's screens show it, with the kind
Siren's code and `request.feature["kind"]` use beside it. `siren.FeatureKind`
holds each kind's string, and each member's docs give its screen name, so
your editor shows `FeatureKind.INCENTIVE_RESOLVER` as Attribution model.
`siren.APP_KINDS` holds Siren's own list of the kinds, with each one's name
and description, from the same snapshot of Siren's registry. A tell tells the app something and waits only for
a 2xx. An ask waits for an answer, which the handler posts for you. Every
question below is the `data` Siren sends in a request, and every function
runs inside the handler of [Answer Siren in a few lines](#answer-siren-in-a-few-lines).

Siren checks each answer against its kind and refuses one that doesn't fit
with 422 `app_answer_invalid` and the reason. Before posting, the handler
checks only what every answer needs. `simulate()` runs Siren's own check for
each kind as well, so a test sees the refusal first. An ask that gets no answer is sent again, three sends in all,
and then needs attention, as [Errors and retries](#errors-and-retries)
explains, along with how long each kind waits.

### Post-conversion action (`postConversionAction`)

A program picks it. Siren tells the app each time a conversion is approved in
the program. A test sale's conversion is never sent. The question is
`siren.PostConversionActionTell`:

<!-- readme-test: question-postConversionAction -->
```json
{
  "conversion": {"id": 1042, "type": "sale", "status": "complete", "engagementId": 880, "opportunityId": 615, "collaboratorId": 42, "transactionId": 3307, "obligationId": 2210},
  "reward": {"value": 1500, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "program": {"id": 3, "name": "Partner program"}
}
```

`conversion` holds the approved conversion's ids. `reward` is the reward it
earned, or `None`, with `value` as a stored whole number, so 1500 in US
dollars is $15.00. The function is the one in the first sample:

<!-- readme-test: fn-postConversionAction -->
```python
@answers.on("postConversionAction", "tell-the-crm")
def tell_the_crm(request):
    if request.test:  # a test request, as siren trigger ask sends: write nothing
        return
    # Siren may send it twice, so add it once, keyed by request.id.
    add_to_crm(request.id, request.data["conversion"], request.data["reward"])
```

The handler replies 200 once the function returns, and 500 when it raises.

### Post-allocation action (`postAllocationAction`)

A distributor picks it. Siren tells the app each time an allocation completes
for the distributor. The question is `siren.PostAllocationActionTell`:

<!-- readme-test: question-postAllocationAction -->
```json
{
  "allocation": {"distributionId": 21, "obligationId": 2290, "collaboratorId": 42, "status": "complete", "value": 6000, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "distributor": {"id": 2, "name": "Partner pool"}
}
```

`allocation` is one collaborator's share of a distribution, with `value` as a
stored whole number, so 6000 in US dollars is $60.00.

<!-- readme-test: fn-postAllocationAction -->
```python
@answers.on("postAllocationAction", "note-the-share")
def note_the_share(request):
    if request.test:  # a test request, as siren trigger ask sends: write nothing
        return
    # Siren may send it twice, so note it once, keyed by request.id.
    note_share(request.id, request.data["allocation"])
```

### Distribution resolver (`distributionResolver`)

A distributor picks it. Siren asks the app how to divide a distribution's
pool when the distribution closes, and waits for the answer before it pays
anyone. The question is `siren.DistributionResolverQuestion`:

<!-- readme-test: question-distributionResolver -->
```json
{
  "distribution": {"id": 21, "distributorId": 2, "periodStart": "2026-10-01T00:00:00+00:00", "periodEnd": "2026-11-01T00:00:00+00:00"},
  "distributor": {"id": 2, "name": "Partner pool"},
  "pool": {"value": 10000, "total": 10000, "paid": 0, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "metrics": [{"metricId": 41, "collaboratorId": 42, "score": 30, "triggerCount": 3}, {"metricId": 43, "collaboratorId": 57, "score": 20, "triggerCount": 2}]
}
```

`pool["value"]` is what this answer may hand out, as a stored whole number.
`total` is the whole pool and `paid` what collaborators already hold from it.
Each of `metrics` needs a share.

<!-- readme-test: fn-distributionResolver -->
```python
@answers.on("distributionResolver", "split-by-score")
def split_by_score(request):
    pool, metrics = request.data["pool"], request.data["metrics"]
    # Whole currency steps, never more than the pool, split evenly when every score is 0.
    shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in metrics})
    return {"outcome": "allocated", "units": pool["units"], "shares": shares}
```

It answers `siren.DistributionResolverAnswer`:

<!-- readme-test: answer-distributionResolver -->
```json
{"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4000}}
```

`shares` holds each `metricId` of the question as text, with a whole number
of 0 or more in the pool's stored units that the currency can show. The
shares may add up to less than `pool["value"]`, never more.

### Attribution model (`incentiveResolver`)

Siren's documentation calls this an incentive resolver. A program picks it. Siren asks
the app how to split the program's reward among the collaborators a sale,
renewal or lead converted, and the rewards wait for the answer. A partial
refund asks again about the smaller pool. The question is
`siren.IncentiveResolverQuestion`:

<!-- readme-test: question-incentiveResolver -->
```json
{
  "reason": "sale",
  "program": {"id": 3, "name": "Partner program", "status": "active"},
  "conversionType": "sale",
  "transaction": {"id": 3307, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "pool": {"value": 1500, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "conversions": [{"conversionId": 1042, "engagementId": 880, "collaboratorId": 42, "collaboratorStatus": "active", "score": 60, "engagementCreatedDate": "2026-11-02T09:15:00+00:00"}, {"conversionId": 1043, "engagementId": 881, "collaboratorId": 57, "collaboratorStatus": "active", "score": 40, "engagementCreatedDate": "2026-11-09T16:40:00+00:00"}]
}
```

`reason` is `sale`, or `refund` when a partial refund asks again about
rewards already made. `transaction` is `None` for a lead. `pool` is the
reward the program's incentive structure worked out, as a stored whole
number.

<!-- readme-test: fn-incentiveResolver -->
```python
@answers.on("incentiveResolver", "split-the-reward")
def split_the_reward(request):
    pool, conversions = request.data["pool"], request.data["conversions"]
    rewards = client.apps.split_pool(pool, {c["conversionId"]: c["score"] for c in conversions})
    return {"outcome": "rewards", "units": pool["units"], "rewards": rewards}
```

It answers `siren.IncentiveResolverAnswer`:

<!-- readme-test: answer-incentiveResolver -->
```json
{"outcome": "rewards", "units": "USD", "rewards": {"1042": 900, "1043": 600}}
```

`rewards` holds every `conversionId` of the question as text, each a whole
number from 0 up to `pool["value"]`, or `None` for a collaborator who earns
nothing. The rewards together may add up to more than the pool.

### Incentive structure (`incentiveType`)

Siren's documentation calls this an incentive type. A program picks it as its
incentive type, for the conversions its `conversionTypes` setting lists.
Siren asks the app which engagements a sale, lead or renewal converts and the
pool they share. The program awards nothing until the answer. The question is
`siren.IncentiveTypeQuestion`:

<!-- readme-test: question-incentiveType -->
```json
{
  "reason": "sale",
  "program": {"id": 3, "name": "Partner program", "units": "USD"},
  "conversionType": "sale",
  "opportunity": {"id": 615},
  "transaction": {"id": 3307, "status": "complete", "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}, "total": 15000, "lines": [{"id": 5501, "type": "lineItem", "name": "Annual plan", "quantity": 1, "value": 15000}]},
  "binding": null,
  "engagements": [{"engagementId": 880, "collaboratorId": 42, "score": 60, "createdDate": "2026-11-02T09:15:00+00:00"}, {"engagementId": 881, "collaboratorId": 57, "score": 40, "createdDate": "2026-11-09T16:40:00+00:00"}]
}
```

`transaction` is `None` for a lead, which names its `binding` instead. Its
`total` and each line's `value` are stored whole numbers. A partial refund
asks again with `reason` `refund` and the award's `conversions`, and its
answer changes only the pool.

<!-- readme-test: fn-incentiveType -->
```python
@answers.on("incentiveType", "pay-on-closed-deals")
def pay_on_closed_deals(request):
    sale, ids = request.data["transaction"], [e["engagementId"] for e in request.data["engagements"]]
    if not sale or sale["units"] != request.data["program"]["units"]:
        return {"outcome": "none"}  # A lead earns nothing here.
    return {"outcome": "award", "units": sale["units"], "pool": sale["total"] // 10, "engagementIds": ids}
```

It answers `siren.IncentiveTypeAnswer`, an award or `{"outcome": "none"}`:

<!-- readme-test: answer-incentiveType -->
```json
{"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880, 881]}
```

`pool` is a whole number of 0 or more in the program's `units`, and
`engagementIds` lists at least one engagement of the question, each once.
The program's attribution model divides the pool among them.

### Metric calculation (`metricCalculation`)

Siren's documentation calls this a calculation strategy. A distributor's metric type
picks it. Siren asks the app who scores, and how much, each time a metric
triggers. The metric scores nothing until the answer, and the distribution it
lands in waits for the answer before it pays anyone. The question is an
`MetricCalculationQuestion`, and the answer an
`MetricCalculationAnswer`. It looks like this:

<!-- readme-test: question-metricCalculation -->
```json
{
  "distributor": {"id": 2, "name": "Partner leaderboard"},
  "distribution": {"id": 21, "periodEnd": "2026-12-01T00:00:00+00:00"},
  "metricType": "referredSiteVisit",
  "distributorMetricTypeId": 55,
  "triggeringCollaborator": {"id": 42, "name": "Riley Rep"},
  "opportunityId": 615,
  "points": null,
  "cause": {"id": "9f2c4e1a7b3d5c60", "type": null, "causeId": null},
  "sale": null
}
```

`points` is an amount a person entered, or `None`. When it is set, every
collaborator you score gets it in place of your score. `cause["id"]` is the
trigger's own random id, so two visits make two requests. `sale` is the sale
behind a sale trigger, or `None`.

<!-- readme-test: fn-metricCalculation -->
```python
@answers.on("metricCalculation", "visit-score")
def visit_score(request):
    # Score the collaborator who triggered it, more for a sale.
    score = 10 if request.data["metricType"] == "awardedSale" else 1
    return {"outcome": "scores", "results": [{"collaboratorId": request.data["triggeringCollaborator"]["id"], "score": score}]}
```

It answers:

<!-- readme-test: answer-metricCalculation -->
```json
{"outcome": "scores", "results": [{"collaboratorId": 42, "score": 1}]}
```

`results` holds up to 50 entries, each a `collaboratorId` and a whole-number
`score` of 0 or more, with no collaborator twice. An empty list scores
nobody.

### Customer filter for distributors (`metricFilter`)

Siren's documentation calls this a metric filter. A distributor picks it. Siren asks
the app whether a metric may score each time one triggers, after the
distributor's built-in filters allow it. The question is an
`MetricFilterQuestion`, and the answer an `MetricFilterAnswer`. It
looks like this:

<!-- readme-test: question-metricFilter -->
```json
{
  "distributor": {"id": 2, "name": "Partner leaderboard"},
  "distribution": {"id": 21},
  "collaborator": {"id": 42, "name": "Riley Rep"},
  "metricType": "awardedSale",
  "opportunityId": 615,
  "points": null,
  "cause": {"id": "4b7e0c9d2a1f3e58", "type": "transaction", "causeId": "3307"},
  "sale": {"transactionId": 3307, "conversionId": 1042, "excludedLineIds": []}
}
```

<!-- readme-test: fn-metricFilter -->
```python
@answers.on("metricFilter", "refund-check")
def refund_check(request):
    # Suppress a sale the CRM knows was refunded. Allow everything else.
    if request.data["sale"] and is_refunded(request.data["sale"]["transactionId"]):
        return {"outcome": "suppress", "reason": "The order was refunded within 30 days."}
    return {"outcome": "allow"}
```

It answers `{"outcome": "allow"}`, or a suppress with a plain text `reason` of
1 to 200 characters, which Siren notes on the distributor:

<!-- readme-test: answer-metricFilter -->
```json
{"outcome": "suppress", "reason": "The order was refunded within 30 days."}
```

### Transaction filter (`transactionFilter`)

Siren's documentation calls this a transaction compiler. A program or a distributor
picks it. Siren asks the app which of a sale's own lines count, once per sale
for each program or distributor that picks it. The question is
`siren.TransactionFilterQuestion`:

<!-- readme-test: question-transactionFilter -->
```json
{
  "for": {"type": "program", "id": 3, "name": "Partner program"},
  "transaction": {"id": 3307, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "lines": [{"id": 5501, "type": "lineItem", "name": "Annual plan", "quantity": 1, "value": 1500, "productId": null}, {"id": 5502, "type": "discount", "name": "Welcome discount", "quantity": 1, "value": -300, "productId": null}],
  "distribution": null,
  "refundDistribution": null
}
```

`for` names the program or distributor asking. Each line's `type` is
`lineItem`, `discount`, `fee`, `shipping` or `tax`, and its `value` is the
unit value as a stored whole number. `distribution` is `None` for a program,
and for a distributor the period the sale's value lands in.
`refundDistribution` is `None`, or the later period a refund came in while
the ask was open. A refund reuses the stored answer, so Siren never sends
refund lines.

<!-- readme-test: fn-transactionFilter -->
```python
@answers.on("transactionFilter", "plan-lines")
def plan_lines(request):
    lines = request.data["lines"]
    return {"outcome": "filtered", "include": [line["id"] for line in lines if line["type"] == "lineItem"]}
```

It answers `siren.TransactionFilterAnswer`, the ids of the lines that
count, each at most once. An empty list includes nothing. When a record picks
more than one filter, a line counts when any of them includes it.

<!-- readme-test: answer-transactionFilter -->
```json
{"outcome": "filtered", "include": [5501]}
```

### Engagement calculation (`engagementCalculation`)

Siren's documentation calls this a calculation strategy. A program's engagement type
picks it. Siren asks the app who earns an engagement, and its score, each
time one triggers. Siren makes no engagement until the answer, then makes the
ones it names, dated when the trigger happened. A sale on that program and
opportunity waits for the answer. The question is
`siren.EngagementCalculationQuestion`:

<!-- readme-test: question-engagementCalculation -->
```json
{
  "program": {"id": 3, "name": "Partner program"},
  "engagementType": "referredSiteVisit",
  "programEngagementTypeId": 55,
  "args": {"weight": "2"},
  "opportunity": {"id": 615, "customerId": 77},
  "triggeringCollaborator": {"id": 42, "name": "Riley Rep"},
  "cause": {"id": "9f2c4e1a7b3d5c60", "event": "referredSiteVisit", "occurredAt": "2026-11-17T12:20:00+00:00"}
}
```

`args` holds the settings a person gave the engagement type, as text by
name. `opportunity["customerId"]` is the customer Siren matched, or `None`.
`cause["occurredAt"]` dates the engagements the answer makes.

<!-- readme-test: fn-engagementCalculation -->
```python
@answers.on("engagementCalculation", "visit-weight")
def visit_weight(request):
    # Credit the collaborator who triggered it, more when Siren knows the customer.
    score = 10 if request.data["opportunity"]["customerId"] else 1
    return {"outcome": "engagements", "results": [{"collaboratorId": request.data["triggeringCollaborator"]["id"], "score": score}]}
```

It answers `siren.EngagementCalculationAnswer`, with the same limits as a
metric calculation. An empty list makes no engagement.

<!-- readme-test: answer-engagementCalculation -->
```json
{"outcome": "engagements", "results": [{"collaboratorId": 42, "score": 10}]}
```

### Customer filter for programs (`engagementFilter`)

Siren's documentation calls this an engagement filter. A program picks it. Siren asks
the app whether an engagement may be made each time one triggers. Siren makes
no engagement until the answer, and a sale on that program and opportunity
waits for it. The question is `siren.EngagementFilterQuestion`:

<!-- readme-test: question-engagementFilter -->
```json
{
  "program": {"id": 3, "name": "Partner program"},
  "collaborator": {"id": 42, "name": "Riley Rep"},
  "opportunity": {"id": 615, "customerId": 77},
  "engagementType": "referredSiteVisit",
  "cause": {"id": "4b7e0c9d2a1f3e58", "event": "referredSiteVisit", "occurredAt": "2026-11-17T12:20:00+00:00"}
}
```

<!-- readme-test: fn-engagementFilter -->
```python
@answers.on("engagementFilter", "staff-check")
def staff_check(request):
    # Suppress a visit from someone who works for the partner. Allow everything else.
    if is_staff(request.data["collaborator"]["id"], request.data["opportunity"]["customerId"]):
        return {"outcome": "suppress", "reason": "The visitor works for the partner."}
    return {"outcome": "allow"}
```

It answers `siren.EngagementFilterAnswer`, an allow or a suppress with a
plain text `reason` of 1 to 200 characters, which Siren notes on the
opportunity:

<!-- readme-test: answer-engagementFilter -->
```json
{"outcome": "suppress", "reason": "The visitor works for the partner."}
```

### Program group structure (`programGroupSorter`)

Siren's documentation calls this a group sorter. A program group picks it. Siren asks
the app which program of the group a sale or lead tries first. No program in
the group is awarded until the answer, and later sales in the group wait
behind this one. A renewal doesn't ask. It goes to the program in the group
whose engagement is oldest. The question is `siren.ProgramGroupSorterQuestion`:

<!-- readme-test: question-programGroupSorter -->
```json
{
  "group": {"id": 5, "name": "Partner programs"},
  "conversionType": "sale",
  "opportunity": {"id": 615},
  "transaction": {"id": 3307, "units": "USD", "total": 15000},
  "programs": [{"id": 12, "name": "Resellers", "engagements": [{"engagementId": 880, "collaboratorId": 42, "score": 40, "createdDate": "2026-11-02T09:15:00+00:00"}]}, {"id": 9, "name": "Affiliates", "engagements": [{"engagementId": 881, "collaboratorId": 57, "score": 60, "createdDate": "2026-11-09T16:40:00+00:00"}]}]
}
```

`programs` holds each program of the group that takes the conversion, in the
group's order, with its active engagements on the opportunity, oldest first.
`transaction` is `None` for a lead.

<!-- readme-test: fn-programGroupSorter -->
```python
@answers.on("programGroupSorter", "pick-the-program")
def pick_the_program(request):
    # Try the program whose best engagement scores highest first.
    best = {p["id"]: max([0] + [e["score"] for e in p["engagements"]]) for p in request.data["programs"]}
    return {"outcome": "sorted", "programIds": sorted(best, key=lambda program: best[program], reverse=True)}
```

It answers `siren.ProgramGroupSorterAnswer`, every program id of the question
exactly once, in the order Siren tries them. The first program that converts
wins the group.

<!-- readme-test: answer-programGroupSorter -->
```json
{"outcome": "sorted", "programIds": [9, 12]}
```

### Collaborator group structure (`collaboratorGroupStructure`)

A collaborator group picks it. Siren asks the app who sits above and below
whom in the group each time a cascade runs over it, for a distributor's
metric cascade or a program's engagement cascade. The cascade credits nobody
above or below until the answer. The question is
`siren.CollaboratorGroupStructureDistributorQuestion` for a distributor,
as here, and `siren.CollaboratorGroupStructureProgramQuestion` for a
program, which carries an engagement calculation's keys in place of the
metric's:

<!-- readme-test: question-collaboratorGroupStructure -->
```json
{
  "group": {"id": 4, "name": "Northeast team"},
  "members": [{"collaboratorId": 63, "name": "Dana Director", "status": "active", "metadata": {"rank": 1}}, {"collaboratorId": 57, "name": "Morgan Manager", "status": "active", "metadata": {"rank": 2}}, {"collaboratorId": 42, "name": "Riley Rep", "status": "active", "metadata": {"rank": 3}}],
  "for": {"type": "distributor", "id": 2},
  "distributor": {"id": 2, "name": "Partner leaderboard"},
  "distribution": {"id": 21, "periodEnd": "2026-12-01T00:00:00+00:00"},
  "metricType": "referredSiteVisit",
  "distributorMetricTypeId": 55,
  "triggeringCollaborator": {"id": 42, "name": "Riley Rep"},
  "opportunityId": 615,
  "points": null,
  "cause": {"id": "9f2c4e1a7b3d5c60", "type": null, "causeId": null},
  "sale": null
}
```

`members` holds every member as the cascade read them, with their `status` at
the trigger, which Siren credits them by, and the `metadata` the group keeps
for them. Your answer covers exactly these members.

<!-- readme-test: fn-collaboratorGroupStructure -->
```python
@answers.on("collaboratorGroupStructure", "team-tree")
def team_tree(request):
    # Top first, by the rank your CRM keeps on each member.
    members = sorted(request.data["members"], key=lambda member: member["metadata"]["rank"])
    return {"outcome": "structure", "shape": "chain", "order": [member["collaboratorId"] for member in members]}
```

It answers `siren.CollaboratorGroupStructureAnswer`, a chain or a tree. A
chain's `order` lists every member's `collaboratorId` once, top first:

<!-- readme-test: answer-collaboratorGroupStructure -->
```json
{"outcome": "structure", "shape": "chain", "order": [63, 57, 42]}
```

A tree's `parents` maps every member's `collaboratorId`, as text, to another
member's id, or to `None` for a top member, with no cycle:
`{"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": 63, "42": 57}}`.

## Payout method (`payoutMethod`)

An autofulfiller picks it for one of the currencies the feature's
`currencies` setting names. Siren asks the app to pay a payout after payouts
are made, and when someone sends or retries one. The payout shows as
processing until the answer. The question is an `PayoutMethodQuestion`, and the
answer an `PayoutMethodAnswer`, either `PayoutMethodPaidAnswer` or `PayoutMethodFailedAnswer`. It
looks like this:

<!-- readme-test: question-payoutMethod -->
```json
{
  "payout": {"id": 88, "fulfillmentId": 31, "attempt": 1},
  "amount": {"value": 2500, "units": "USD", "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}},
  "collaborator": {"id": 42, "name": "Ana Ruiz", "email": "ana@example.com"}
}
```

`payout["attempt"]` counts Siren's asks about this payout, and a retry after
a failed answer asks again with it one higher. Pay each payout id once at
most, and answer a second ask about a payout you already paid with its first
reference. `amount` is a stored whole number in the currency's smallest unit,
so 2500 in US dollars is $25.00. The collaborator's `email` is `None` when
they have none.

<!-- readme-test: fn-payoutMethod -->
```python
@answers.on("payoutMethod", "acme-ledger")
def acme_ledger(request):
    if request.test:  # a test request, as siren trigger ask sends: answer, and pay nothing
        return {"outcome": "failed", "reason": "A test request, so nothing was paid."}
    # Pay each payout once, keyed by its id, and answer with your reference.
    paid = pay_once(request.data["payout"]["id"], request.data["collaborator"], request.data["amount"])
    return {"outcome": "paid", "reference": paid["id"]} if paid["ok"] else {"outcome": "failed", "reason": paid["error"]}
```

It answers `paid` with the payment's `reference` in your own system, up to
191 characters, or `failed` with a plain `reason` of up to 500 characters.
Siren marks a failed payout failed with your reason, and someone can send it
again.

<!-- readme-test: answer-payoutMethod -->
```json
{"outcome": "paid", "reference": "acme-ledger-5521"}
```

## Custom events with short-lived tokens

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

<!-- readme-test: token-asgi -->
```python
app.add_route("/siren/token", tokens.asgi, methods=["GET", "POST"])
```

Flask, through Werkzeug, which Flask ships with:

<!-- readme-test: token-flask -->
```python
from werkzeug.middleware.dispatcher import DispatcherMiddleware

app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/siren/token": tokens.wsgi})
```

Django, as a view, with `path("siren/token", siren_token)` in `urls.py`.
Calling the handler with the body and query gives the status and JSON, which
is also the way into any other framework:

<!-- readme-test: token-django -->
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

## Check and answer by hand

An app that queues the work, or answers after a person decides, checks a
request with `apps.verify_request()` and answers an ask with
`apps.requests.answer()`. The handler calls both inside, and an ask's
function that returns `answers.later()` leaves its answer to this path.

<!-- readme-test: by-hand -->
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
`SignatureVerificationError` with the reason, and a body that isn't a request
names the first field that fails. Pass the body exactly as
received. Siren may send one request more than once, so drop a repeat by
`request.id`. The request's `answer_by` and `answer_url` say when and where
Siren wants an ask's answer.

`answer()` sends `POST /apps/requests/{id}/answer` once and never retries
it, so a lost connection never sends a second answer. It raises `ValueError`
before sending for an id that is not a request key, an answer that is not a
mapping, one with no text `outcome`, and one over 16384 bytes as JSON. It
returns an `AppAnswerReceipt`, whose `duplicate` is true when Siren already
held this same answer.

### Find and resend requests

An admin's session lists the organization's requests, reads one, and sends
one again, as the Node SDK does. These routes don't take an app's key.

<!-- readme-test: requests -->
```python
client = siren.Siren(api_key=ADMIN_API_KEY)

for row in client.apps.requests.list(status="needs_attention"):
    print(row["id"], row["feature"], row["kind"], row["sendCount"], row["lastError"])

request = client.apps.requests.retrieve("apr_" + "b3" * 16)
print(request["data"], request["answer"], request["answeredByUser"])

client.apps.requests.resend(request["id"])
```

`list()` sends `GET /apps/requests` and returns the latest 50, newest first,
each a `siren.AppRequestRecord`. It pages with `limit` (1 to 100) and
`before`, a request id. A page with `before` holds only requests older than
that one, so the last id of a page reads the next. Siren ignores both for
`needs_attention`, whose list comes whole:

<!-- readme-test: requests-paging -->
```python
before = None
while True:
    page = client.apps.requests.list(limit=20, before=before)
    if not page:
        break
    for row in page:
        print(row["id"])
    before = page[-1]["id"]
```

 `status` is one of
`siren.APP_REQUEST_STATUSES`, typed `siren.AppRequestStatus`: `pending`,
`sent`, `delivered`, `answered`, `applying`, `applied`, `needs_attention` and
`dropped`. `retrieve()` sends
`GET /apps/requests/{id}` and returns a `siren.AppRequestDetail`, which adds
the `data` Siren sent, the `answer` it took and who answered by hand.

`resend()` sends `POST /apps/requests/{id}/resend` once and returns the
request as it now stands. An ask gets a new answer window, and a request
that holds an answer Siren hasn't applied gets that answer applied in place
of a new send. Siren refuses an `applied` request, a `delivered` tell and one
it would send while the app is off with 409 `app_request_not_resendable`,
raised as `ConflictError`.

## Errors and retries

What happens to an ask's answer after the handler's 200:

- The handler checks the function's answer the way `apps.requests.answer()`
  does, then posts it to `POST /apps/requests/{id}/answer` with the client's
  key. A bad answer goes to `on_error` and is not sent.
- A lost connection, a 429 or a 5xx sends the same answer again, up to the
  client's `max_retries`. That is safe, because Siren takes the same answer
  again as a duplicate.
- A refusal goes to `on_error` and is not sent again. Siren refuses an answer
  that doesn't fit the kind with 422 `app_answer_invalid`, a different second
  answer with 409 `app_request_already_answered`, and a late answer with 409
  `app_request_dropped`. After a 422 the default line says "Your function's
  answer broke Siren's rule. Reproduce it with simulate() on this request's
  data, fix it, then resend with `siren apps resend <id>`."
- A function that raises goes to `on_error`, and nothing is posted. So does
  an ask's function that returns nothing.
- Siren sends a failed ask again only when its answer window closes. The
  default `on_error` line says when, such as "Siren sends it again after
  16:58 UTC, when its answer window closes. Resend it sooner with
  `siren apps resend apr_b339...`." Siren's API resends a pending ask, while
  Siren Cloud offers Send again only once a request needs attention.

What Siren does when an app doesn't answer:

- A tell waits 8 seconds for a 2xx. A timeout or any other status is sent
  again about 1, 2, 4 and 8 minutes later, five tries in all. Then the
  request shows under Needs attention, where an admin can send it again.
- An ask waits until its `answerBy`, an hour after it was sent unless the
  feature's answer window says otherwise. Then Siren sends it again with a new
  window, three sends in all, and then the request needs attention, where an
  admin can send it again or answer it by hand.
- An ask that holds a reward, a score or a payout waits 24 hours at most.
  Then Siren drops it and the work goes on without the app: a sale earns no
  reward on that program, a metric scores nothing, a trigger makes no
  engagement, a cascade credits nobody, and a payout is marked failed. A
  distribution resolver's ask is the one ask that never drops. The
  distribution waits until an admin sends it again or answers it by hand.
- While an app's asks wait too long, Siren emails the app's problem
  contacts, or the organization's admins when it names none, about twice a
  week.

The errors the SDK raises all subclass `siren.SirenError`, with `message`,
`code` and `status_code`. For an app:

| When | Error |
|---|---|
| A request's signature, time or envelope fails a check | `SignatureVerificationError`, with the reason, and a `code` for a failed signature or time |
| A tell's or `on_event` function ran past Siren's 8 seconds | `AppSlowTellError`, to `on_error` and from `simulate()` |
| A mistake in the app's code that the handler finds: an unknown kind, a second function for one kind and key, a bad option, a function that returns nothing or an answer it can't post, a bad argument to `simulate()` | `AppHandlerError` |
| A bad argument to `apps.requests.answer()`, `list()`, `retrieve()` or `resend()`, before anything is sent | `ValueError` |
| Siren takes no answer from this key | `AuthenticationError`, `code` `ErrorCode.APP_ANSWER_UNAUTHORIZED` |
| Siren refuses the answer for its kind | `ValidationError`, `code` `app_answer_invalid` |
| A different answer to an answered ask, or an answer after the drop | `ConflictError`, `code` `app_request_already_answered` or `app_request_dropped` |
| The key may not answer this request, or it doesn't exist | `PermissionError` or `NotFoundError` |
| Siren can't be reached | `ConnectionError` |

From `apps.requests.answer()` these raise. From the handler they go to
`on_error`. The [Errors](#errors) table lists every other status.

## Record sales and verify webhooks

### Record a sale

<!-- readme-test: record-sale -->
```python
import siren

client = siren.Siren(api_key="sk_live_...")  # or set SIREN_API_KEY

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

<!-- readme-test: sale-items -->
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

<!-- readme-test: refund-visit -->
```python
client.events.refund(source="stripe", external_id="cs_test_a1b2c3")
client.events.site_visited(collaborator_id=88, user_id=12345)
```

### Verify a webhook

This section is for a webhook subscription's own address. An app's address
gets the newer `X-Siren-Signature-V2` header, which signs the time too, and the
request handler checks it for you, for app requests and for webhook events
sent there through `on_event`.

Siren signs every delivery with `X-Siren-Signature: sha256=<hex hmac>`, an
HMAC-SHA256 of the **raw request body** keyed by your subscription's signing
secret. `construct_event` verifies the signature (constant-time) and parses
the event in one call.

The body is the event's payload alone, such as `{"conversionId": 991}`. The
event type arrives in the `X-Siren-Event` header and the event id in
`X-Siren-Delivery`, so pass both headers along. Without `event_type`,
`construct_event` raises `SignatureVerificationError`, because the body alone
does not name the event. The event id stays the same when Siren retries a
delivery, so use it to drop repeats.

<!-- readme-test: webhook-flask -->
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

<!-- readme-test: verify-signature -->
```python
if client.webhooks.verify_signature(raw_body, signature_header, secret):
    ...
```

#### Webhook addresses

An API key and a webhook address are parts of an app. Siren makes, changes,
rotates and revokes them through its `/apps` routes, with a signed-in
session: declare the app's `url` and `events` in `siren.app.json` and run
`siren apps apply`, rotate the key with `siren apps rotate-key`, or use the
app's page in Siren Cloud. Run the `siren` CLI with npx, as
[Develop on your machine](#develop-on-your-machine) says. Siren has no
`POST /webhooks` and no `/api-keys` routes.

The SDK does the same for an app's key, and reads and removes an app's
address, with a signed-in dashboard session's token, passed as `api_key`.
No API key may call a webhook or apps route: Siren answers it with 401 and
`error.code` `api_key_not_accepted`.

<!-- readme-test: app-keys -->
```python
client = siren.Siren(api_key=SESSION_TOKEN)

groups = client.apps.scopes()  # what a key may hold, such as groups["ledger"]
made = client.apps.create_key("acme-payouts", scopes=["ledger:payouts:read"])
print(made["rawKey"])  # shown this once, so save it as the app's SIREN_API_KEY
client.apps.rotate_key("acme-payouts")  # a new rawKey, and the old key stops working
client.apps.revoke_key("acme-payouts")
```

`create_key()` makes a key for an app that has none, and Siren answers 409
`app_key_exists` for one that has a key. Each call is sent once and never
retried. This SDK has no call that makes or changes an app or its address, so
use `siren apps apply` for those.

<!-- readme-test: subscriptions -->
```python
client.webhooks.subscriptions.list()
client.webhooks.subscriptions.delete(subscription_id)
```

## What is Siren?

[Siren](https://sirenaffiliates.com) is an affiliate, referral, and incentive
platform. You register commerce events — sales, refunds, referred visits — and
Siren attributes them to collaborators, computes rewards, and pays out.
This SDK is the official Python client for the Siren API.

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
- **Webhook addresses** — list and remove an app's address with a signed-in
  dashboard session. An app's key and address are made through the apps
  routes, with `siren apps apply`.
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

Siren grants the ledger scopes only by their exact names. `*`,
`event:*`, wildcards such as `ledger:*`, and older names such as
`conversions:read` grant none of them, so a key made before these scopes
existed reads nothing until you replace it with one that holds them. A key's
requests count against the organization's API rate limit.

The two webhook scopes, `webhook-subscriptions:read` and the manage scope,
are retired. A key that still holds one keeps the string, and it opens
nothing. Webhook and apps routes take a signed-in session only, and Siren
answers a key there with `ApiKeyNotAcceptedError`. The reconciliation readers
also take a dashboard session's token or a connected site's token.

A key created with a `property_id` is bound to that store. It reads only its
own store's conversions, transactions and obligations, and Siren refuses it
on payouts with `StoreKeyNotAllowedError`. An
organization-wide key reads the whole organization, and narrows a ledger read
to one store with `property_id`:

<!-- readme-test: store-scope -->
```python
client.conversions.list(property_id=5, status="approved")
```

### App features

`apps.features` adds, reads, changes and removes one app's features through
`/apps/{app}/features`, with a signed-in dashboard session passed as the
client's key. A feature's kind and key never change once Siren makes it, so
`update()` refuses them. `delete()` archives a feature a record still picks
and returns it, and deletes one nothing picks:

<!-- readme-test: features -->
```python
added = client.apps.features.create("acme-payouts", {
    "kind": "distributionResolver",
    "key": "split-by-score",
    "name": "Split by score",
})
client.apps.features.update("acme-payouts", added["id"], {"name": "Split by score, then tenure"})
removed = client.apps.features.delete("acme-payouts", added["id"])
print(removed["archived"])  # False: no record picked it, so Siren deleted it
```

Siren answers 409 `app_features_need_key_and_address`
(`siren.ErrorCode.APP_FEATURES_NEED_KEY_AND_ADDRESS`) when the app lacks a key
or an address, since it asks the app at its address.

### Reconciliation

Thin paginated readers over Siren's ledger. `page` and `per_page` become
Siren's `number` and `offset`, and a page holds at most 200 records:

<!-- readme-test: reconciliation -->
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

<!-- readme-test: errors -->
```python
try:
    client.events.refund(source="stripe", external_id="unknown")
except siren.NotFoundError:
    print("no matching sale")
except siren.RateLimitError as err:
    print(f"retry in {err.retry_after}s")
```

### Configuration

<!-- readme-test: configuration -->
```python
client = siren.Siren(
    api_key="sk_live_...",  # SIREN_API_KEY when left out
    base_url="https://api.sirenaffiliates.com/siren/v1",  # the default, Siren Cloud
    timeout=30.0,       # seconds
    max_retries=2,      # exponential backoff on network errors and 429/5xx
)
```

Set `base_url` only for a local or sandbox Siren.

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
