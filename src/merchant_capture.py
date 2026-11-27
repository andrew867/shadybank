"""Atomic posting for an existing scoped merchant reservation."""

from decimal import Decimal

from aiohttp import web


async def lock_pending_authorization(db, merchant, code):
    return await db.fetchrow(
        "SELECT id,pan,auth_code,debit_account,credit_account,authorized_debit_amount "
        "FROM authorizations WHERE credit_account=$1 AND auth_code=$2 "
        "AND status='pending' AND expires>NOW() FOR UPDATE", merchant, code)


async def post_locked_capture(db, authorization, amount: Decimal, description=None):
    """Caller owns a database transaction and the pending authorization row lock."""
    held = authorization['authorized_debit_amount']
    debit, credit = authorization['debit_account'], authorization['credit_account']
    if held is None or not Decimal('0') < amount <= held:
        raise web.HTTPForbidden(text='Amount exceeds reservation')
    if debit is None or credit is None or debit == credit:
        raise web.HTTPConflict(text='Reservation cannot be posted')
    # All account changes and the purchase record commit together. An unused portion
    # of the hold is released, not posted or left reserved.
    accounts = await db.fetch('SELECT id FROM accounts WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE', [debit, credit])
    if len(accounts) != 2:
        raise web.HTTPConflict(text='Reservation cannot be posted')
    transitioned = await db.fetchval(
        "UPDATE authorizations SET status='posted' WHERE id=$1 AND status='pending' AND expires>NOW() RETURNING id",
        authorization['id'])
    if transitioned is None:
        raise web.HTTPConflict(text='Reservation is no longer pending')
    posted = await db.fetchval(
        'UPDATE accounts SET balance=balance-$1, available=available+($2-$1), last_updated=NOW() '
        'WHERE id=$3 AND balance >= $1 RETURNING id', amount, held, debit)
    if posted is None:
        raise web.HTTPConflict(text='Reservation cannot be posted')
    await db.execute('UPDATE accounts SET balance=balance+$1, available=available+$1, last_updated=NOW() WHERE id=$2', amount, credit)
    return await db.fetchval(
        "INSERT INTO transactions(debit_account,credit_account,amount,pan,auth_code,type,description) "
        "VALUES($1,$2,$3,$4,$5,'purchase',$6) RETURNING id", debit, credit, amount,
        authorization['pan'], authorization['auth_code'], description)
