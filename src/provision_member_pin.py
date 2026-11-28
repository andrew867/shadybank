"""Assign or rotate an ordinary member PIN from the database operator's console."""

import asyncio
import argparse
import getpass
import uuid

import asyncpg
from member_api import PAN, PIN, active_card, ordinary
from pin_credentials import hash_pin


async def provision_on_connection(db, pin, pan=None, card_id=None):
    if (pan is None) == (card_id is None):
        raise ValueError('Choose one bank instrument identifier')
    async with db.transaction():
        qualifier = 'c.merchant_instrument_id=$1' if card_id is not None else 'c.pan=$1'
        card = await db.fetchrow(
            'SELECT c.account_id,c.status,c.expires,a.partner,a.admin,a.special '
            'FROM cards c JOIN accounts a ON a.id=c.account_id WHERE ' + qualifier + ' FOR UPDATE OF a,c',
            card_id if card_id is not None else pan)
        if not ordinary(card) or not active_card(card):
            raise ValueError('Eligible member card required')
        digest = await asyncio.get_running_loop().run_in_executor(None, hash_pin, pin)
        await db.execute("DELETE FROM secrets WHERE account_id=$1 AND type='pin'",card['account_id'])
        await db.execute("INSERT INTO secrets(account_id,type,secret) VALUES($1,'pin',$2)",card['account_id'],digest)


async def provision(pan, pin, card_id=None):
    db = await asyncpg.connect(database='shadybucks')
    try:
        await provision_on_connection(db, pin, pan, card_id)
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description='Assign or rotate a member PIN from an authorized bank console')
    parser.add_argument('--card-id', type=uuid.UUID)
    args = parser.parse_args()
    pan = None if args.card_id else getpass.getpass('Member card number: ')
    pin = getpass.getpass('New assigned member PIN (4-8 digits): ')
    confirm = getpass.getpass('Confirm PIN: ')
    if (pan is not None and not PAN.fullmatch(pan)) or not PIN.fullmatch(pin) or pin != confirm:
        raise SystemExit('Invalid number, PIN or confirmation.')
    try:
        asyncio.run(provision(pan,pin,args.card_id))
    except ValueError:
        raise SystemExit('Eligible member card required.')
    print('Member PIN assigned.')


if __name__ == '__main__':
    main()
