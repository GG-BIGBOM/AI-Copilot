"""normalize json null to sql null in nullable jsonb

Revision ID: 486fd1cae6f8
Revises: 108c3b17f470
Create Date: 2026-08-17 17:27:21.989434

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy  # Vector 列类型，autogenerate 生成的迁移需要它


# revision identifiers, used by Alembic.
revision: str = '486fd1cae6f8'
down_revision: Union[str, Sequence[str], None] = '108c3b17f470'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLAlchemy 早先把 Python 的 None 存成了 JSON 的 null 而不是 SQL 的 NULL
# （JSONB 的默认行为）。读回来都是 None，功能上无差别，但 SQL 侧变成两种"空"：
#     WHERE images IS NOT NULL   会把 JSON null 也算进来
#     jsonb_array_length(images) 撞上 JSON null 直接报错
# 模型侧已改用 JSONB(none_as_null=True)，这里把存量数据一并归一。
_COLUMNS = (("chunks", "images"), ("messages", "images"), ("messages", "citations"))


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = NULL WHERE jsonb_typeof({column}) = 'null'"  # noqa: S608
        )


def downgrade() -> None:
    # 不还原。JSON null 和 SQL NULL 读回来都是 None，退回去只会把混乱重新引入。
    pass
