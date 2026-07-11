# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
