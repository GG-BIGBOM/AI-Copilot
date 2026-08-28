"""chunks.content_tsv：中文词法检索用的分词索引（W1.2）

Revision ID: c8d3f1a704be
Revises: b7e91c4d2a08
Create Date: 2026-08-28

⚠️ **加完这一列，旧块的它全是 NULL，也就是词法那一路一条都召不回。**
这不是 bug，是刻意的顺序：先上线（列在、开关关着、行为不变），
再回填（`copilot backfill-tsv`），最后才谈开不开 `HYBRID_ENABLED`。
反过来做的话，回填要在一个还没有这列的库上跑。

⚠️ 索引用 `CONCURRENTLY` 建不了——alembic 的迁移跑在一个事务里，
而 `CREATE INDEX CONCURRENTLY` 不允许在事务块内执行。5000 行的表建 GIN
是秒级的事，锁一下无所谓；等语料上到十万块再考虑手工在事务外建。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8d3f1a704be"
down_revision = "b7e91c4d2a08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("content_tsv", postgresql.TSVECTOR(), nullable=True))
    # GIN 是全文检索的标准选择：建得慢一点、占得大一点，但 `@@` 查得快得多。
    # 这张表是写少读多（入库一次、检索无数次），正是 GIN 的场景
    op.create_index(
        "ix_chunks_content_tsv",
        "chunks",
        ["content_tsv"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    # ⭐ 这个 downgrade 是安全的：`content_tsv` 是纯派生数据，
    # 从 `content` 用 `copilot backfill-tsv` 能原样再算一遍。
    # （对比 `b7e91c4d2a08`——那个的 downgrade 会删掉所有纠错图，是不可逆的。）
    op.drop_index("ix_chunks_content_tsv", table_name="chunks")
    op.drop_column("chunks", "content_tsv")
