"""M0 冒烟测试：证明工程地基是活的。

跑这些测试需要本机 PostgreSQL 已启动、kb 库已建好、vector 扩展已启用。
"""

from sqlalchemy import text

from copilot.config import get_settings
from copilot.db.models import Base

EXPECTED_TABLES = {
    "users",
    "invite_codes",
    "documents",
    "chunks",
    "jobs",
    "conversations",
    "messages",
}


def test_settings_load():
    s = get_settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.embedding_dim == 1024, "bge-m3 是 1024 维，改这个值必须同步改迁移"
    assert s.rerank_top_k <= s.retrieve_top_k, "重排后的数量不该超过召回数量"


def test_all_tables_declared():
    """七张表都在 metadata 里——漏一张，alembic 就不会给它建表。"""
    assert set(Base.metadata.tables) >= EXPECTED_TABLES, (
        f"缺少表: {EXPECTED_TABLES - set(Base.metadata.tables)}"
    )


def test_chunk_has_owner_id():
    """隔离设计的地基：chunks 必须自带 owner_id，检索时才能不 join 直接过滤。"""
    assert "owner_id" in Base.metadata.tables["chunks"].columns


async def test_database_reachable(engine):
    async with engine.connect() as conn:
        assert (await conn.scalar(text("SELECT 1"))) == 1


async def test_pgvector_available(engine):
    """向量类型能用，且距离计算正确。这一条不过，整个 RAG 无从谈起。"""
    async with engine.connect() as conn:
        version = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        assert version is not None, "vector 扩展没装上"

        # [1,2,3] 与 [3,2,1] 的 L2 距离 = sqrt(4+0+4) = 2.828...
        distance = await conn.scalar(text("SELECT '[1,2,3]'::vector <-> '[3,2,1]'::vector"))
        assert abs(distance - 8**0.5) < 1e-9


async def test_migration_applied(engine):
    """迁移已跑到最新——七张表真的存在于数据库里，不只是在代码里。"""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        actual = {r[0] for r in rows}
    assert actual >= EXPECTED_TABLES, f"数据库里缺表: {EXPECTED_TABLES - actual}"
