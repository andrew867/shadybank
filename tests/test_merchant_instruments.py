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
from merchant_capture import lock_pending_authorization, post_locked_capture


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

    async def test_capture_requires_independent_opt_in_and_merchant_authentication(self):
        for enabled, key, expected in (('0', KEY, 503), ('1', 'wrong', 401)):
            app = web.Application()
            with patch.dict(os.environ, {'SHADYBANK_MERCHANT_SERVICE_KEY': KEY, 'SHADYBANK_MERCHANT_ACCOUNT_ID': '1',
                                        'SHADYBANK_MERCHANT_CAPTURE_ENABLED': enabled, 'SHADYBANK_MEMBER_SERVICE_KEY': ''}):
                MerchantAPI(SimpleNamespace(_app=app))
            async with TestClient(TestServer(app)) as client:
                response = await client.post('/api/merchant/capture', json={}, headers={'X-Shadybank-Merchant-Key':key})
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
        with patch.dict(os.environ, {'SHADYBANK_MERCHANT_SERVICE_KEY': KEY, 'SHADYBANK_MERCHANT_ACCOUNT_ID': str(self.merchant),
                                    'SHADYBANK_MERCHANT_CAPTURE_ENABLED': '1', 'SHADYBANK_MEMBER_SERVICE_KEY': ''}):
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

    async def capture(self, request_id, settlement_id, amount='12.50'):
        return await self.client.post('/api/merchant/capture', json={'request_id': str(request_id),
            'settlement_id': str(settlement_id), 'amount': amount}, headers={'X-Shadybank-Merchant-Key': KEY})

    async def merchant_balances(self):
        row = await self.pool.fetchrow('SELECT balance,available FROM accounts WHERE id=$1', self.merchant)
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

    async def test_concurrent_capture_posts_once_and_replays_exact_receipt(self):
        request_id, settlement_id = uuid4(), uuid4()
        self.assertEqual((await (await self.authorize(request_id=request_id)).json())['status'], 'approved')
        self.assertEqual(await self.balances(), (Decimal('100.00'), Decimal('87.50')))
        a,b = await asyncio.gather(self.capture(request_id, settlement_id), self.capture(request_id, settlement_id))
        self.assertEqual((a.status,b.status),(200,200))
        expected={'status':'posted','settlement_id':str(settlement_id)}
        self.assertEqual(await a.json(), expected)
        self.assertEqual(await b.json(), expected)
        self.assertEqual(await self.balances(), (Decimal('87.50'), Decimal('87.50')))
        self.assertEqual(await self.merchant_balances(), (Decimal('12.50'), Decimal('12.50')))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM merchant_capture_requests'),1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM transactions WHERE type='purchase' AND amount=12.50"),1)
        self.assertEqual(await self.pool.fetchval('SELECT pan FROM transactions'),'************0001')
        self.assertEqual((await self.capture(request_id,uuid4())).status,409)
        self.assertEqual((await self.capture(request_id,settlement_id,'12.49')).status,409)
        self.assertEqual(await self.balances(), (Decimal('87.50'), Decimal('87.50')))

    async def test_partial_capture_releases_unused_reservation_and_rejects_invalid_amount(self):
        request_id, settlement_id = uuid4(), uuid4()
        await self.authorize(request_id=request_id)
        self.assertEqual((await self.capture(request_id,settlement_id,'12.51')).status,403)
        self.assertEqual((await self.capture(request_id,settlement_id,'0.00')).status,400)
        self.assertEqual((await self.capture(request_id,settlement_id,'1.001')).status,400)
        self.assertEqual((await self.capture(request_id,request_id)).status,400)
        self.assertEqual(await self.balances(),(Decimal('100.00'),Decimal('87.50')))
        self.assertEqual((await self.capture(request_id,settlement_id,'5.00')).status,200)
        self.assertEqual(await self.balances(),(Decimal('95.00'),Decimal('95.00')))
        self.assertEqual(await self.merchant_balances(),(Decimal('5.00'),Decimal('5.00')))

    async def test_settlement_identifier_cannot_post_two_reservations(self):
        first,second,settlement=uuid4(),uuid4(),uuid4()
        await self.authorize(request_id=first,amount='5.00')
        await self.authorize(request_id=second,amount='5.00')
        responses=await asyncio.gather(self.capture(first,settlement,'5.00'),self.capture(second,settlement,'5.00'))
        self.assertEqual(sorted(r.status for r in responses),[200,409])
        self.assertEqual(await self.balances(),(Decimal('95.00'),Decimal('90.00')))
        self.assertEqual(await self.merchant_balances(),(Decimal('5.00'),Decimal('5.00')))
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM authorizations WHERE status='pending'"),1)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'),1)

    async def test_expired_voided_and_declined_reservations_are_not_posted(self):
        expired,voided,declined=uuid4(),uuid4(),uuid4()
        a=await (await self.authorize(request_id=expired,amount='5.00')).json()
        b=await (await self.authorize(request_id=voided,amount='5.00')).json()
        await self.authorize(request_id=declined,amount='200.00')
        await self.pool.execute("UPDATE authorizations SET expires=NOW()-INTERVAL '1 minute' WHERE auth_code=$1",a['authorization_code'])
        async with self.pool.acquire() as db:
            async with db.transaction():
                released=await db.fetchrow("UPDATE authorizations SET status='voided' WHERE auth_code=$1 AND status='pending' RETURNING debit_account,authorized_debit_amount",b['authorization_code'])
                await db.execute('UPDATE accounts SET available=available+$1 WHERE id=$2',released['authorized_debit_amount'],released['debit_account'])
        for request_id,expected in ((expired,409),(voided,409),(declined,404),(uuid4(),404)):
            self.assertEqual((await self.capture(request_id,uuid4(),'5.00')).status,expected)
        self.assertEqual(await self.balances(),(Decimal('100.00'),Decimal('95.00')))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'),0)

    async def test_existing_posted_reservation_is_not_captured_again(self):
        request_id=uuid4()
        decision=await (await self.authorize(request_id=request_id,amount='5.00')).json()
        async with self.pool.acquire() as db:
            async with db.transaction():
                row=await lock_pending_authorization(db,self.merchant,decision['authorization_code'])
                self.assertIsNotNone(row)
                await post_locked_capture(db,row,Decimal('5.00'),'Manual fixture capture')
        self.assertEqual((await self.capture(request_id,uuid4(),'5.00')).status,409)
        self.assertEqual(await self.balances(),(Decimal('95.00'),Decimal('95.00')))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'),1)

    async def test_database_enforces_one_purchase_per_merchant_authorization(self):
        import asyncpg
        identity=uuid4()
        approved=await (await self.authorize(request_id=identity,amount='5.00')).json()
        self.assertEqual((await self.capture(identity,uuid4(),'5.00')).status,200)
        with self.assertRaises(asyncpg.UniqueViolationError):
            await self.pool.execute("INSERT INTO transactions(debit_account,credit_account,amount,auth_code,type) "
                "VALUES($1,$2,5.00,$3,'purchase')",self.member,self.merchant,approved['authorization_code'])
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM transactions'),1)
        self.assertEqual(await self.balances(),(Decimal('95.00'),Decimal('95.00')))
        self.assertEqual(await self.merchant_balances(),(Decimal('5.00'),Decimal('5.00')))

    async def test_capture_migration_keeps_prior_reservations_and_is_repeatable(self):
        request_id=uuid4()
        await self.authorize(request_id=request_id,amount='5.00')
        # This is the unique disposable fixture schema, with no capture receipts yet.
        await self.pool.execute('DROP TABLE merchant_capture_requests; DROP INDEX transactions_purchase_authorization_once')
        migration=(Path(__file__).resolve().parents[1]/'srv/data/merchant-capture.sql').read_text()
        await self.pool.execute(migration)
        await self.pool.execute(migration)
        self.assertEqual(await self.balances(),(Decimal('100.00'),Decimal('95.00')))
        self.assertEqual((await self.capture(request_id,uuid4(),'5.00')).status,200)
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM merchant_capture_requests'),1)

    async def test_capture_is_scoped_to_the_same_merchant(self):
        app=web.Application()
        other_key='independent-synthetic-merchant-service-key'
        with patch.dict(os.environ,{'SHADYBANK_MERCHANT_SERVICE_KEY':other_key,
            'SHADYBANK_MERCHANT_ACCOUNT_ID':str(self.other_merchant),'SHADYBANK_MERCHANT_CAPTURE_ENABLED':'1',
            'SHADYBANK_MEMBER_SERVICE_KEY':''}):
            MerchantAPI(SimpleNamespace(_app=app,_psql_pool=self.pool,_redis_pool=Redis()))
        request_id,settlement_id=uuid4(),uuid4()
        async with TestClient(TestServer(app)) as other:
            headers={'X-Shadybank-Merchant-Key':other_key}
            approved=await other.post('/api/merchant/authorize',json={'instrument_token':self.other_token,
                'request_id':str(request_id),'amount':'5.00'},headers=headers)
            self.assertEqual((await approved.json())['status'],'approved')
            self.assertEqual((await self.capture(request_id,settlement_id,'5.00')).status,404)
            result=await other.post('/api/merchant/capture',json={'request_id':str(request_id),
                'settlement_id':str(settlement_id),'amount':'5.00'},headers=headers)
            self.assertEqual(result.status,200)
        self.assertEqual(await self.balances(),(Decimal('95.00'),Decimal('95.00')))
        self.assertEqual(await self.merchant_balances(),(Decimal('0.00'),Decimal('0.00')))
        self.assertEqual(await self.pool.fetchval('SELECT balance FROM accounts WHERE id=$1',self.other_merchant),Decimal('5.00'))
        self.assertEqual(await self.pool.fetchval('SELECT count(*) FROM merchant_capture_requests'),1)
