"""Independent, opt-in HTTP service for scoped merchant instruments."""

import aioredis
import asyncpg
from aiohttp import web
from merchant_api import MerchantAPI


class MerchantService:
    def __init__(self):
        self._app = web.Application(client_max_size=1024)
        self._psql_pool = None
        self._redis_pool = None
        self._app.on_startup.append(self.start)
        self._app.on_cleanup.append(self.stop)
        self._app.add_routes([web.get('/health', self.health)])
        self.merchant = MerchantAPI(self)

    async def start(self, app):
        self._psql_pool = await asyncpg.create_pool(database='shadybucks', min_size=1, max_size=10)
        self._redis_pool = aioredis.from_url('redis://redis', decode_responses=True)
        await self._redis_pool.ping()

    async def stop(self, app):
        if self._redis_pool is not None:
            await self._redis_pool.close()
        if self._psql_pool is not None:
            await self._psql_pool.close()

    async def health(self, request):
        try:
            await self._psql_pool.fetchval('SELECT 1')
            await self._redis_pool.ping()
            if self.merchant.capture_enabled and not await self._psql_pool.fetchval(
                    "SELECT to_regclass('merchant_capture_requests') IS NOT NULL AND EXISTS("
                    "SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('transactions_purchase_authorization_once') "
                    "AND indisvalid AND indisunique)"):
                raise RuntimeError('Capture schema is unavailable')
        except Exception:
            raise web.HTTPServiceUnavailable(text='Merchant service unavailable') from None
        return web.json_response({'status': 'healthy'})


if __name__ == '__main__':
    web.run_app(MerchantService()._app, host='0.0.0.0', port=8080, access_log=None)
