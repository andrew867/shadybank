"""Opt-in merchant authorization using bank-issued opaque instrument tokens."""

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from decimal import Decimal

from aiohttp import web
from member_api import active_card, amount_value, ordinary


TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


def instrument_digest(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('Invalid instrument token')
    return hashlib.sha256(token.encode('ascii')).digest()


class MerchantAPI:
    def __init__(self, daemon):
        self.daemon = daemon
        self.key = os.getenv('SHADYBANK_MERCHANT_SERVICE_KEY', '')
        member_key = os.getenv('SHADYBANK_MEMBER_SERVICE_KEY', '')
        if self.key and hmac.compare_digest(self.key.encode(), member_key.encode()):
            self.key = ''
        identity = os.getenv('SHADYBANK_MERCHANT_ACCOUNT_ID', '')
        self.merchant = int(identity) if re.fullmatch(r'[1-9][0-9]{0,9}', identity) and int(identity) <= 2147483647 else None
        daemon._app.add_routes([web.post('/api/merchant/authorize', self.authorize)])

    def service(self, request):
        if len(self.key) < 32 or self.merchant is None:
            raise web.HTTPServiceUnavailable(text='Merchant service unavailable')
        supplied = request.headers.get('X-Shadybank-Merchant-Key', '')
        if not hmac.compare_digest(supplied.encode(), self.key.encode()):
            raise web.HTTPUnauthorized(text='Merchant authentication failed')

    async def limit(self):
        identifier = hmac.new(self.key.encode(), str(self.merchant).encode(), hashlib.sha256).hexdigest()
        result = await self.daemon._redis_pool.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return n",
            1, 'merchant_instrument_rate:' + identifier, 60)
        if int(result) > 120:
            raise web.HTTPTooManyRequests(text='Merchant request limit exceeded')

    @staticmethod
    def result(outcome, code=None):
        if outcome == 'approved':
            return web.json_response({'status': 'approved', 'authorization_code': code})
        if outcome == 'declined':
            return web.json_response({'status': 'declined'})
        raise web.HTTPServiceUnavailable(text='Merchant decision unavailable')

    async def authorize(self, request):
        self.service(request)
        await self.limit()
        if request.content_type != 'application/json' or request.content_length is None or request.content_length > 1024:
            raise web.HTTPBadRequest(text='Invalid authorization request')
        try:
            body = await request.read()
            if len(body) > 1024:
                raise ValueError()
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {'instrument_token', 'amount', 'request_id'}:
                raise ValueError()
            digest = instrument_digest(data['instrument_token'])
            amount = amount_value(data['amount'])
            if amount >= Decimal('50000.00'):
                raise ValueError()
            if not isinstance(data['request_id'], str):
                raise ValueError()
            request_id = uuid.UUID(data['request_id'])
            if str(request_id) != data['request_id'] or request_id.int == 0:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise web.HTTPBadRequest(text='Invalid authorization request') from None

        async with self.daemon._psql_pool.acquire() as db:
            async with db.transaction():
                merchant = await db.fetchrow('SELECT partner,admin,special FROM accounts WHERE id=$1', self.merchant)
                if not merchant or not merchant['partner'] or merchant['admin'] or merchant['special']:
                    raise web.HTTPServiceUnavailable(text='Merchant service unavailable')

                inserted = await db.fetchval(
                    "INSERT INTO merchant_authorization_requests(merchant_account,request_id,token_digest,amount,outcome) "
                    "VALUES($1,$2,$3,$4,'pending') ON CONFLICT DO NOTHING RETURNING request_id",
                    self.merchant, request_id, digest, amount)
                if inserted is None:
                    prior = await db.fetchrow(
                        'SELECT token_digest,amount,outcome,auth_code FROM merchant_authorization_requests '
                        'WHERE merchant_account=$1 AND request_id=$2', self.merchant, request_id)
                    if not prior or not hmac.compare_digest(prior['token_digest'], digest) or prior['amount'] != amount:
                        raise web.HTTPConflict(text='Authorization request identifier already used')
                    return self.result(prior['outcome'], prior['auth_code'])

                card = await db.fetchrow(
                    'SELECT right(c.pan,4) AS last_four,c.account_id,c.status,c.expires,a.partner,a.admin,a.special '
                    'FROM merchant_instruments i JOIN cards c ON c.merchant_instrument_id=i.card_id JOIN accounts a ON a.id=c.account_id '
                    'WHERE i.token_digest=$1 AND i.merchant_account=$2 AND i.revoked_at IS NULL FOR UPDATE OF i,c,a',
                    digest, self.merchant)
                held = None
                if ordinary(card) and active_card(card):
                    held = await db.fetchval('UPDATE accounts SET available=available-$1,last_updated=NOW() '
                                             'WHERE id=$2 AND available >= $1 RETURNING id', amount, card['account_id'])
                if held is None:
                    await db.execute("UPDATE merchant_authorization_requests SET outcome='declined' WHERE merchant_account=$1 AND request_id=$2",
                                     self.merchant, request_id)
                    return self.result('declined')

                # Reserve funds only. The ordinary bank authorization/capture lifecycle
                # owns later settlement; no posted transaction is created here.
                # The legacy authorization field is retained for UI compatibility;
                # the new service only receives and records a masked last-four value.
                masked_identifier = '************' + card['last_four']
                for _ in range(10):
                    code = str(secrets.randbelow(1000000)).zfill(6)
                    stored = await db.fetchval(
                        'INSERT INTO authorizations(pan,auth_code,debit_account,credit_account,authorized_debit_amount) '
                        'VALUES($1,$2,$3,$4,$5) ON CONFLICT (credit_account,auth_code) DO NOTHING RETURNING id',
                        masked_identifier, code, card['account_id'], self.merchant, amount)
                    if stored is not None:
                        break
                else:
                    raise web.HTTPServiceUnavailable(text='Merchant decision unavailable')
                await db.execute("UPDATE merchant_authorization_requests SET outcome='approved',auth_code=$3 WHERE merchant_account=$1 AND request_id=$2",
                                 self.merchant, request_id, code)
                return self.result('approved', code)
