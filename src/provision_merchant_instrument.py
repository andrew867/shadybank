"""Provision a scoped, opaque instrument token from an authorized bank console."""

import asyncio
import getpass
import secrets

import asyncpg
from member_api import PAN, active_card, ordinary
from merchant_api import instrument_digest


async def provision(merchant, card_identifier):
    db = await asyncpg.connect(database='shadybucks')
    try:
        async with db.transaction():
            account = await db.fetchrow('SELECT partner,admin,special FROM accounts WHERE id=$1 FOR UPDATE', merchant)
            card = await db.fetchrow('SELECT c.merchant_instrument_id,c.status,c.expires,a.partner,a.admin,a.special '
                                     'FROM cards c JOIN accounts a ON c.account_id=a.id WHERE c.pan=$1 FOR UPDATE OF c,a', card_identifier)
            if not account or not account['partner'] or account['admin'] or account['special'] or not ordinary(card) or not active_card(card):
                raise ValueError('Eligible merchant and active ordinary card required')
            token = secrets.token_urlsafe(32)
            await db.execute('INSERT INTO merchant_instruments(token_digest,merchant_account,card_id) VALUES($1,$2,$3)',
                             instrument_digest(token), merchant, card['merchant_instrument_id'])
            return token
    finally:
        await db.close()


def main():
    merchant = input('Authorized merchant account ID: ')
    card = getpass.getpass('Bank card identifier: ')
    if not merchant.isdecimal() or not 0 < int(merchant) <= 2147483647 or not PAN.fullmatch(card):
        raise SystemExit('Invalid account or card identifier.')
    try:
        token = asyncio.run(provision(int(merchant), card))
    except ValueError:
        raise SystemExit('Eligible merchant and active ordinary card required.')
    print('Private instrument token (shown once): ' + token)


if __name__ == '__main__':
    main()
