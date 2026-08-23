"""add knowledge_spaces and nullable knowledge_space_id

Revision ID: b2f5a91c3d47
Revises: a1c7e4d20b31
Create Date: 2026-08-23

M14-A 的第一步：**只建结构，一行数据都不动。**

⚠️ 为什么拆成两个 migration。这一步给 `documents` / `conversations` 加的列
是**可空**的，因为加列的那一刻线上每一行都还没有值；直接建成 NOT NULL 会
在生产库上当场失败（表非空时 ALTER 加 NOT NULL 列必须给默认值，而这里的
默认值只能是「查出来的 flagship 的 id」，DDL 里写不出来）。

回填和 NOT NULL 在下一个 migration（`c3a7d82e5f19`），中间隔着一次校验。
两步分开还有一个好处：回填错了可以只回滚第二步，不用把表也删掉——
而删表意味着刚建好的 `knowledge_spaces` 连同它的 id 一起没了，
第二次建出来的 id 和第一次不一样，任何记下过这些 id 的地方全部对不上。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f5a91c3d47"
down_revision: Union[str, Sequence[str], None] = "a1c7e4d20b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_spaces",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        # ⚠️ 唯一约束不能省：回填、`common` 判定、`spaces.by_code` 都假设
        # 一个 code 只对应一行。重复一个就意味着两套"通用知识"，
        # 而检索会随机命中其中一套
        sa.Column("code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_knowledge_spaces_code", "knowledge_spaces", ["code"], unique=True)

    for table in ("documents", "conversations"):
        op.add_column(
            table,
            sa.Column("knowledge_space_id", sa.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            f"ix_{table}_knowledge_space_id", table, ["knowledge_space_id"]
        )
        # ⭐ 外键现在就加，NOT NULL 留到下一步。
        # 两者管的是不同的事：外键防的是「指向一个不存在的空间」，这一条从
        # 第一行数据写进去就该生效；NOT NULL 防的是「没有空间」，那要等回填完。
        #
        # ondelete=RESTRICT：删一个还有文档挂着的空间，直接报错。
        # 换成 CASCADE 等于「删空间顺手删掉一整版知识库的文档」，
        # 换成 SET NULL 等于把那些文档变成谁都搜不到的孤儿（检索 fail closed）。
        op.create_foreign_key(
            f"fk_{table}_knowledge_space",
            table,
            "knowledge_spaces",
            ["knowledge_space_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for table in ("documents", "conversations"):
        op.drop_constraint(f"fk_{table}_knowledge_space", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_knowledge_space_id", table_name=table)
        op.drop_column(table, "knowledge_space_id")
    op.drop_index("ix_knowledge_spaces_code", table_name="knowledge_spaces")
    op.drop_table("knowledge_spaces")
