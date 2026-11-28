-- Terminal observations are evidence, not a second purchase writer.
BEGIN;
CREATE TABLE IF NOT EXISTS merchant_terminal_reports (
    merchant_account integer NOT NULL,
    report_id uuid NOT NULL,
    request_id uuid NOT NULL,
    settlement_id uuid NOT NULL,
    amount numeric(8,2) NOT NULL CHECK (amount > 0),
    created_at timestamp NOT NULL DEFAULT NOW(),
    PRIMARY KEY (merchant_account, report_id),
    UNIQUE (merchant_account, request_id),
    UNIQUE (merchant_account, settlement_id),
    CHECK (report_id <> request_id AND report_id <> settlement_id AND request_id <> settlement_id),
    FOREIGN KEY (merchant_account, request_id) REFERENCES merchant_authorization_requests(merchant_account, request_id)
);
COMMIT;
