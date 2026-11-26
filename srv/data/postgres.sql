create table customers (id SERIAL PRIMARY KEY, shadytel_customer_id int, beerocracy_id int, name varchar(80), created_at 
timestamp not null default NOW(), last_updated timestamp);

create table accounts (id SERIAL PRIMARY KEY, name varchar(80), balance numeric(8,2) not null default 0.00, 
available numeric(8,2) not null default 0.00, customer_id int, created_at  timestamp not null default NOW(),
last_updated timestamp, partner bool not null default false, admin bool not null default false,
special bool not null default false, constraint fk_customer_id foreign key(customer_id) references customers(id));

create type card_status as enum ('unallocated', 'issued', 'activated', 'blocked', 'lost', 'stolen');

create table cards (pan varchar(19) NOT NULL PRIMARY KEY, account_id integer, name varchar(80), expires char(4) not null, 
dd1 varchar(31), dd2 numeric(31), status card_status, constraint fk_account_id foreign key (account_id) references accounts(id));

create type secrets_type as enum ('pin', 'password', 'totp', 'webauthn', 'shopt');

create table secrets (id serial primary key, account_id int, type secrets_type, secret text, created_at timestamp 
not null default NOW(), last_used timestamp, constraint fk_account_id foreign key (account_id) references accounts(id),
constraint member_pin_hash check (type <> 'pin' or (secret is not null and secret like '$argon2%')));

create type trans_type as enum ('credit_points', 'purchase', 'refund', 'member_transfer');

create table transactions (id serial primary key, timestamp timestamp not null default NOW(), debit_account integer not null, credit_account 
integer not null, amount numeric(8,2) not null, pan varchar(19), auth_code varchar(6), type trans_type not null, related_transaction int,
description text, constraint transaction_description_no_track check (description is null or description !~* '(B[0-9]{8,19}\^|[0-9]{8,19}=)'),
constraint fk_debit_aid foreign key (debit_account) references accounts(id),
constraint fk_credit_aid foreign key (credit_account) references accounts(id));

create table member_transfers (
    sender_account integer not null references accounts(id),
    request_id uuid not null,
    recipient_account integer not null references accounts(id),
    amount numeric(8,2) not null check (amount > 0),
    transaction_id integer not null unique references transactions(id),
    created_at timestamp not null default NOW(),
    primary key(sender_account, request_id),
    check (sender_account <> recipient_account)
);

create type auth_status as enum ('pending', 'posted', 'reversed', 'voided', 'expired');

create table authorizations (id serial primary key, pan varchar(19) not null, auth_code varchar(6) not null, 
debit_account int, credit_account int, authorized_debit_amount numeric(8,2), timestamp timestamp not null default NOW(), 
expires timestamp default NOW() + INTERVAL '24 hours', status auth_status not null default 'pending',
constraint fk_debit_aid foreign key (debit_account) references accounts(id),
constraint fk_credit_aid foreign key (credit_account) references accounts(id),
unique(credit_account, auth_code));

create table tokens (token char(16) primary key, account_id int, created_at timestamp not null default NOW(),
expires timestamp not null default NOW() + INTERVAL '72 hours', can_initiate_transaction bool not null default true,
constraint fk_account_id foreign key (account_id) references accounts(id));

create table nfc_keys (uid char(14) primary key, des_key1 char(16) not null, des_key2 char(16) not null,
aes_key char(32) not null, created_at timestamp not null default NOW());

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
