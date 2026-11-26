"""Operator-run migration for existing explicit PIN rows, without logging their contents."""

import asyncio

import asyncpg

from pin_credentials import PIN, hash_pin, is_pin_hash


async def convert(connection):
    async with connection.transaction():
        rows = await connection.fetch("SELECT id,secret FROM secrets WHERE type='pin' FOR UPDATE")
        # Do not reinterpret web passwords as PINs or silently discard malformed records.
        if any(not is_pin_hash(row['secret']) and
               (not isinstance(row['secret'], str) or not PIN.fullmatch(row['secret'])) for row in rows):
            raise ValueError('Some PIN records require explicit operator re-provisioning')
        changed = 0
        for row in rows:
            if not is_pin_hash(row['secret']):
                digest = await asyncio.get_running_loop().run_in_executor(None, hash_pin, row['secret'])
                await connection.execute("UPDATE secrets SET secret=$1 WHERE id=$2 AND type='pin'", digest, row['id'])
                changed += 1
        await connection.execute('ALTER TABLE secrets VALIDATE CONSTRAINT member_pin_hash')
        return changed


async def main():
    connection = await asyncpg.connect(database='shadybucks')
    try:
        count = await convert(connection)
    finally:
        await connection.close()
    print(f'Converted {count} explicit PIN records; hashed PIN constraint validated.')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except ValueError as error:
        raise SystemExit(str(error)) from None
