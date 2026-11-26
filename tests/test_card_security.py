import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from test_member_api import CARD, PIN
from card_inputs import merchant_description, resolve_legacy_card, reject_portal_pin_or_track, redact_track_description
from pin_credentials import hash_pin, is_pin_hash, verify_pin


class CardSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_pins_have_unique_salts_and_no_plaintext_fallback(self):
        first, second = hash_pin(PIN), hash_pin(PIN)
        self.assertNotEqual(first, second)
        self.assertTrue(is_pin_hash(first) and verify_pin(PIN, first) and verify_pin(PIN, second))
        self.assertFalse(verify_pin(PIN, PIN))
        self.assertFalse(verify_pin('000000', first))
        self.assertFalse(is_pin_hash('$argon2id$invalid'))
        with self.assertRaises(ValueError):
            hash_pin('invalid')
        with self.assertRaises(web.HTTPUnauthorized):
            reject_portal_pin_or_track({'pan': CARD, 'pin': PIN})
        reject_portal_pin_or_track({'pan': CARD, 'otp': '000000'})

    async def test_legacy_input_is_transient_and_can_be_disabled(self):
        # Synthetic virtual-card fixture; the merchant receives only the resolved identifier.
        value = f';{CARD}=380110112345678?'
        database = type('FixtureDB', (), {})()
        database.fetchrow = AsyncMock(return_value={'account_id': 1, 'status': 'activated', 'dd2': 12345678})
        with patch.dict(os.environ, {'SHADYBANK_ALLOW_LEGACY_TRACKS': '1'}):
            result = await resolve_legacy_card({'magstripe': value}, database)
            self.assertEqual(result, {'account': 1, 'status': 'activated', 'card': {'pan': CARD}})
            self.assertEqual(database.fetchrow.await_args.args[1:], (CARD, '3801'))
            self.assertNotIn(value, repr(result))
            with self.assertRaises(web.HTTPBadRequest):
                await resolve_legacy_card({'magstripe': value, 'track2': value}, database)
        with patch.dict(os.environ, {'SHADYBANK_ALLOW_LEGACY_TRACKS': '0'}):
            with self.assertRaises(web.HTTPBadRequest):
                await resolve_legacy_card({'magstripe': value}, database)
        self.assertEqual(database.fetchrow.await_count, 1)
        with self.assertRaises(web.HTTPUnauthorized):
            reject_portal_pin_or_track({'magstripe': value})
        with self.assertRaises(web.HTTPBadRequest):
            merchant_description({'description': value})
        self.assertEqual(redact_track_description(value), '')
        self.assertEqual(merchant_description({'description': 'Fixture call payment'}), 'Fixture call payment')
