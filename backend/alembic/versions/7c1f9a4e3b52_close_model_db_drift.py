"""收掉模型和库之间最后 5 项漂移（ISSUES.md I-14）

Revision ID: 7c1f9a4e3b52
Revises: 5e04dd2b641d
Create Date: 2026-09-03

⚠️⚠️ **这份也是手写的。** 理由和 `5e04dd2b641d` 一样：在这个仓库上
`alembic revision --autogenerate` 会夹带破坏性 DDL（删混合检索的 GIN 索引、
把 `chunks.knowledge_space_id` 放松成可空）。见 ISSUES.md I-14。

这一轮把漂移从 16 项收到 0，分两半：

    11 项  模型该跟上库    只改声明，**零 DDL**（上一个 commit 做完了）
     5 项  库该跟上模型    就是这份迁移

⭐ **方向必须一项一项看，不能一刀切。** 同一批 `modify_nullable` 里，
`chunks/conversations/documents.knowledge_space_id` 是「库更严、模型更松」
（改模型），而下面这四个 `created_at/updated_at` 是「库更松、模型更严」
（改库）。按同一个方向批量处理的话，前三个会被放松成可空——而那正是
隔离的地基。

**为什么这四列本来就不该是可空的**　它们都带 `server_default=func.now()`，
实际上一行 NULL 都没有（迁移前实测：0 / 2、0 / 2、0 / 6091、0 / 0）。
库没拦着而已。「实际上不会为空」和「库保证不为空」是两件事，
而中间那段差距只在出事那天才有人看。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1f9a4e3b52"
down_revision: str | Sequence[str] | None = "5e04dd2b641d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 列)。四列都带 server_default，收紧前实测无 NULL
_TIGHTEN = [
    ("answer_corrections", "created_at"),
    ("answer_corrections", "updated_at"),
    ("image_assets", "created_at"),
    ("verified_answer_revisions", "created_at"),
]


def upgrade() -> None:
    for table, column in _TIGHTEN:
        # ⚠️ 先兜底再收紧。历史行理论上不会有 NULL（都有 server_default），
        # 但 `SET NOT NULL` 撞上一行 NULL 就整条迁移回滚——而部署已经跑到
        # 第 7 步了。这一句是幂等的、代价可忽略，换的是"迁移不会半路炸"
        op.execute(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL")  # noqa: S608
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )

    # 模型上一直写着 `index=True`，而库里没有。补上它，而不是把声明删掉——
    # `image_assets` 按空间查是真实存在的查法（`_space_filter` 那一路）
    op.create_index(
        "ix_image_assets_knowledge_space_id", "image_assets", ["knowledge_space_id"]
    )


def downgrade() -> None:
    # ⭐ 安全的 downgrade：只是把约束放松回去，**一行数据都不动**
    op.drop_index("ix_image_assets_knowledge_space_id", table_name="image_assets")
    for table, column in reversed(_TIGHTEN):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
