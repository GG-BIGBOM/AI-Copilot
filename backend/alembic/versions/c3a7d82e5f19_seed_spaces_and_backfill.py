"""seed knowledge spaces, backfill existing rows, then require the column

Revision ID: c3a7d82e5f19
Revises: b2f5a91c3d47
Create Date: 2026-08-23

M14-A 的第二步：**种子 → 回填 → 校验 → 上 NOT NULL。** 四步在一个事务里，
中间任何一步不对就整个回滚——留下一半回填的表比没开始更难收拾。

回填规则：现有的文档和会话**全部**归 `flagship`。这不是猜的——语雀那个知识库
（`https://www.yuque.com/wdterpqjb`）就是旗舰版的，而用户上传的私有文档也都是
在只有旗舰版的时期传的。

⚠️ **校验那一步不能省。** 回填漏掉一行的表现是：那份文档在「我的知识库」
列表里好好地待着，检索却永远搜不到它（缺空间时 fail closed）——
没有报错、没有日志，只有用户觉得"我明明传过"。所以宁可让 migration 当场炸掉。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a7d82e5f19"
down_revision: Union[str, Sequence[str], None] = "b2f5a91c3d47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ⚠️ 这份种子和 `copilot.spaces.SEED` 是**同一份数据的两个副本**，改一处要改两处。
# 为什么不 import：migration 是历史记录，跑的是「当时那一版」的语义；
# import 应用代码的话，半年后有人给 SEED 加了第五个空间，这个早就跑过的
# migration 的含义会跟着变——而它记录的本该是 2026-08-23 那天发生了什么。
SEED = (
    ("flagship", "旗舰版", "旺店通旗舰版 ERP。当前语雀知识库的全部内容。", "active"),
    ("enterprise_desktop", "客户端企业版", "旺店通企业版（客户端）。语料尚未导入。", "inactive"),
    ("enterprise_web", "网页版企业版", "旺店通企业版（网页版）。语料尚未导入。", "inactive"),
    ("common", "通用知识", "跨版本都适用的通用内容。只作为检索范围，不是可选的聊天空间。", "active"),
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. 种子。已经存在的按 code 跳过，重跑安全 ──
    for code, name, description, status in SEED:
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_spaces (id, code, name, description, status) "
                "VALUES (gen_random_uuid(), :code, :name, :description, :status) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "name": name, "description": description, "status": status},
        )

    flagship_id = conn.execute(
        sa.text("SELECT id FROM knowledge_spaces WHERE code = 'flagship'")
    ).scalar_one()

    # ── 2. 回填。只动 NULL 的行，已经有值的不覆盖 ──
    for table in ("documents", "conversations"):
        conn.execute(
            sa.text(
                f"UPDATE {table} SET knowledge_space_id = :sid "  # noqa: S608 - 表名是字面量
                "WHERE knowledge_space_id IS NULL"
            ),
            {"sid": flagship_id},
        )

    # ── 3. 校验。漏一行就炸，别让它带着窟窿上线 ──
    for table in ("documents", "conversations"):
        left = conn.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE knowledge_space_id IS NULL")  # noqa: S608
        ).scalar_one()
        if left:
            raise RuntimeError(
                f"{table} 还有 {left} 行没有知识版本，回填没做干净——"
                "不能加 NOT NULL。先查清这些行是怎么来的。"
            )

    # ── 4. 上 NOT NULL ──
    for table in ("documents", "conversations"):
        op.alter_column(table, "knowledge_space_id", nullable=False)


def downgrade() -> None:
    """把 NOT NULL 摘掉，**但不删数据**。

    ⚠️ 不清空 `knowledge_space_id`，也不删种子。回滚的目的是让旧代码能跑
    （旧代码不认识这一列，可空就够了），不是把数据抹掉——真抹了的话，
    再升回来又要重新回填一次，而那时候库里已经混进了新写入的行，
    「全部归 flagship」这条规则就不再成立了。
    """
    for table in ("documents", "conversations"):
        op.alter_column(table, "knowledge_space_id", nullable=True)
