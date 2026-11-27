# Scoped merchant instrument authorization

The optional merchant interface authorizes an explicitly provisioned bank instrument without accepting a PAN, track, PIN or member session. It is disabled unless `SHADYBANK_MERCHANT_SERVICE_KEY` contains at least 32 characters and `SHADYBANK_MERCHANT_ACCOUNT_ID` identifies a non-administrative partner account. The key must differ from the member API key. Keep both in private configuration and use TLS on any non-loopback service connection. Legacy card inputs are independent of this interface.

The merchant endpoint runs in its own optional Compose service and does not extend the legacy portal process or receive the member service key. On an existing bank database, apply `srv/data/merchant-instruments.sql` before enabling this service. Fresh initialization already includes the tables. Set the merchant account and distinct key in `.env`, then start `docker compose --profile merchant up -d merchant-endpoint`; its host port defaults to loopback 8022. From an authorized bank console, run `python src/provision_merchant_instrument.py` in the merchant container, enter the merchant account and active bank card identifier. The command displays a high-entropy token once. Store it in the trusted client credential store, not in source, logs or audit text. The bank stores only its digest and the scoped internal instrument mapping. Revoking an instrument prevents new authorizations, but does not alter previously committed idempotent results.

`POST /api/merchant/authorize`, JSON, header `X-Shadybank-Merchant-Key`:

| Field | Meaning |
| --- | --- |
| `instrument_token` | Private bank-issued opaque token, 43 URL-safe characters |
| `amount` | Positive decimal string with two fractional digits, below 50000 |
| `request_id` | Canonical nonzero UUID; reuse for the same request after timeouts |

An approved reservation returns HTTP 200 with `status: approved` and the six-digit `authorization_code`. An unavailable, revoked, expired, blocked or insufficient instrument returns HTTP 200 with `status: declined` and no identifier. A replay with the same merchant, request ID, instrument and amount returns the identical original decision. Reusing a request ID for different details returns 409. Invalid requests, unavailable services and authentication failures are not approvals. Instrument mappings use an internal card UUID, not a second copy of the bank card identifier; the authorization record contains only the masked last four digits.

Funds are reserved atomically with the idempotency record. Posted balances and transaction history do not change until a separate capture. A lost HTTP response may have created a reservation; retry the same ID rather than creating another. The integration must reconcile or release any abandoned authorization according to the bank's verified workflow.

## Scoped capture

Capture is separately disabled by default. On an existing bank database, first apply `srv/data/merchant-capture.sql` after `merchant-instruments.sql`. It adds a durable receipt table and a unique purchase constraint without changing previous instruments, reservations or purchases. Fresh initialization includes both. If the constraint identifies existing duplicate purchases, the migration fails; reconcile the existing ledger before retrying rather than deleting records. After independently qualifying the client, set `SHADYBANK_MERCHANT_CAPTURE_ENABLED=1` for the merchant endpoint only.

`POST /api/merchant/capture`, JSON, the same scoped merchant key header:

| Field | Meaning |
| --- | --- |
| `request_id` | Original approved authorization UUID |
| `settlement_id` | Independent canonical nonzero UUID for the reviewed settlement, retained across every retry |
| `amount` | Positive fixed-point amount with two fractional digits, at most the reserved amount |

The response is HTTP 200 with `status: posted` and the supplied `settlement_id`. Retrying the identical request returns the identical receipt even after the original hold is no longer pending. A changed settlement identifier or amount conflicts, as does using one settlement identifier for another reservation. Unknown or declined reservations are not captures. Expired, voided or otherwise no-longer-pending reservations cannot be posted through this route. A partial capture releases the unused part of the reservation. Both account balances, the purchase and the durable receipt commit together. The database enforces one purchase per merchant authorization across purchase writers; the existing manual endpoint is not changed by this interface.

Clients must not treat an authorization as a purchase. A terminal integration must independently match an actual charged call record to the approved reservation, reject missing, ambiguous, zero or higher-than-reserved charges, and retain the same settlement identifier through any uncertain transport outcome. The capture endpoint receives no card identifier, track, PIN, address, phone number or call contents. It records the previously masked identifier and a fixed description, not client-supplied transaction text. Reversal and abandoned-reservation release remain separate operations. Do not enable capture merely to test a reader or authorization.

The disposable PostgreSQL tests verify full and partial capture, exact receipt replay under concurrency, distinct reservation and settlement constraints, rejection of invalid or expired states, the database purchase invariant and repeatable migration. This is bank service evidence, not terminal or physical payment acceptance.

Tests use dedicated synthetic instruments and a disposable PostgreSQL schema, including competing reservations and identical retries. No actual payment-network credential or protocol is required.
