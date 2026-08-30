"""remove unused enterprise knowledge spaces

Revision ID: 83d51db7d7a7
Revises: d9c4f2e81b36
Create Date: 2026-08-30 11:22:58.562664

2026-08-30：M18（多知识版本）这一轮主动砍掉，只保留旗舰版单独上线。
`enterprise_desktop` / `enterprise_web` 从种子建好那天起就是 inactive、
0 篇文档，从没被激活过——删之前查过 documents / chunks / conversations /
image_assets / verified_answers 五张表，两个 code 上全是 0 行。

⚠️ 六个 `knowledge_space_id` 外键都是 `ondelete="RESTRICT"`：如果哪张表
还有行指着这两个 code，这条 DELETE 会直接报错退出，不会静默清空或级联删
别的数据——查过一遍是保险，RESTRICT 是保险的保险。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83d51db7d7a7'
down_revision: Union[str, Sequence[str], None] = 'd9c4f2e81b36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REMOVED = (
    ("enterprise_desktop", "客户端企业版", "旺店通企业版（客户端）。语料尚未导入。", "inactive"),
    ("enterprise_web", "网页版企业版", "旺店通企业版（网页版）。语料尚未导入。", "inactive"),
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM knowledge_spaces WHERE code IN ('enterprise_desktop', 'enterprise_web')")
    )


def downgrade() -> None:
    """把两个种子行插回去，状态和当初一样：`inactive`、没有语料。"""
    conn = op.get_bind()
    for code, name, description, status in REMOVED:
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_spaces (id, code, name, description, status) "
                "VALUES (gen_random_uuid(), :code, :name, :description, :status) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "name": name, "description": description, "status": status},
        )
