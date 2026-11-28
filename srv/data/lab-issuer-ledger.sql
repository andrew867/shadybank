-- An optional, administrator-created catalogue of internal lab ledger instruments.
BEGIN;
CREATE TABLE IF NOT EXISTS lab_issuer_instruments (
    catalogue varchar(64) NOT NULL,
    case_name varchar(16) NOT NULL CHECK (case_name IN ('full','mid','low','zero','unlinked')),
    card_id uuid NOT NULL UNIQUE REFERENCES cards(merchant_instrument_id),
    lab_reference uuid UNIQUE,
    initial_amount numeric(8,2) NOT NULL CHECK (initial_amount >= 0),
    funding_transaction_id integer UNIQUE REFERENCES transactions(id),
    created_at timestamp NOT NULL DEFAULT NOW(),
    PRIMARY KEY (catalogue, case_name),
    CHECK ((case_name = 'unlinked') = (lab_reference IS NULL)),
    CHECK ((initial_amount = 0) = (funding_transaction_id IS NULL))
);
COMMIT;
