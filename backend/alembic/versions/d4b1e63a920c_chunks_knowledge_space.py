"""denormalize knowledge_space_id onto chunks

Revision ID: d4b1e63a920c
Revises: c3a7d82e5f19
Create Date: 2026-08-23

M14-A 的第三步：把知识版本冗余到 `chunks`，和 `owner_id` 一样。

⚠️ **为什么冗余而不是 join。** 检索的第一步是按向量距离排序取前 20，
走的是 embedding 上的索引；在那条语句上 join `documents` 会让规划器
在「先过滤再排序」和「先排序再过滤」之间摇摆，而后者在过滤掉大半数据时
要扫的行数是没有上限的。`owner_id` 当初冗余下来就是这个理由
（见 `db/models.py` 文件头），空间这一根轴同理。

代价是一条数据一致性规则：**`chunks.knowledge_space_id` 必须等于所属
document 的那一个**。和 owner_id 一样，只允许 `ingest/pipeline.write_chunks`
一处写值，且只能取 `doc.knowledge_space_id`。

回填直接从 documents 抄，所以不需要单独的"猜"的规则——上一个 migration
已经把 documents 全部归到 flagship 了。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b1e63a920c"
down_revision: Union[str, Sequence[str], None] = "c3a7d82e5f19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    op.add_column("chunks", sa.Column("knowledge_space_id", sa.UUID(as_uuid=True), nullable=True))

    # 从所属文档抄过来。这是唯一正确的来源——**不要**在这里再写一次
    # 「归 flagship」的规则：那样两处规则就可能漂移，而漂移的表现是
    # 「文档在这个版本里，它的块却在另一个版本里」，检索结果会自相矛盾。
    conn.execute(
        sa.text(
            "UPDATE chunks SET knowledge_space_id = d.knowledge_space_id "
            "FROM documents d WHERE d.id = chunks.document_id"
        )
    )

    left = conn.execute(
        sa.text("SELECT count(*) FROM chunks WHERE knowledge_space_id IS NULL")
    ).scalar_one()
    if left:
        raise RuntimeError(
            f"chunks 还有 {left} 行没有知识版本——多半是有块的 document_id 指向了"
            "一篇不存在的文档（外键本该拦住）。先查清再加 NOT NULL。"
        )

    op.alter_column("chunks", "knowledge_space_id", nullable=False)
    # ⭐ 复合索引，顺序是「空间 → owner」：检索**每一次**都按空间过滤，
    # 而 owner 那一支只有登录用户传了 user_id 时才用得上。
    # 选择性高的列放前面，索引才吃得住。
    op.create_index(
        "ix_chunks_space_owner", "chunks", ["knowledge_space_id", "owner_id"]
    )
    op.create_foreign_key(
        "fk_chunks_knowledge_space",
        "chunks",
        "knowledge_spaces",
        ["knowledge_space_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_chunks_knowledge_space", "chunks", type_="foreignkey")
    op.drop_index("ix_chunks_space_owner", table_name="chunks")
    op.drop_column("chunks", "knowledge_space_id")
