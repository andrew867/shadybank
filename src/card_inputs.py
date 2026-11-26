"""Shared guard for ledger descriptions and legacy magnetic-card inputs."""

import re
import os

from aiohttp import web


# Match card-track-shaped text even when a reader omits the surrounding sentinels.
TRACK_TEXT = re.compile(r'(?:B[0-9]{8,19}\^|[0-9]{8,19}=)', re.ASCII | re.IGNORECASE)
TRACK_FIELDS = ('magstripe', 'track1', 'track2')
TRACK1 = re.compile(r'(?a)%B(?P<pan>\d{8,19})\^(?P<name>.*)\^(?P<exp>\d{4})(?P<svc>\d{3})(?P<dd1>.*?)\?')
TRACK2 = re.compile(r'(?a);(?P<pan>\d{8,19})=(?P<exp>\d{4})(?P<svc>\d{3})(?P<dd2>.*?)\?')


def has_track_input(args):
    return any(args.get(field) for field in TRACK_FIELDS)


def reject_portal_pin_or_track(args):
    # These inputs must never result in a long-lived web or merchant token.
    if 'pin' in args or has_track_input(args):
        raise web.HTTPUnauthorized()


def track_text(args):
    fields = [field for field in TRACK_FIELDS if args.get(field)]
    if len(fields) != 1:
        raise web.HTTPBadRequest(text='Invalid card input')
    field = fields[0]
    value = args[field]
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise web.HTTPBadRequest(text='Invalid card input')
    return field, value


def merchant_description(args):
    value = args.get('description')
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 512 or TRACK_TEXT.search(value):
        raise web.HTTPBadRequest(text='Invalid transaction description')
    return value


def redact_track_description(value):
    return '' if value and TRACK_TEXT.search(value) else (value or '')


async def resolve_legacy_card(args, db, *, issuance=False):
    """Validate existing virtual-card credentials; discard raw/parsed track details."""
    if not issuance and os.getenv('SHADYBANK_ALLOW_LEGACY_TRACKS', '1') != '1':
        raise web.HTTPBadRequest(text='Legacy card input disabled')
    field, value = track_text(args)
    first = TRACK1.search(value) if field in ('magstripe', 'track1') else None
    second = TRACK2.search(value) if first is None and field in ('magstripe', 'track2') else None
    matched = first or second
    if not matched:
        raise web.HTTPBadRequest(text='Invalid card input')
    card_row = await db.fetchrow('SELECT * FROM cards WHERE pan = $1 AND expires = $2',
                                matched['pan'], matched['exp'])
    if not card_row or not ((first and first['dd1'] == card_row['dd1']) or
                            (second and second['dd2'] == str(card_row['dd2']))):
        raise web.HTTPNotFound()
    card = {'pan': matched['pan']}
    if issuance:
        # Only the existing administrator-only issuance path needs these fields.
        card.update(exp=matched['exp'], dd1=card_row['dd1'])
    return {'account': card_row['account_id'], 'status': card_row['status'], 'card': card}
