-- One durable posting per scoped reservation and per settlement identifier.
BEGIN;
CREATE UNIQUE INDEX IF NOT EXISTS transactions_purchase_authorization_once
    ON transactions(credit_account,auth_code) WHERE type='purchase' AND auth_code IS NOT NULL;
CREATE TABLE IF NOT EXISTS merchant_capture_requests (
    merchant_account integer NOT NULL,
    request_id uuid NOT NULL,
    settlement_id uuid NOT NULL,
    amount numeric(8,2) NOT NULL CHECK (amount > 0),
    transaction_id integer NOT NULL UNIQUE REFERENCES transactions(id),
    created_at timestamp NOT NULL DEFAULT NOW(),
    PRIMARY KEY (merchant_account, request_id),
    UNIQUE (merchant_account, settlement_id),
    CHECK (request_id <> settlement_id),
    FOREIGN KEY (merchant_account, request_id) REFERENCES merchant_authorization_requests(merchant_account, request_id)
);
COMMIT;
