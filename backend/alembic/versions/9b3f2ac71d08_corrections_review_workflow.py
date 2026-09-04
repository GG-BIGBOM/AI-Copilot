"""corrections review workflow: submit != effective

Revision ID: 9b3f2ac71d08
Revises: 7c1f9a4e3b52
Create Date: 2026-09-03

⚠️⚠️ **手写的，不是 `alembic revision --autogenerate` 生成的**（ISSUES.md I-14）。
在这个仓库上 autogenerate 会夹带破坏性 DDL（删混合检索的 GIN 索引、
把隔离列改成可空），而那批 DDL 部署时会安静跑过、退出码 0、没有测试会红。
这一份只做下面五件事，逐行读完再留。

---

## 这个迁移在修什么

`corrections` 这张表原来**没有任何流程状态**：一行写进去就是生效的，
`ingest.corrections.load_db_corrections` 无条件读全表、盖到语雀原文上。
也就是说「提交」和「修改公共事实」是同一个动作。现在把它们拆开：

    提交 → pending → 管理员 approve → publish → 才进 RAG

## 历史数据怎么迁移（这一步最要紧）

⚠️⚠️ **存量的每一行今天都是生效的**，所以必须整体映射到 `published`，
**绝不能落到 `pending`**。落到 pending 的表现是：迁移跑完、部署成功、
退出码 0，而线上那几条勘误**在下一次 ingest 时集体失效**，
公共知识库悄悄回到语雀原文——没有报错，没有任何一处看得出来。
「加一个带默认值的状态列」这种看起来最无害的改动，恰恰是这一类事故的典型形态。

所以顺序是：先加列（server_default 让存量行拿到 'pending'），
**紧接着一句 UPDATE 把它们全部改成 'published'**。迁移跑的时候还没有任何
新代码在写行，所以"此刻表里的每一行"就精确等于"存量行"。

`published_at` 回填成 `updated_at`：那是我们对"它什么时候开始生效"
最好的近似（勘误保存即生效，保存就会刷新 updated_at）。
`reviewed_by` / `reviewed_at` **留空**——它们确实没有被任何人审过，
编一个审核人比留空更糟。`review_note` 写明这是迁移带进来的。

## 唯一性从全表收窄成"只在已发布之间"

原来 `ix_corrections_target_url` 是**唯一**索引。在提交队列下这是错的：
两个用户先后给同一篇提勘误是完全正常的事，全表唯一会让第二个人直接撞库。
换成：普通索引 + 一条 `WHERE status = 'published'` 的部分唯一索引。
真正要守的不变量（同一篇最多一条**生效**的勘误，否则 `apply_corrections`
的字典行为取决于构造顺序）一分没松。

## downgrade

只删列和索引、**一行数据都不动**，并把全表唯一索引还回去。
⚠️ 还原全表唯一之前会先删掉重复的 target_url 行是**不做**的——
那是丢数据。真撞上重复就让 downgrade 失败，人来决定删哪条。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3f2ac71d08"
down_revision: str | Sequence[str] | None = "7c1f9a4e3b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- 1. 审核字段 ----
    op.add_column(
        "corrections",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "corrections",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "corrections",
        sa.Column("reviewed_by", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "corrections",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("corrections", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column(
        "corrections",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_corrections_reviewed_by_users",
        "corrections",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---- 2. 存量行 = 已经生效的行。整体映射到 published ----
    #
    # ⚠️ 这一句**不能省，也不能挪到后面**：下面那条部分唯一索引是
    # `WHERE status = 'published'`，先建索引再 UPDATE 的话，
    # 如果存量里真有两条指向同一篇（原来的全表唯一保证了不会，
    # 但迁移不该依赖另一条约束的正确性），UPDATE 会在半路炸掉，
    # 而前面几列已经加上了——留下一个半迁移的表。
    op.execute(
        """
        UPDATE corrections
           SET status = 'published',
               published_at = COALESCE(published_at, updated_at, created_at),
               review_note = COALESCE(
                   review_note,
                   '迁移带入：这条勘误在审核流程上线之前就已经生效，未经审核'
               )
        """
    )

    # ---- 3. 唯一性收窄成「只在已发布之间」 ----
    op.drop_index("ix_corrections_target_url", table_name="corrections")
    op.create_index("ix_corrections_target_url", "corrections", ["target_url"], unique=False)
    op.create_index(
        "ux_corrections_published_target",
        "corrections",
        ["target_url"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    # ---- 4. 状态列的索引（审核队列按 status 过滤）----
    op.create_index("ix_corrections_status", "corrections", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_corrections_status", table_name="corrections")
    op.drop_index("ux_corrections_published_target", table_name="corrections")
    op.drop_index("ix_corrections_target_url", table_name="corrections")
    # ⚠️ 还原成全表唯一。表里真有重复 target_url 时这一句会失败——
    # **那是对的**：让人来决定删哪一条，别在 downgrade 里替他丢数据
    op.create_index("ix_corrections_target_url", "corrections", ["target_url"], unique=True)

    op.drop_constraint("fk_corrections_reviewed_by_users", "corrections", type_="foreignkey")
    op.drop_column("corrections", "published_at")
    op.drop_column("corrections", "review_note")
    op.drop_column("corrections", "reviewed_at")
    op.drop_column("corrections", "reviewed_by")
    op.drop_column("corrections", "version")
    op.drop_column("corrections", "status")
