import asyncio
import os
import secrets
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from merchant_api import MerchantAPI, instrument_digest


KEY = 'synthetic-merchant-service-key-for-tests-only'


class Redis:
    async def eval(self, *args):
        return 1


class MerchantConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_wrong_key_are_not_authorizations(self):
        for key, account, member_key, expected in (('', '', '', 503), (KEY, '1', '', 401), (KEY, '1', KEY, 503)):
            app = web.Application()
            with patch.dict(os.environ, {'SHADYBANK_MERCHANT_SERVICE_KEY': key, 'SHADYBANK_MERCHANT_ACCOUNT_ID': account,
                                        'SHADYBANK_MEMBER_SERVICE_KEY': member_key}):
                MerchantAPI(SimpleNamespace(_app=app))
            async with TestClient(TestServer(app)) as client:
                response = await client.post('/api/merchant/authorize', json={}, headers={'X-Shadybank-Merchant-Key':'wrong'})
                self.assertEqual(response.status, expected)


@unittest.skipUnless(os.getenv('SHADYBANK_TEST_DSN'), 'Dedicated PostgreSQL DSN required')
class MerchantPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.schema = 'merchant_test_' + uuid4().hex
        self.control = await asyncpg.connect(os.environ['SHADYBANK_TEST_DSN'])
        await self.control.execute('CREATE SCHEMA ' + self.schema)
        self.pool = await asyncpg.create_pool(os.environ['SHADYBANK_TEST_DSN'], server_settings={'search_path': self.schema})
        self.addAsyncCleanup(self.cleanup_database)
        await self.pool.execute((Path(__file__).resolve().parents[1] / 'srv/data/postgres.sql').read_text())
        self.merchant = await self.pool.fetchval("INSERT INTO accounts(name,partner) VALUES('Synthetic merchant',true) RETURNING id")
        self.other_merchant = await self.pool.fetchval("INSERT INTO accounts(name,partner) VALUES('Other merchant',true) RETURNING id")
        self.member = await self.pool.fetchval("INSERT INTO accounts(name,balance,available) VALUES('Synthetic member',100,100) RETURNING id")
        card = '0000000000000001'
        card_id = await self.pool.fetchval("INSERT INTO cards(pan,account_id,expires,status) VALUES($1,$2,'3801','activated') RETURNING merchant_instrument_id", card, self.member)
        self.token, self.other_token, self.revoked = (secrets.token_urlsafe(32) for _ in range(3))
        for token, merchant in ((self.token, self.merchant), (self.other_token, self.other_merchant), (self.revoked, self.merchant)):
            await self.pool.execute('INSERT INTO merchant_instruments(token_digest,merchant_account,card_id) VALUES($1,$2,$3)',
                                    instrument_digest(token), merchant, card_id)
        await self.pool.execute('UPDATE merchant_instruments SET revoked_at=NOW() WHERE token_digest=$1', instrument_digest(self.revoked))
        app = web.Application()
        with patch.dict(os.environ, {'SHADYBANK_MERCHANT_SERVICE_KEY': KEY, 'SHADYBANK_MERCHANT_ACCOUNT_ID': str(self.merchant)}):
            MerchantAPI(SimpleNamespace(_app=app, _psql_pool=self.pool, _redis_pool=Redis()))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def cleanup_database(self):
        await self.pool.close()
        await self.control.execute('DROP SCHEMA ' + self.schema + ' CASCADE')
        await self.control.close()

    async def authorize(self, token=None, amount='12.50', request_id=None):
        return await self.client.post('/api/merchant/authorize', json={'instrument_token': token or self.token,
            'amount': amount, 'request_id': str(request_id or uuid4())}, headers={'X-Shadybank-Merchant-Key': KEY})

    async def balances(self):
        row = await self.pool.fetchrow('SELECT balance,available FROM accounts WHERE id=$1', self.member)
        return row['balance'], row['available']

    async def test_concurrent_identical_authorization_reserves_once_and_replays(self):
        identity = uuid4()
        a, b = await asyncio.gather(self.authorize(request_id=identity), self.authorize(request_id=identity))
        self.assertEqual((a.status, b.status), (200, 200))
        first, second = await a.json(), await b.json()
        self.assertEqual(first, second)
        self.assertEqual(set(first), {'status','authorization_code'})
        self.assertEqual(first['status'], 'approved')
        self.assertRegex(first['authorization_code'], r'^\d{6}$')
        self.assertEqual(await self.balances(), (Decimal('100.00'), Decimal('87.50')))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM authorizations'), 1)
        self.assertEqual(await self.pool.fetchval('SELECT pan FROM authorizations'), '************0001')
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'), 0)
        self.assertEqual((await self.authorize(amount='12.51', request_id=identity)).status, 409)
        await self.pool.execute('UPDATE merchant_instruments SET revoked_at=NOW() WHERE token_digest=$1', instrument_digest(self.token))
        self.assertEqual(await (await self.authorize(request_id=identity)).json(), first)
        self.assertEqual(await (await self.authorize()).json(), {'status':'declined'})

    async def test_competing_reservations_and_scope_declines(self):
        a, b = await asyncio.gather(self.authorize(amount='80.00'), self.authorize(amount='80.00'))
        self.assertEqual(sorted([(await a.json())['status'],(await b.json())['status']]), ['approved','declined'])
        for token in (self.other_token, self.revoked, secrets.token_urlsafe(32)):
            self.assertEqual(await (await self.authorize(token)).json(), {'status':'declined'})
        self.assertEqual(await self.balances(), (Decimal('100.00'), Decimal('20.00')))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM authorizations'), 1)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'), 0)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM merchant_authorization_requests WHERE outcome=\'pending\''), 0)
        self.assertEqual((await self.authorize(amount='1.001')).status, 400)
        response = await self.client.post('/api/merchant/authorize', json={'instrument_token':self.token,
            'amount':'1.00','request_id':str(uuid4()),'pan':'0000000000000001'}, headers={'X-Shadybank-Merchant-Key':KEY})
        self.assertEqual(response.status, 400)

    async def test_additive_migration_does_not_modify_existing_instruments(self):
        migration = (Path(__file__).resolve().parents[1] / 'srv/data/merchant-instruments.sql').read_text()
        await self.pool.execute(migration)
        await self.pool.execute(migration)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM merchant_instruments'), 3)
        self.assertEqual((await (await self.authorize()).json())['status'], 'approved')
