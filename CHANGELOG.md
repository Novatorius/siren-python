# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Breaking

- An `on_event` function is timed like a tell's. Past Siren's 8 seconds it
  reaches `on_error` as an `AppSlowTellError` named by the event's type, and
  `simulate()` returns it, as the PHP SDK already does. Siren delivers webhook
  events with the same 8-second timeout, so a slower function ran twice
  without a word.
- The README no longer documents `api_keys` or
  `webhooks.subscriptions.create`. Siren answers `/api-keys` with 404 and
  `POST /webhooks` with 405, because an API key and a webhook address are
  parts of an app, made through `/apps` with `siren apps apply`. It no longer
  lists the retired `webhook-subscriptions` scopes as opening anything. The
  methods stay until the SDK's resources follow Siren's routes.
- The request handler hands a refused signature to `on_error` as a
  `SignatureVerificationError`, with `None` for the request, where it
  replied 401 and said nothing. The reply is still 401. A custom `on_error`
  that expected only failed functions now sees these too.
- The default `on_error` writes one line per failure and adds a traceback
  only when the app's own function raised. A 401 on the answer post was two
  chained tracebacks and is now one line. The line holds the message, the
  error's `code` in brackets when the message lacks it, and a cause as
  "(cause: ...)". An event's failure names its type and delivery id, as in
  "Siren request handler (conversion.approved evt_...): ...".
- After a 422 `app_answer_invalid` on the answer post, the line drops "Siren
  sends it again" and says "Your function's answer broke Siren's rule.
  Reproduce it with simulate() on this request's data, fix it, then resend
  with `siren apps resend <id>`."

- Each kind's types are named for the kind, with no `App` prefix and no old
  name kept: `<Kind>Question` and `<Kind>Answer` for an ask, `<Kind>Tell`
  for a tell, and the variant before the suffix, as in
  `PayoutMethodPaidAnswer`. `AppConversionActionData` is now
  `PostConversionActionTell`, `AppDistributionQuestion` is
  `DistributionResolverQuestion`, `AppRewardQuestion` is
  `IncentiveResolverQuestion`, `AppGroupOrderQuestion` is
  `ProgramGroupSorterQuestion`, `AppCollaboratorGroupChain` is
  `CollaboratorGroupStructureChainAnswer`, `AppPayoutPaid` is
  `PayoutMethodPaidAnswer`, and so on for every kind. Node and PHP use the
  same names.
- An answer's `shares`, `rewards` and `parents` are typed `Mapping`, not
  `Dict`, so the `Dict[str, int]` that `apps.split_pool()` returns fits
  `IncentiveResolverAnswer.rewards` under `mypy --strict`. The SDK writes any
  mapping in an answer as JSON, a read-only one included.
- `AppRequest` is generic. The handler and `apps.verify_request()` return an
  `AppAsk[Q]` or an `AppTell[D]`, and `on()` has a generated overload for
  each kind, so mypy checks a decorated function's `request.data` and its
  answer against the kind. Every fixed `outcome` and `reason` is a
  `Literal`, every fixed-shape `Dict[str, Any]` is a `TypedDict` read from
  Siren's question builders, and a metric's or engagement's `score` is an
  `int`. `answers.later()` returns the new `siren.AppAnswerLater`, and
  `siren.AppAskResult[A]` types an ask's return.
- A post-conversion action's `reward` and a transaction filter's
  transaction `currency` may be `None`, as Siren sends them.
- An engagement type's `args` reads as `{}` when Siren sends `[]`, for an
  engagement calculation and a collaborator group structure. `raw` keeps the
  body as sent.
- The handler raises the new `siren.AppHandlerError`, a `SirenError`, for
  a mistake in the app's code, where it raised `ValueError` or `TypeError`:
  an unknown or empty kind, an empty key, a function that is not callable, a
  second function for one kind and key, a bad handler option, and a bad
  argument to `simulate()`. A function that returns nothing and an answer the
  handler won't post reach `on_error` and `simulate()`'s `error` as an
  `AppHandlerError` too. The messages are unchanged. The resource methods
  still raise `ValueError` for a bad argument.
- The default `on_error` line ends "Resend it sooner with `siren apps resend
  <id>`." Siren's API resends a pending ask, and Siren Cloud offers Send
  again only once a request needs attention.

- Code written against the apps branch before this entry says "feature"
  where it said "item", as the docs, Siren Cloud's screens and Siren's
  renamed envelope and routes do. `siren.FeatureKind` replaces
  `AppItemKind`, with no old name, and a request's feature is
  `request.feature`, read from the envelope's `feature`. A body with `item`
  and no `feature` gets 400, "The request's `feature` must be an object with
  `kind`, `key` and `id`." `siren.testing.app_request()` signs a body with
  `feature`, and the fixture copies of Siren's requests follow Siren's
  renamed fixture. Kinds keep their ids.
- The screen-name refusal reads "The kind for Attribution model is
  incentiveResolver." The names come from the kind snapshot below.
- `siren.APP_REQUEST_KINDS` follows the order of Siren's registry, the "New
  feature" chooser's order.

- The request handler's `on()` refuses a kind Siren never sends, at once,
  with a `ValueError` that says what to write: a near miss gets "Did you
  mean distributionResolver?", a screen name such as `attributionModel` gets
  its kind, snake_case gets the camelCase kind, `customEvent` points at
  `events.ingest()`, a webhook event's name points at `on_event()`, and
  `fulfillmentMethod` points at `payoutMethod`. A kind a newer Siren sends
  goes in the handler's new `extra_kinds` option.
- An ask's function that returns `None` is an error. `on_error` gets a
  `SirenError` that says so and nothing is posted. A function that will
  answer by itself with `apps.requests.answer()` returns `answers.later()`.
- `simulate()` returns a failure in the result's new `error` field and never
  raises for one: what the function raised, an ask's function that returned
  nothing, an answer the handler would not post (`ValueError`), and an answer
  Siren would refuse. A tell whose function raises shows its 500. Something
  that is not a request still raises `TypeError`.

### Added

- `request_handler(logger=...)` takes a `logging.Logger`, or anything with
  `debug`, `info`, `warning` and `error`, and is the `siren` logger by
  default. The handler writes one `debug` line per request it handled, such
  as "Siren request handler (distributionResolver split-by-score apr_...):
  200 in 3 ms", for a custom `on_error` too. Refused signatures and slow
  tells are warnings, and every other failure an error.
- A refused signature's default line names what to check, such as
  "Check SIREN_SIGNING_SECRET: it must be this app's signing secret, and it
  changes when the secret is rotated." After five in one minute, the rest
  are counted into one line. `SignatureVerificationError.code` says which
  check failed: `signature_missing`, `signature_malformed`,
  `timestamp_mismatch`, `signature_mismatch` or
  `timestamp_out_of_tolerance`, from the handler and from
  `apps.verify_request()`.
- `siren.AppSlowTellError` and `siren.TELL_TIME_LIMIT_SECONDS` (8). A tell
  whose function runs past Siren's 8-second wait still gets its 200, and
  `on_error` gets the error with a warning that the tell ran twice.
  `simulate()` returns it in `error`.
- `request.test`, true on a test request such as `siren trigger ask` sends
  and false on a real one. The handler refuses a `test` that is not true or
  false with 400. `siren.testing.app_request(..., test=True)` builds one,
  and the README's payout method sample answers one without paying.
- `siren.testing.webhook_event(type, data=None)` signs a webhook event as
  Siren signs one to an app's address, for an `on_event` test.
  `sample_event_data(type)` gives Siren's sample payload for 60 event types,
  generated by scripts/siren_event_samples.py from Siren's
  docs/api/webhooks-events.md (feat/apps at 6d2abd936a), with the same rules
  and values as the Node SDK.
- `siren.testing.app_request_from_record(record, key=None)` turns a stored
  request, as `apps.requests.retrieve(id)` answers it, or a raw body, into a
  signed test input with the same id, kind, mode, `test`, data and
  `answerBy`.
- The README's "Develop on your machine" section teaches
  `siren listen --forward-to` and `siren trigger ask` on a sandbox, which
  arrive with the command-line tool's next release, and says once how to run
  any `siren` command, `siren apps rotate-key` among them, with
  `npx @novatorius/siren`.

- `client.apps.requests.list(status=...)`, `retrieve(id)` and `resend(id)`,
  for `GET /apps/requests`, `GET /apps/requests/{id}` and
  `POST /apps/requests/{id}/resend`, with `siren.APP_REQUEST_STATUSES`,
  `siren.AppRequestRecord` and `siren.AppRequestDetail`, as in the Node SDK.
- A test runs `mypy --strict` on small files that use the SDK and on Siren's
  request fixture, and `mypy` joins the `dev` extra.

- `client.apps.features`: `create()`, `retrieve()`, `update()` and
  `delete()` on `/apps/{app}/features`, with a dashboard session. `delete()`
  returns `{"archived", "feature"}`. `siren.AppFeature`,
  `siren.RemovedAppFeature` and `siren.AppRequestFeature` type them, and
  `siren.ErrorCode.APP_FEATURES_NEED_KEY_AND_ADDRESS` names Siren's 409.
- The SDK ships Siren's kind registry snapshot (Siren docs/api/app-kinds.json)
  as `siren/app_kinds.json`, byte for byte, and reads it as
  `siren.APP_KINDS`. `APP_REQUEST_KINDS` and the screen names `on()` refuses
  come from it. Each `FeatureKind` member's docs give the kind's screen name,
  Siren's own term where it differs, and its description, written by
  `scripts/feature_kinds.py`. A test holds the shipped copy byte for byte
  equal to the file `SIREN_APP_KINDS` names, and skips without it.

- `siren.testing`, to try a request handler without Siren. `app_request(kind,
  key)` builds a request signed as Siren signs one, with the kind's sample
  question, and `sample_data(kind)` gives that question to change. The
  samples ship in the package and a test holds them equal to Siren's request
  fixture. The handler's `simulate(request)` runs every check and the
  function, and returns an `AppSimulation` with the reply's `status` and
  `body` and, for an ask, the `answer`, without posting it.
- `siren.Siren()` reads `SIREN_API_KEY` and `apps.request_handler()` reads
  `SIREN_SIGNING_SECRET` when no value is passed. A value passed in wins, and
  a missing one raises `ValueError` naming the argument and the variable.
- The handler replies 500 to an ask whose signed `answerUrl` is on another
  Siren than the client's `base_url`, and `on_error` gets a `SirenError`
  naming both addresses and the `base_url` to set. Siren Cloud's two hosts,
  api.sirenaffiliates.com and app.sirenaffiliates.com, count as one Siren.
  The answer still only goes to the client's own address.

- Types for the three kinds that had none: `MetricCalculationQuestion`
  and `MetricCalculationAnswer` for the metric calculation
  (`metricCalculation`), `MetricFilterQuestion` and `MetricFilterAnswer`
  for the customer filter for distributors (`metricFilter`), with
  `AppMetricCause` and `AppMetricSale`, and `PayoutMethodQuestion`,
  `PayoutMethodPaidAnswer`, `PayoutMethodFailedAnswer` and `PayoutMethodAnswer` for the payout
  method (`payoutMethod`). Each matches the Node SDK's type of the same name
  and the question Siren builds.
- `events.ingest()` sends an app's custom event with a short-lived app
  token in place of the key, as Siren takes only that app's token at the
  event's address. The client trades the app's key for a token
  (`POST /oauth/token`) the first time, keeps it, gets a new one when fewer
  than two minutes of its 20 are left, and on a 401 gets a new one and sends
  once more. Threads that need a token at once share one request. A key that
  belongs to no app still sends with the key.
- `events.browser_token(event_key)` returns a `BrowserToken` for one custom
  event, which lasts ten minutes and works only from a domain the event
  allows. `events.browser_token_handler(events=...)` returns a
  `BrowserTokenHandler`, the browser token address for the app's server, as
  a plain call, `.wsgi` or `.asgi`. It answers 200 with `token`,
  `expiresIn` and `url`, 400 for an event the page may not ask for or Siren
  refuses, and 502 when Siren can't be reached or refuses the key, and it
  reuses one token per event until fewer than 60 seconds are left.
- `FeatureKind.TRANSACTION_FILTER` (`transactionFilter`), the feature kind a
  program or a distributor picks to let the app say which of a sale's own
  lines count. Siren's catalog calls it a transaction compiler. Siren asks it
  once per sale for each program or distributor that picks it, and a refund
  reuses the stored answer. The types `TransactionFilterQuestion`,
  `AppTransaction`, `AppTransactionLine` and `TransactionFilterAnswer`
  describe the question and its answer, the ids of the lines to include.
  Siren refuses a line id the question lacks with 422 `app_answer_invalid`.
  The README shows the question and a handler that counts only `lineItem`
  lines.
- `FeatureKind.COLLABORATOR_GROUP_STRUCTURE` (`collaboratorGroupStructure`),
  the feature kind a collaborator group picks to let the app give the group's
  shape. Siren asks it once per cascade trigger on the group, for a program
  or a distributor. The types `CollaboratorGroupStructureQuestion`,
  `CollaboratorGroupStructureDistributorQuestion`,
  `CollaboratorGroupStructureProgramQuestion`,
  `AppCollaboratorGroupMember`, `CollaboratorGroupStructureChainAnswer`,
  `CollaboratorGroupStructureTreeAnswer` and `CollaboratorGroupStructureAnswer`
  describe the question and its answer, a chain or a tree. Each member
  carries its `status` at the trigger, which Siren credits it by. Siren
  refuses an answer that leaves out or repeats a member, names one outside the group, or
  makes a cycle with 422 `app_answer_invalid`. The README shows the question
  and both answers.
- `apps.request_handler(signing_secret, *, tolerance_seconds, run_later,
  on_error)` returns an `AppRequestHandler`, one address for everything
  Siren sends an app. Register a function per feature with
  `on(kind, key, fn)`, `on(kind, fn)` or `@answers.on(kind, key)`, and a
  webhook event with `on_event(type, fn)`. Mount it as `answers.flask`,
  `answers.django`, `answers.asgi` or `answers.wsgi`, or call it with the raw
  body and headers for an `AppRequestReply`. It answers 405 for a method
  other than POST, 413 for a body over 1 MiB, 500 when a body parser already
  read the body, 401 for a signature or time `verify_request()` refuses, 400
  for a signed body that is not a request, 200 once a tell's function returns and 500 when it raises,
  200 at once for an ask, and 404 for a kind and key with no function. After
  an ask's 200 it posts what the function returns to
  `POST /apps/requests/{id}/answer`, sent again after a lost connection, 429
  or 5xx, and never after a refusal, which goes to `on_error`. Flask and
  Django are imported only when their face is used.
- `apps.split_pool(pool, weights)` splits a resolver's pool by weight into
  whole steps of the currency, rounded down so the sum never passes the
  pool, and evenly when every weight is 0. It raises `ValueError` for a
  negative or non-finite weight.
- `FeatureKind.INCENTIVE_TYPE` (`incentiveType`), the kind a program picks as
  its incentive type to let the app decide which engagements each sale, lead
  or renewal converts and the pool the program's resolver divides, with the
  types `IncentiveTypeQuestion`, `AppIncentiveTypeEngagement`,
  `IncentiveTypeAwardAnswer`, `IncentiveTypeNoneAnswer` and
  `IncentiveTypeAnswer`, `{"outcome": "award", "units": ..., "pool": ...,
  "engagementIds": [...]}` or `{"outcome": "none"}`. The README shows the
  handler's sample for it.
- `FeatureKind.ENGAGEMENT_CALCULATION` (`engagementCalculation`) and
  `FeatureKind.ENGAGEMENT_FILTER` (`engagementFilter`), the kinds a program's
  engagement type and a program pick to let the app decide who earns each
  engagement and whether it is made, with the types
  `EngagementCalculationQuestion`, `EngagementCalculationAnswer`,
  `EngagementFilterQuestion`, `EngagementFilterAnswer` and
  `AppEngagementCause`. The README shows the answer each takes.
- `FeatureKind.PROGRAM_GROUP_SORTER` (`programGroupSorter`), the kind a
  program group picks as its structure to let the app order its programs for
  each sale or lead, with the types `ProgramGroupSorterQuestion`,
  `ProgramGroupSorterProgram`, `ProgramGroupSorterEngagement` and `ProgramGroupSorterAnswer`,
  `{"outcome": "sorted", "programIds": [...]}`. The README shows the
  handler's sample for it.
- Types for each feature kind's question and answer: `DistributionResolverQuestion`,
  `DistributionResolverAnswer`, `IncentiveResolverQuestion`, `IncentiveResolverAnswer`,
  `PostConversionActionTell`, `PostAllocationActionTell`, and the `AppCurrency`,
  `AppAmount`, `AppDistributionPool`, `AppDistributionMetric` and
  `IncentiveResolverConversion` they hold.
- `FeatureKind` names the kinds of feature an app can add, as an app
  request's `feature["kind"]` carries them, among them the new
  `METRIC_CALCULATION` (`metricCalculation`), `METRIC_FILTER` (`metricFilter`)
  and `INCENTIVE_RESOLVER` (`incentiveResolver`), which a program picks to let
  the app split each sale's reward. The README shows the answer each of the
  three takes, and the 409 `app_request_dropped` Siren answers once it stopped
  waiting, 24 hours after the sale's reward began to wait.
- `apps.verify_request(raw_body, headers, secret, *, tolerance_seconds, now)`
  checks a request Siren signed and sent an app and returns an `AppRequest`.
  It checks `X-Siren-Event`, the `X-Siren-Signature-V2` signature over
  `"{t}."` and the raw body, `X-Siren-Timestamp`, a replay window of 300
  seconds by default (at most 3600), and the version 1 envelope, and raises
  `SignatureVerificationError` with the reason. It reads nothing over the
  network.
- `apps.requests.answer(request_id, answer)` sends an ask's answer once with
  `POST /apps/requests/{id}/answer` and returns an `AppAnswerReceipt`. It
  raises `ValueError` before sending for a bad id, an answer with no text
  `outcome`, and one over 16384 bytes of JSON, and gives Siren's uncoded 401
  the code `ErrorCode.APP_ANSWER_UNAUTHORIZED`.

- `simulate()` checks an ask's answer the way Siren's answer route does,
  each kind's rules ported from Siren with the Siren file and method beside
  each one: the 16384-byte limit as Siren measures it, a share for every
  metric of the question, no share or reward above the pool, whole currency
  steps, ids that were in the question and each only once, every member in a
  chain or tree with no cycle, and the payout and filter text limits. An
  answer Siren would refuse comes back as a `ValidationError` with code
  `app_answer_invalid` (now `ErrorCode.APP_ANSWER_INVALID`) and Siren's own
  words. The live handler still posts and leaves the refusal to Siren.
- `siren.APP_REQUEST_KINDS`, the 13 kinds Siren sends, in Siren's order. A
  test holds it equal to Siren's request fixture.
- `answers.later()`, what an ask's function returns when the app answers by
  itself.

### Changed

- The default `on_error` line names the kind, key and request id, and says
  when Siren sends a failed request again. For an ask, that is when its
  answer window closes, with the time from `answerBy` and how to resend it
  sooner, from Needs attention or with `siren apps resend <id>`. For a tell,
  it is about a minute. A 409 says nothing about a next send, since none
  comes.
- `on()` takes a `FeatureKind` member as well as its text.
- The README opens with "How Siren talks to your app", the same six lines
  as the Node and PHP READMEs. It says an ask's function can run more than
  once and must return the same answer each time, and its reply table gains
  "When your function fails". `AppRequest`'s docstring names `id` as the id
  to write once by, beside the feature's `feature["id"]` and the per-send
  `delivery_id`.
- A signed body that is not an app request of version 1 gets a 400, and a
  `SignatureVerificationError` from `apps.verify_request()`, whose words name
  the first field that fails and its rule, such as "The request's `id` must
  be `apr_` followed by 32 lowercase hex characters."
- The README leads with answering Siren: what the SDK can do, Install with
  the branch install lines while the apps code is unreleased, the first
  sample with no `base_url`, and "Try it without Siren". Recording a sale
  and verifying a webhook move below the app sections.
- The README starts with what the SDK can do, then shows the request
  handler with a sample for Flask, Django, FastAPI or Starlette, any WSGI
  server and a plain call. A section for each feature kind, named as Siren
  Cloud's screens name it, gives the question Siren sends, a function that
  answers it and the answer, followed by the payout method, custom events
  with short-lived tokens, checking and answering by hand, and errors and
  retries. Tests run every sample as written against Siren's own request for
  each kind. The fixture copy of Siren's requests
  (tests/fixtures/apps-feature-contracts/requests.json) now holds all 13
  kinds.

## [0.2.0] - 2026-10-19

This is a minor bump, because the changes below break existing callers. Each
one follows a rule Siren already enforces.

### Breaking

- `api_keys.create` requires the `scopes` keyword and raises `ValueError`
  for an empty list. Code that passed only `label` now raises `TypeError`.
  Siren answers 400 to a key without scopes.
- `webhooks.construct_event` returns the body as `event.data`, and raises
  `SignatureVerificationError` when a bare payload arrives without
  `event_type`. The call from 0.1.0, which passed no headers, used to return
  an event with an empty type for every real delivery, which no handler
  matched. Pass the `X-Siren-Event` header as `event_type`.
- `ListPage.page` and `ListPage.per_page` come from Siren's list body. They
  used to echo the arguments, even though Siren ignored them.

### Fixed

- Reconciliation readers send Siren's `number` and `offset` paging params.
  `page` and `per_page` are turned into them, and `per_page` is capped at
  200. Siren never read `page` or `perPage`, so every call returned the first
  10 rows.
- Reconciliation readers read Siren's `{items, total, page, perPage,
  totalPages}` list body. They used to accept only a bare array and returned
  no rows. `ListPage` gains `total_pages`.
- `webhooks.construct_event` takes `event_type` and `delivery_id`, from the
  `X-Siren-Event` and `X-Siren-Delivery` headers. A plain subscription's body
  is the event payload alone, which now becomes `event.data`. A connected
  site's versioned envelope is unwrapped when its `id` and `type` match the
  two headers, or when neither header is passed, so a custom event's
  payload that holds `version`, `type` and `data` keys stays whole. The SDK
  used to look for `type`, `data` and `deliveryId` in the body, which Siren
  never sends, so real deliveries parsed with an empty type and empty data.
  `WebhookEvent` gains `raw`.
- Errors read `error.context` as `data` when the body has no `data`, and use
  its `type` as `code` when there is no `code`. A failed field check, which
  Siren answers with 400, now exposes its broken rules on
  `BadRequestError.field_errors`, a list of `{field, message, type,
  context}` entries in Siren's order.
- `api_keys.create` documents the scopes Siren issues: event scopes
  (`event:*`, `event:{slug}` or `*`), the ledger read scopes and the
  webhook scopes.
- `ApiKey.created_at` and `WebhookSubscription.created_at` read Siren's
  `dateCreated`. Both gain `property_id`, and `ApiKey` gains `revoked_at`.
- `api_keys.list` and `webhooks.subscriptions.list` take `number` and
  `offset`, because Siren returns at most 200 rows a request.
- `api_keys.create` takes `label` as optional and leaves it out of the
  request when not given, as Siren allows.
- `events.sale` sends a line item that has only `amount`. It used to raise
  `ValueError` without `name`, which Siren does not need. `external_id`
  may be a number, which Siren stores as text.
- `openapi.yaml` lists the credentials each operation takes. It adds `GET /ping`, drops the page size maximum that Siren
  lowers rather than refuses, and types `externalId` as a string or a
  number.

### Added

- An API key reads Siren's ledger and manages webhook subscriptions, as Siren
  Cloud 3.6.259 allows. `conversions`, `transactions`, `obligations` and
  `payouts` take a key that holds the matching ledger scope.
  `webhooks.subscriptions.list` takes a key with
  `webhook-subscriptions:read` or `webhook-subscriptions:manage`, and
  `create` and `delete` take one with `webhook-subscriptions:manage`. Since
  Siren Cloud 3.6.260 that scope also sends a test delivery, and
  `openapi.yaml` lists the route, `POST /webhooks/{id}/test`.
- `siren.ApiKeyScope` names the scopes a key may hold: `ALL_EVENTS`,
  `FULL_ACCESS`, `SALE`, `REFUND`, `SITE_VISITED`,
  `LEDGER_CONVERSIONS_READ`, `LEDGER_TRANSACTIONS_READ`,
  `LEDGER_OBLIGATIONS_READ`, `LEDGER_PAYOUTS_READ`,
  `WEBHOOK_SUBSCRIPTIONS_READ` and `WEBHOOK_SUBSCRIPTIONS_MANAGE`.
  `siren.LEDGER_READ_SCOPES` holds the four ledger scopes, and
  `siren.event_scope(slug)` builds one event scope. `api_keys.create` takes
  them as members or strings. Siren grants the ledger and webhook scopes
  only by their exact names, so `*`, `event:*` and older names such as
  `conversions:read` grant none of them.
- `ApiKeyNotAcceptedError` (401, `api_key_not_accepted`), raised when a
  route takes a session and not an API key, as every `api_keys` route does.
  It subclasses `AuthenticationError`.
- `MissingScopeError` (403, `missing_scope`), with `scope` and `any_of` from
  `error.context`, and `StoreKeyNotAllowedError` (403,
  `store_key_not_allowed`), raised when a key bound to one store asks for
  payouts or webhook subscriptions, with the key's `property_id`. Both
  subclass `PermissionError`. `siren.ErrorCode` holds the three codes.
- The reconciliation readers take `property_id`, which narrows an
  organization-wide read to one store. A key bound to one store reads only
  its own store's rows.
- `EventResult.http_status`, `EventResult.body`, `EventResult.status` and
  `EventResult.queued`. Siren answers a sale and a site visit with what came
  of them, and answers `202` with `status: queued` while an organization's
  processing is paused. When a response has no `X-Siren-OID` header,
  `opportunity_id` is read from the body's `opportunityId`.
- `api_keys.create` takes `expires_at` and `property_id`.
- `WebhookEventType` members for `collaborator.updated`,
  `collaborator.deactivated`, `program.created`, `credit.issued`,
  `credit.redeemed`, `currency.created` and `currency.deleted`, matching the
  catalog Siren serves at `GET /webhooks/events`.

### Changed

- `openapi.yaml` describes the API as Siren serves it: which credential each
  route takes, with the `apiKey` scheme and the scopes each ledger and
  webhook operation needs, the new error codes, the list and error shapes, 400 for failed field checks, the
  ingestion outcome bodies and the queued `202`, the webhook delivery format,
  `GET /webhooks/events`, and the current field names.

## [0.1.0] - 2026-07-11

### Added

- Initial public release of the Siren SDK for Python.
- `siren.Siren` client with configurable base URL, timeout, and retries.
- Event ingestion: `events.sale`, `events.refund`, `events.site_visited`,
  and `events.ingest` for custom event types.
- Webhook verification: `webhooks.construct_event` and
  `webhooks.verify_signature` (constant-time HMAC-SHA256 over the raw body).
- Webhook subscription management: `webhooks.subscriptions.create`, `list`,
  and `delete`.
- API-key management: `api_keys.create`, `list`, and `revoke`.
- Reconciliation readers: `conversions`, `transactions`, `obligations`, and
  `payouts` with paginated `ListPage` results.
- Typed error hierarchy rooted at `SirenError`.
- Ships `py.typed` for full type-checker support.

[Unreleased]: https://github.com/Novatorius/siren-python/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Novatorius/siren-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Novatorius/siren-python/releases/tag/v0.1.0
