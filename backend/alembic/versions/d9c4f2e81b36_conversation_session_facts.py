"""conversations.facts：会话级已确认事实（W2.2）

Revision ID: d9c4f2e81b36
Revises: c8d3f1a704be
Create Date: 2026-08-28

⚠️ **单独一列，不复用 `profile`。** `profile is not None` 是「这条会话在走
Agent」的路由标记（routes/chat.py），往里塞事实会把普通问答会话也路由过去——
表现是用户随口问一句就被追问「你们有几个仓」。

加完这一列，旧会话的它是 NULL，`SessionFacts.load(None)` 返回空表，
注入那一段整个不出现——也就是**行为和加这一列之前逐字节一致**。
真正会改变答案的是 `SESSION_FACTS_ENABLED`，那个默认关着。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9c4f2e81b36"
down_revision = "c8d3f1a704be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "facts")
