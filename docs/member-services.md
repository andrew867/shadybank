# Member services API

The member interface supports a telephone client or other trusted service without conferring merchant, partner or administrative capabilities. It is distinct from the existing merchant endpoints, and disabled unless `SHADYBANK_MEMBER_SERVICE_KEY` contains an operator-supplied service credential of at least 32 characters. Deploy behind an authenticated, TLS-protected service connection; never expose the service credential to a caller. Ordinary members authenticate using their own activated, unexpired numeric ShadyBucks card and assigned member PIN, not a payment-network card or PIN.

## Provisioning

For a fresh database, copy `.env.example` to ignored `.env` and assign a strong, unique `SHADYBANK_DB_PASSWORD` before running Compose. The API and PostgreSQL receive the same value. For an existing database volume, changing `.env` alone does not rotate the database user's password; coordinate an operator-authorized role password rotation and update `.env` together. The previously committed password must not be reused. The initialization SQL includes the member transfer ledger and hashed PIN constraint.

For an already initialized database, apply `srv/data/member-services.sql` with an operator-authorized PostgreSQL connection before enabling the member service. Run `python src/migrate_member_pins.py` in the API container to convert explicit legacy numeric `pin` records to salted Argon2 hashes and validate the schema check. If invalid PIN records are reported, assign replacement PINs using the provisioning utility before repeating the conversion. The migration never interprets `password` records as PINs. Set `SHADYBANK_MEMBER_SERVICE_KEY` in the ignored `.env`. Do not commit either credential.

The database operator runs `python src/provision_member_pin.py` in the API container with its authorized database connection and a terminal capable of hidden input. Assign the member's 4 to 8 digit numeric PIN; it is stored as a salted Argon2 hash in the existing `pin` secret type, separately from web passwords and OTP secrets. Plaintext and malformed hashes are rejected during login. Rotating the PIN deauthorizes old member sessions. The legacy `/api/login` no longer accepts PIN or raw magnetic-card input; PINs produce only scoped member tokens through `/api/member/login`, not web or merchant credentials.

## HTTP contract

All routes require `X-Shadybank-Member-Key`. After login they additionally require `Authorization: Bearer <member token>`. POST payloads are URL-encoded forms. Responses are JSON except the empty logout response. Member tokens expire after five minutes and only authorize the member API, not existing merchant or administrative routes. No card number, PIN or full recipient identifier is returned in JSON.

| Method and path | Inputs | Response |
| --- | --- | --- |
| POST `/api/member/login` | `pan`, `pin` | 201 `token`, `expires_in` |
| POST `/api/member/logout` | Member token | 204 |
| GET `/api/member/balance` | Member token | `balance`, `available` as decimal strings |
| GET `/api/member/transactions` | Member token | Up to 10 posted transactions with `timestamp`, `amount`, `direction`, `type`, `counterparty`, `description` |
| GET `/api/member/authorizations` | Member token | Up to 10 pending, unexpired holds with `timestamp`, `expires`, `amount`, `direction`, `counterparty` |
| POST `/api/member/transfer` | `recipient_pan`, `amount` (positive fixed two decimals), `request_id` (UUID) | 201 `status: posted`, `transaction_id`, `amount`; exact repeat returns 200 with the same receipt |

Authentication failure responses are generic and account/card attempts have a bounded fixed-window rate limit, in addition to a service-wide limit. Transfers only debit the authenticated ordinary member, require both eligible cards and sufficient posted and available balances, reject self-transfer and privileged accounts, and lock both accounts in deterministic order. Debit, credit, journal entry and idempotency receipt commit in one database transaction. Reusing a request ID with different transfer details is rejected. If an HTTP mutation response is lost, a client must not invent a success or automatically generate a new request; it may inspect activity or retry the identical request identifier where appropriate.

## Merchant card input

The existing merchant authorize and credit interfaces can retain compatibility with legacy virtual ShadyBucks magnetic-card readers. They validate submitted tracks in request memory and discard the parsed track fields after resolving the account; ledger operations receive only the existing card identifier. Never send real payment-network cards to this service. `SHADYBANK_ALLOW_LEGACY_TRACKS=0` rejects legacy magnetic-card requests on merchant paths. Token and OTP paths remain available. Card issuance is a separate administrative operation and retains the existing structured virtual-card credential fields, not a saved raw track string. Transaction descriptions containing recognizable track data are rejected by the API and a database check. Older such descriptions, if present, are suppressed in transaction responses and require an operator review; the migration does not rewrite historical ledger entries. Access logs record status and duration, not request URLs or bodies, and the administrative issuance response is marked no-store. Configure any reverse proxy without request-body logging as well.

## Testing

`python -m unittest discover -s tests -v` exercises the member API and card input guards using synthetic data. Set `SHADYBANK_TEST_DSN` to a dedicated disposable PostgreSQL test database for real database concurrency and PIN migration tests. They create and remove uniquely named test schemas, verify identical concurrent requests post once and verify competing requests cannot overspend. To exercise the actual CoinLine ADSI live client against the bank's real HTTP routes over verified local TLS, set `ADSI_CONTRACT_CLIENT_ROOT` to the checked-out CoinLine-ADSI repository and install its service requirements. The contract test runs in CI against its current `main`; no real accounts or production balances are used.
