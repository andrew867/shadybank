"""Create an explicitly funded, private bank-side test catalogue."""

import argparse
import asyncio
import json
import re
import uuid
from decimal import Decimal

import asyncpg
from virtual_card_identifier import bank_card_identifier


CASES = {
    'full': Decimal('85.20'),
    'mid': Decimal('0.50'),
    'low': Decimal('0.05'),
    'zero': Decimal('0.00'),
    'unlinked': Decimal('0.50'),
}


async def read_catalogue(db, name):
    rows = await db.fetch(
        'SELECT i.case_name,i.lab_reference,i.card_id,i.initial_amount,i.funding_transaction_id,'
        'c.account_id,a.balance,a.available FROM lab_issuer_instruments i '
        'JOIN cards c ON c.merchant_instrument_id=i.card_id JOIN accounts a ON a.id=c.account_id '
        'WHERE i.catalogue=$1 ORDER BY i.case_name', name)
    return [dict(row) for row in rows]


async def seed_catalogue(db, name, funding_account):
    """Caller supplies a bank administrator connection, not a merchant or member session."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name):
        raise ValueError('Choose a stable lab catalogue name')
    if not isinstance(funding_account, int) or funding_account <= 0:
        raise ValueError('Choose an existing funding account')
    async with db.transaction():
        # Serialize restarts and competing administrators for the same catalogue.
        await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', 'lab-issuer:' + name)
        existing = await read_catalogue(db, name)
        if existing:
            if {row['case_name'] for row in existing} != set(CASES):
                raise ValueError('An incomplete existing catalogue requires operator review')
            return existing

        funding = await db.fetchrow(
            'SELECT id,balance,available,special,admin FROM accounts WHERE id=$1 FOR UPDATE', funding_account)
        total = sum(CASES.values())
        if not funding or not (funding['special'] or funding['admin']) or min(funding['balance'], funding['available']) < total:
            raise ValueError('The designated funding account is ineligible or has insufficient funds')

        for case, amount in CASES.items():
            display = f'Lab {name} {case}'[:80]
            account = await db.fetchval('INSERT INTO accounts(name) VALUES($1) RETURNING id', display)
            identifier = bank_card_identifier(account)
            # These are bank-only virtual instruments. No magnetic or physical-device
            # authentication fields are issued by this catalogue.
            card_id = await db.fetchval(
                "INSERT INTO cards(pan,account_id,name,expires,status) VALUES($1,$2,$3,'3801','activated') "
                'RETURNING merchant_instrument_id', identifier, account, display)
            payment = None
            if amount > 0:
                await db.execute('UPDATE accounts SET balance=balance-$1,available=available-$1,last_updated=NOW() WHERE id=$2',
                                 amount, funding_account)
                await db.execute('UPDATE accounts SET balance=balance+$1,available=available+$1,last_updated=NOW() WHERE id=$2',
                                 amount, account)
                payment = await db.fetchval(
                    "INSERT INTO transactions(debit_account,credit_account,amount,type,description) "
                    "VALUES($1,$2,$3,'credit_points','Lab issuer opening balance') RETURNING id",
                    funding_account, account, amount)
            await db.execute(
                'INSERT INTO lab_issuer_instruments(catalogue,case_name,card_id,lab_reference,initial_amount,funding_transaction_id) '
                'VALUES($1,$2,$3,$4,$5,$6)', name, case, card_id, None if case == 'unlinked' else uuid.uuid4(), amount, payment)
        return await read_catalogue(db, name)


async def run(args):
    db = await asyncpg.connect(database='shadybucks')
    try:
        rows = await seed_catalogue(db, args.catalogue, args.funding_account)
        print(json.dumps(rows, default=str, indent=2))
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description='Create or review a private, funded bank-side lab catalogue')
    parser.add_argument('--catalogue', required=True)
    parser.add_argument('--funding-account', required=True, type=int)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
