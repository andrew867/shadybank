-- Apply once to an existing database before enabling the member API.
-- Execute with autocommit (do not wrap the enum addition and subsequent use in a single transaction).
ALTER TYPE trans_type ADD VALUE IF NOT EXISTS 'member_transfer';

-- Enforce hashed PINs for new or changed rows immediately; the operator's
-- migration utility converts existing numeric PIN rows and validates the check.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='secrets'::regclass AND conname='member_pin_hash') THEN
        ALTER TABLE secrets ADD CONSTRAINT member_pin_hash
            CHECK (type <> 'pin' OR (secret IS NOT NULL AND secret LIKE '$argon2%')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='transactions'::regclass AND conname='transaction_description_no_track') THEN
        ALTER TABLE transactions ADD CONSTRAINT transaction_description_no_track
            CHECK (description IS NULL OR description !~* '(B[0-9]{8,19}\^|[0-9]{8,19}=)') NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS member_transfers (
    sender_account integer NOT NULL REFERENCES accounts(id),
    request_id uuid NOT NULL,
    recipient_account integer NOT NULL REFERENCES accounts(id),
    amount numeric(8,2) NOT NULL CHECK (amount > 0),
    transaction_id integer NOT NULL UNIQUE REFERENCES transactions(id),
    created_at timestamp NOT NULL DEFAULT NOW(),
    PRIMARY KEY(sender_account, request_id),
    CHECK (sender_account <> recipient_account)
);
