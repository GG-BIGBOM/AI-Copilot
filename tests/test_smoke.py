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


# ---------- logfire 插件必须保持关闭（M7 踩的坑）----------


def test_pydantic_logfire_plugin_is_disabled():
    """⭐ `copilot` 包一被导入就要关掉 pydantic 的 logfire 插件。

    不关的话，**第一次构建 pydantic 模型**时 pydantic 会去 load 这个 entry point
    （= import logfire），而 logfire 初始化会读**当前工作目录**下的
    `pyproject.toml`。以 `copilot` 系统账号从 `/root` 跑任何 CLI 命令就会炸在

        PermissionError: [Errno 13] Permission denied: 'pyproject.toml'

    而 pydantic 加载插件时只捕获 ImportError / AttributeError，
    PermissionError 直接穿出来，连个警告都没有。
    """
    import os

    import copilot  # noqa: F401 - 就是要它的副作用

    assert "logfire-plugin" in os.environ.get("PYDANTIC_DISABLE_PLUGINS", "")


def test_cli_path_does_not_import_logfire():
    """在**子进程**里验：走 CLI 的导入路径时 logfire 一个字节都不该加载。

    必须用子进程——同一个测试进程里别的用例可能已经 import 过 agent
    （pydantic-ai 自己会直接 import logfire），那样这条断言就永远是假阴性。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "from copilot.api.schemas import RegisterRequest;"
        "RegisterRequest(email='a@b.co', password='12345678', inviteCode='X');"
        "print('logfire' in sys.modules, len(sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )
    assert out.returncode == 0, out.stderr[-500:]
    loaded, _count = out.stdout.split()
    assert loaded == "False", f"logfire 又被加载了：{out.stdout}"
