"""add answer_source to request_trace

Revision ID: a1c7e4d20b31
Revises: 96f8bd847be6
Create Date: 2026-08-21

M13 P5。这一轮的答案是从哪来的：kb / general_knowledge / canned / tool / no_answer。

⚠️ **可空，且不给 server_default。**
NULL 的语义是「那时候还没有这一列」，和「不知道来源」不是一回事。
给个默认值（比如 'kb'）会让 M13 之前的每一行都凭空变成一条查库答案，
而 M12 放开常识正是在那段时间里上的线——那批历史行恰恰是最需要
「不知道」这个状态的。
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c7e4d20b31'
down_revision: Union[str, Sequence[str], None] = '96f8bd847be6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'request_trace',
        sa.Column('answer_source', sa.String(length=20), nullable=True),
    )
    op.create_index(
        'ix_request_trace_answer_source', 'request_trace', ['answer_source']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_request_trace_answer_source', table_name='request_trace')
    op.drop_column('request_trace', 'answer_source')
