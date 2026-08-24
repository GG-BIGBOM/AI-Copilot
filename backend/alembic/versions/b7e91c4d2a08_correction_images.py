"""images can belong to an answer correction, not only to a document

Revision ID: b7e91c4d2a08
Revises: a2c8f47b91d6
Create Date: 2026-08-24

纠错传图（路线图 13.2 / 17）：用户在「答错了，我来改」里贴一张截图。

在此之前 `image_assets` 的每一行都必须挂在一篇文档上（`document_id` NOT NULL）——
而纠错稿在**被发布之前根本没有文档**（发布时才建 `source_type='verified'` 那篇）。
M16 当时把这件事明确记成 delta 推迟掉了，这里补上：

    document_id    改成可空
    correction_id  新增，指向 answer_corrections（级联删）
    source         新增，'document' | 'correction'，NOT NULL

⚠️ **两个归属列不能同时有值。** 一行图要么属于一篇文档，要么属于一条纠错；
同时有值意味着「这张图算谁的」有两个答案，而鉴权和删除各按各的走——
表现是删了文档图还在、或者一张私有图跟着公共文档一起被发出去。
所以这条是 CHECK 约束，不是代码里的自觉。

⚠️ **两个都为空是合法的**，且**必须合法**：截图是在提交纠错**之前**传上来的
（用户要先看到图才知道自己写的对不对），那一刻还没有 correction 行可挂。
这种"悬空"的行由 `copilot prune-junk` 按时间清掉。

⚠️ **downgrade 会删掉所有纠错图**（`document_id` 要变回 NOT NULL）。
生产上真收到过纠错图之后，优先用备份恢复，不要跑 downgrade——
同 `f1a9d5c86b24` 那条一样的理由。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b7e91c4d2a08"
down_revision: Union[str, Sequence[str], None] = "a2c8f47b91d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 恰好这两种形态之一：挂文档、挂纠错、或者都不挂（刚传上来、还没提交）。
# 唯一被禁的是**两个都挂**
_BOTH = "ck_image_assets_one_owner"


def upgrade() -> None:
    op.alter_column("image_assets", "document_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column(
        "image_assets", sa.Column("correction_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "image_assets",
        sa.Column("source", sa.String(16), nullable=False, server_default="document"),
    )
    op.create_foreign_key(
        "fk_image_assets_correction",
        "image_assets",
        "answer_corrections",
        ["correction_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_image_assets_correction_id", "image_assets", ["correction_id"])
    op.create_check_constraint(
        _BOTH,
        "image_assets",
        "NOT (document_id IS NOT NULL AND correction_id IS NOT NULL)",
    )
    # 同一条纠错里同一个文件只该有一行（同 `ux_image_assets_document_path` 的理由）。
    # 部分索引：`correction_id` 为空的那些（文档图、悬空图）不进这个索引
    op.create_index(
        "ux_image_assets_correction_path",
        "image_assets",
        ["correction_id", "storage_path"],
        unique=True,
        postgresql_where=sa.text("correction_id IS NOT NULL"),
    )


def downgrade() -> None:
    # ⚠️ 见 docstring：这一步**会丢数据**。纠错图和悬空图都挂不回文档，
    # 而 document_id 马上要变回 NOT NULL
    op.execute("DELETE FROM image_assets WHERE document_id IS NULL")
    op.drop_index("ux_image_assets_correction_path", table_name="image_assets")
    op.drop_constraint(_BOTH, "image_assets", type_="check")
    op.drop_index("ix_image_assets_correction_id", table_name="image_assets")
    op.drop_constraint("fk_image_assets_correction", "image_assets", type_="foreignkey")
    op.drop_column("image_assets", "source")
    op.drop_column("image_assets", "correction_id")
    op.alter_column("image_assets", "document_id", existing_type=UUID(as_uuid=True), nullable=False)
