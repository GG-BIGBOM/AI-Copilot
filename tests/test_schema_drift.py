"""模型和库必须严丝合缝（ISSUES.md I-14）。

⚠️⚠️ **这道闸门拦的不是"忘了写迁移"，是「autogenerate 替你做主」。**

2026-09-02 给 `request_trace` 加四列时，按常规跑
`alembic revision --autogenerate`，生成出来的迁移**除了那四列还夹带**：

    drop_index('ix_chunks_content_tsv', postgresql_using='gin')   混合检索的 GIN 索引
    drop_index('ix_chunks_space_owner')                           隔离查询的复合索引
    alter_column('chunks','knowledge_space_id', nullable=True)    ⚠️ 隔离列改成可空
    drop_constraint('knowledge_spaces_code_key', type_='unique')
    + 七八处 alter_column

最后一条动的是 plan.md 二·6 里「这个项目最不能出的 bug」。

⭐⭐ **最坏的地方在于它们全都不报错。** 这批 DDL 部署时安静跑过、退出码 0、
没有任何测试会红，表现只是「检索变慢了」和「地基松了一格」——
两样都要等到出事那天才有人看。

根因是模型和库长期漂移了 16 项，autogenerate 把那些漂移当成"你想做的改动"。
2026-09-03 收到 0：11 项改模型声明（零 DDL），5 项走迁移 `7c1f9a4e3b52`。

⚠️ **这道闸门必须钉在 0 上，不能钉一个"已知漂移清单"。** 允许一份清单存在，
下一次夹带就会被顺手加进清单里——而清单变长的那一刻不会有人拦。
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from copilot.config import get_settings
from copilot.db.models import Base


def _compare(sync_conn):
    return compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)


def _flatten(diffs) -> list:
    out = []
    for d in diffs:
        out.extend(d if isinstance(d, list) else [d])
    return out


def _describe(d) -> str:
    """把一条 diff 说成人话——报错信息要能直接照着修。"""
    kind = d[0]
    if kind in ("add_index", "remove_index"):
        idx = d[1]
        where = "模型里有、库里没有" if kind == "add_index" else "库里有、模型里没有"
        return f"{kind}: {idx.name}（{where}）"
    if kind == "modify_nullable":
        _, _schema, table, col, _opts, conn_nullable, meta_nullable = d
        return (
            f"modify_nullable: {table}.{col}  "
            f"库={'可空' if conn_nullable else 'NOT NULL'} "
            f"模型={'可空' if meta_nullable else 'NOT NULL'} "
            f"→ {'改模型' if not conn_nullable else '改库（要迁移）'}"
        )
    return f"{kind}: {d[1] if len(d) > 1 else d}"


@pytest.mark.asyncio
async def test_models_and_database_have_no_drift():
    """`compare_metadata` 必须返回空。

    红了怎么办——**先判方向，一项一项判，不要一刀切**：

        库更严、模型更松  →  改模型声明（零 DDL）
        库更松、模型更严  →  写迁移

    这两种在同一批 `modify_nullable` 里会同时出现。2026-09-02 那次就是：
    `chunks.knowledge_space_id` 属前者、`answer_corrections.created_at`
    属后者，按同一个方向批量处理会把隔离列放松成可空。

    ⚠️ **永远不要靠 `--autogenerate` 的产物直接修**——它的方向永远是
    「让库去迁就模型」，而上面第一种情形要的恰恰是反过来。
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            diffs = _flatten(await conn.run_sync(_compare))
    finally:
        await engine.dispose()

    assert not diffs, "模型和库漂移了 %d 项：\n  %s" % (
        len(diffs),
        "\n  ".join(_describe(d) for d in diffs),
    )


def test_the_isolation_column_is_declared_not_null():
    """⚠️ 单独钉住这三列——它们是隔离的地基，被放松过一次。

    上面那道闸门已经能拦住，但它拦的是"有没有漂移"。这一条说的是
    **哪一件事绝对不许发生**：`knowledge_space_id` 可空 = 一篇文档可以没有
    所属版本，而 `_space_filter` 对它是 fail closed 的——谁都搜不到它，
    而那种失败没有任何症状（文档在列表里好好的）。
    """
    from copilot.db.models import Chunk, Conversation, Document

    for model in (Document, Chunk, Conversation):
        col = model.__table__.c.knowledge_space_id
        assert not col.nullable, f"{model.__tablename__}.knowledge_space_id 不许可空"


def test_the_hybrid_search_index_is_declared_in_the_model():
    """⚠️ GIN 索引必须在模型里声明，不能只活在迁移里。

    只活在迁移里的索引，autogenerate 会认为「库里有、模型里没有」而 drop 它。
    它一删，混合检索退化成顺序扫描——**只表现为"变慢了"**。
    """
    from copilot.db.models import Chunk

    names = {ix.name for ix in Chunk.__table__.indexes}
    assert "ix_chunks_content_tsv" in names
    assert "ix_chunks_space_owner" in names
