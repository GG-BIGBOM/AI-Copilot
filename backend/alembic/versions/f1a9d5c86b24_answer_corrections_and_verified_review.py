"""answer_corrections, verified answer review fields, and revisions

Revision ID: f1a9d5c86b24
Revises: e6d2c48b17f5
Create Date: 2026-08-23

M16：把「答错了，我来改」从**提交即公共生效**改成**要过审才生效**。

在这之前，任何登录用户点一下就能让一段文字对全站立刻生效、无人审核——
那是一个任何注册用户都能往公共知识库里塞内容的入口（plan.md 的记账项之一）。

三件事：

1. 新表 `answer_corrections`：用户提交的纠错，带**不可变的原问答快照**。
   ⚠️ 它不是现有 `corrections`（语雀原文勘误）的扩展，两张表管的不是一回事。
2. `verified_answers` 补上审核后才有意义的列：知识版本、状态、来路、版本号。
   ⚠️ **唯一键从 `question` 改成 `(question, knowledge_space_id)`**——
   同一个问题在旗舰版和企业版有两套不同的正确答案，这正是知识版本存在的理由。
3. 新表 `verified_answer_revisions`：每改一版留一行，「修订可追溯」的落点。

回填：现有的 `verified_answers` 全部归 flagship、状态 active、版本 1，
并各补一行 version=1 的修订记录（写明「M16 之前的历史数据」）。
理由和 M14-A 的回填一样——那时候库里只有旗舰版一套语料。

回滚说明：downgrade 会**删掉整张 `answer_corrections`**（审核中的纠错会丢），
并把 `verified_answers` 退回到 M16 之前的形状。生产上如果已经收到过真实纠错，
优先用备份恢复，不要靠 downgrade 找补。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a9d5c86b24"
down_revision: Union[str, Sequence[str], None] = "e6d2c48b17f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. answer_corrections ──
    op.create_table(
        "answer_corrections",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # ⚠️ 这三列不加外键：消息会被删、trace 会被清理，而快照要活得比它们久
        sa.Column("trace_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "submitted_by",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "knowledge_space_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("original_answer", sa.Text(), nullable=False),
        sa.Column("original_citations", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("original_images", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("corrected_answer_markdown", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "reviewed_by",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for col in ("trace_id", "message_id", "submitted_by", "knowledge_space_id", "status", "created_at"):
        op.create_index(f"ix_answer_corrections_{col}", "answer_corrections", [col])

    # ── 2. verified_answers 补列 ──
    op.add_column(
        "verified_answers", sa.Column("knowledge_space_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "verified_answers",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "verified_answers", sa.Column("source_correction_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "verified_answers", sa.Column("source_trace_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "verified_answers", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "verified_answers", sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "verified_answers", sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True)
    )

    # 回填空间：现有订正全部归 flagship（那时候库里只有旗舰版一套语料）
    flagship = conn.execute(
        sa.text("SELECT id FROM knowledge_spaces WHERE code = 'flagship'")
    ).scalar_one()
    conn.execute(
        sa.text("UPDATE verified_answers SET knowledge_space_id = :sid WHERE knowledge_space_id IS NULL"),
        {"sid": flagship},
    )
    left = conn.execute(
        sa.text("SELECT count(*) FROM verified_answers WHERE knowledge_space_id IS NULL")
    ).scalar_one()
    if left:
        raise RuntimeError(f"还有 {left} 条订正没有知识版本，回填没做干净。")

    op.create_foreign_key(
        "fk_verified_knowledge_space",
        "verified_answers",
        "knowledge_spaces",
        ["knowledge_space_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_verified_source_correction",
        "verified_answers",
        "answer_corrections",
        ["source_correction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_verified_answers_space", "verified_answers", ["knowledge_space_id"])

    # ⭐ 唯一键换成 (question, knowledge_space_id)。
    # 旧的 question 单列 unique 会让「同一个问题在两个空间各有一条标准答案」
    # 直接写不进去，而那正是知识版本存在的理由
    op.drop_index("ix_verified_answers_question", table_name="verified_answers")
    op.create_index("ix_verified_answers_question", "verified_answers", ["question"])
    op.create_index(
        "ux_verified_question_space",
        "verified_answers",
        ["question", "knowledge_space_id"],
        unique=True,
    )

    # ── 3. verified_answer_revisions ──
    op.create_table(
        "verified_answer_revisions",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "verified_answer_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("verified_answers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("question", sa.String(1024), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("knowledge_space_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "editor_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_verified_answer_revisions_answer",
        "verified_answer_revisions",
        ["verified_answer_id"],
    )

    # 给现有订正各补一版历史，否则「修订可追溯」对老数据是断的
    conn.execute(
        sa.text(
            "INSERT INTO verified_answer_revisions "
            "(verified_answer_id, version, question, answer, knowledge_space_id, status, "
            " editor_id, note) "
            "SELECT id, 1, question, answer, knowledge_space_id, 'active', author_id, "
            "       'M16 之前的历史数据：那时还没有审核流程，作者提交即生效' "
            "FROM verified_answers"
        )
    )


def downgrade() -> None:
    op.drop_table("verified_answer_revisions")

    op.drop_index("ux_verified_question_space", table_name="verified_answers")
    op.drop_index("ix_verified_answers_question", table_name="verified_answers")
    op.create_index(
        "ix_verified_answers_question", "verified_answers", ["question"], unique=True
    )
    op.drop_constraint("fk_verified_source_correction", "verified_answers", type_="foreignkey")
    op.drop_constraint("fk_verified_knowledge_space", "verified_answers", type_="foreignkey")
    op.drop_index("ix_verified_answers_space", table_name="verified_answers")
    for col in (
        "last_hit_at",
        "hit_count",
        "version",
        "source_trace_id",
        "source_correction_id",
        "status",
        "knowledge_space_id",
    ):
        op.drop_column("verified_answers", col)

    op.drop_table("answer_corrections")
