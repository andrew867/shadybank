-- Optional scoped merchant instrument tokens and durable authorization idempotency.
ALTER TABLE cards ADD COLUMN IF NOT EXISTS merchant_instrument_id uuid DEFAULT gen_random_uuid();
UPDATE cards SET merchant_instrument_id = gen_random_uuid() WHERE merchant_instrument_id IS NULL;
ALTER TABLE cards ALTER COLUMN merchant_instrument_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cards_merchant_instrument_id ON cards(merchant_instrument_id);

CREATE TABLE IF NOT EXISTS merchant_instruments (
    token_digest bytea PRIMARY KEY CHECK (octet_length(token_digest) = 32),
    merchant_account integer NOT NULL REFERENCES accounts(id),
    card_id uuid NOT NULL REFERENCES cards(merchant_instrument_id),
    created_at timestamp NOT NULL DEFAULT NOW(),
    revoked_at timestamp
);

CREATE TABLE IF NOT EXISTS merchant_authorization_requests (
    merchant_account integer NOT NULL REFERENCES accounts(id),
    request_id uuid NOT NULL,
    token_digest bytea NOT NULL CHECK (octet_length(token_digest) = 32),
    amount numeric(8,2) NOT NULL CHECK (amount > 0),
    outcome text NOT NULL CHECK (outcome IN ('pending','approved','declined')),
    auth_code varchar(6),
    created_at timestamp NOT NULL DEFAULT NOW(),
    PRIMARY KEY (merchant_account, request_id),
    CHECK ((outcome = 'approved') = (auth_code IS NOT NULL)),
    FOREIGN KEY (merchant_account, auth_code) REFERENCES authorizations(credit_account, auth_code)
);
