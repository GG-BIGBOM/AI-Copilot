"""测试夹具。

关键：每个 async 测试跑在各自的事件循环里，而连接池会把上一个循环里建的
连接留给下一个测试用——那个循环已经关了，asyncpg 会直接崩。
所以测试一律用 NullPool 的独立引擎，用完即弃。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from copilot.config import get_settings


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def api_client(engine, maker):
    """打 ASGI 的 httpx 客户端，会自动带 cookie——正好用来验登录态。

    `get_session` 换成测试引擎（NullPool），理由同上：每个测试一个事件循环，
    共用连接池必崩。
    """
    from httpx import ASGITransport, AsyncClient

    from copilot.api.app import app
    from copilot.db.session import get_session

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
