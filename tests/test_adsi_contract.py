"""Exercise the actual ADSI client and bank HTTP handlers over verified local TLS."""

import os
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from aiohttp import web
from test_member_api import CARD, RECIPIENT, PIN, KEY, Database, Redis
from member_api import MemberAPI


class ContractDatabase(Database):
    async def fetch(self, sql, *args):
        if 'FROM transactions' in sql:
            return [dict(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), amount=amount,
                         debit_account=debit, type='member_transfer', description='Synthetic transfer',
                         credit_name='Fixture recipient', debit_name='Fixture sender')
                    for debit, credit, amount in self.ledger if args[0] in (debit, credit)]
        if 'FROM authorizations' in sql:
            return [dict(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), expires=datetime(2026, 1, 2, tzinfo=timezone.utc),
                         authorized_debit_amount=Decimal('2.50'), debit_account=args[0],
                         credit_name='Fixture merchant', debit_name='Fixture sender')]
        return await super().fetch(sql, *args)


def tls_contexts(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Local contract fixture')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1'))]), False)
                   .sign(key, hashes.SHA256()))
    cert = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = Path(directory) / 'fixture.crt', Path(directory) / 'fixture.key'
    cert_path.write_bytes(cert)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(cert_path), str(key_path))
    client = ssl.create_default_context(cadata=cert.decode('ascii'))
    return server, client


@unittest.skipUnless(os.getenv('ADSI_CONTRACT_CLIENT_ROOT'), 'Supply a checked-out CoinLine-ADSI repository')
class ADSIContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_client_and_member_endpoints(self):
        import httpx
        root = Path(os.environ['ADSI_CONTRACT_CLIENT_ROOT']).resolve()
        sys.path.insert(0, str(root / 'service'))
        from coinline_adsi.shadybank import LiveBank, Rejected

        daemon = SimpleNamespace(_app=web.Application(), _psql_pool=ContractDatabase(), _redis_pool=Redis())
        with patch.dict(os.environ, {'SHADYBANK_MEMBER_SERVICE_KEY': KEY}):
            MemberAPI(daemon)
        with tempfile.TemporaryDirectory() as directory:
            server_tls, client_tls = tls_contexts(directory)
            runner = web.AppRunner(daemon._app, access_log=None)
            await runner.setup()
            try:
                site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=server_tls)
                await site.start()
                url = f'https://127.0.0.1:{runner.addresses[0][1]}'
                async with httpx.AsyncClient(verify=client_tls, trust_env=False) as client:
                    bank = LiveBank(client, url, KEY)
                    errors = []
                    for card in (CARD, '9000000000000099'):
                        with self.assertRaises(Rejected) as rejected:
                            await bank.login(card, '000000')
                        errors.append(str(rejected.exception))
                    self.assertEqual(errors[0], errors[1])
                    session = await bank.login(CARD, PIN)
                    self.assertEqual(await bank.balance(session), (Decimal('100.00'), Decimal('100.00')))
                    self.assertEqual((await bank.activity(session, holds=True))[0]['amount'], Decimal('-2.50'))
                    identifier = uuid4()
                    receipt = await bank.transfer(session, RECIPIENT, Decimal('12.50'), identifier)
                    self.assertEqual(await bank.transfer(session, RECIPIENT, Decimal('12.50'), identifier), receipt)
                    self.assertEqual(await bank.balance(session), (Decimal('87.50'), Decimal('87.50')))
                    self.assertEqual((await bank.activity(session))[0]['amount'], Decimal('-12.50'))
                    await bank.logout(session)
                    with self.assertRaises(Rejected):
                        await bank.balance(session)
                    receiver = await bank.login(RECIPIENT, PIN)
                    self.assertEqual(await bank.balance(receiver), (Decimal('17.50'), Decimal('17.50')))
                    self.assertEqual((await bank.activity(receiver))[0]['amount'], Decimal('12.50'))
                    await bank.logout(receiver)
                    self.assertEqual(len(daemon._psql_pool.ledger), 1)
            finally:
                await runner.cleanup()
