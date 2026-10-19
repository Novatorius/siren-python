# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The next release is 0.2.0, a minor bump, because the changes below break
existing callers. Each one follows a rule Siren already enforces.

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
- `api_keys.create` documents that Siren issues event scopes only
  (`event:*`, `event:{slug}` or `*`).
- `ApiKey.created_at` and `WebhookSubscription.created_at` read Siren's
  `dateCreated`. Both gain `property_id`, and `ApiKey` gains `revoked_at`.
- `api_keys.list` and `webhooks.subscriptions.list` take `number` and
  `offset`, because Siren returns at most 200 rows a request.
- `api_keys.create` takes `label` as optional and leaves it out of the
  request when not given, as Siren allows.
- `events.sale` sends a line item that has only `amount`. It used to raise
  `ValueError` without `name`, which Siren does not need. `external_id`
  may be a number, which Siren stores as text.
- `openapi.yaml` lists the credentials each operation takes, so the webhook,
  API key and reconciliation routes ask for a dashboard session and not an
  API key. It adds `GET /ping`, drops the page size maximum that Siren
  lowers rather than refuses, and types `externalId` as a string or a
  number.

### Added

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
  route takes, the list and error shapes, 400 for failed field checks, the
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

[Unreleased]: https://github.com/Novatorius/siren-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Novatorius/siren-python/releases/tag/v0.1.0
