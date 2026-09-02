"""request_trace 补四列：M19-B 评测中心的观测维度

Revision ID: 5e04dd2b641d
Revises: 83d51db7d7a7
Create Date: 2026-09-02

⚠️⚠️ **这份是手写的，不是 autogenerate 出来的那份。**

`alembic revision --autogenerate` 在这个仓库上会生成一堆**破坏性 DDL**——
它把既有的模型/库漂移当成"你想做的改动"一起写进来了：

    drop_index('ix_chunks_content_tsv', postgresql_using='gin')   混合检索的 GIN 索引
    drop_index('ix_chunks_space_owner')                           隔离查询的复合索引
    alter_column('chunks', 'knowledge_space_id', nullable=True)   ⚠️ 把隔离列改成可空
    drop_constraint('knowledge_spaces_code_key', type_='unique')  唯一约束
    以及七八处 alter_column ... nullable=False

最后那条 `knowledge_space_id` 可空，动的是这个项目**唯一一条错了不可挽回**
的规则（plan.md 二·6）。而这些 DDL 在部署时会安静地跑过去、退出码 0，
表现是「检索变慢了」和「隔离的地基松了一格」——两样都不会当场报错。

所以这份迁移只保留四个 `add_column` 和一个索引。漂移本身是另一件事，
要单独查、单独修，不能顺手夹带在一次加列里。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e04dd2b641d"
down_revision: str | Sequence[str] | None = "83d51db7d7a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 四列全部 nullable：老数据不回填。NULL 的意思是「那时候还没有这一列」，
    # 给个默认值会让上线之前的行凭空多出一批"确定的观测"（同 answer_source）
    op.add_column("request_trace", sa.Column("verified_answer_id", sa.UUID(), nullable=True))
    op.add_column("request_trace", sa.Column("correction_id", sa.UUID(), nullable=True))
    op.add_column("request_trace", sa.Column("general_knowledge_used", sa.Boolean(), nullable=True))
    op.add_column("request_trace", sa.Column("image_count", sa.Integer(), nullable=True))
    # 只给 verified_answer_id 建索引：「这条标准答案被命中过几次」是要按它查的。
    # 另外三列没有独立的查法，建了也是白占空间
    op.create_index(
        "ix_request_trace_verified_answer_id", "request_trace", ["verified_answer_id"]
    )


def downgrade() -> None:
    # ⭐ 这个 downgrade 是**安全**的：四列都是这次新加的，删掉不会碰到任何
    # 上线之前就有的数据。和 b7e91c4d2a08 那份不同（它的 downgrade 会删掉
    # 所有纠错图，见 plan.md 0.2 的警告）。
    op.drop_index("ix_request_trace_verified_answer_id", table_name="request_trace")
    op.drop_column("request_trace", "image_count")
    op.drop_column("request_trace", "general_knowledge_used")
    op.drop_column("request_trace", "correction_id")
    op.drop_column("request_trace", "verified_answer_id")
