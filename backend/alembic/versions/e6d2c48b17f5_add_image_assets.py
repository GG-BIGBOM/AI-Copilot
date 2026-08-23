"""add image_assets and backfill it from chunks.images

Revision ID: e6d2c48b17f5
Revises: d4b1e63a920c
Create Date: 2026-08-23

M14-B：给每一张图一个能查 owner 的行。

在这之前，一张图只以 `chunks.images` 里的一段 JSON 存在，地址是
`/images/ab/xxxx.png`——线上这个前缀由 nginx 直接发，**不经过 Python，
没有任何鉴权**。公共库的语雀截图这样发本来就对，但 M17 一旦从用户上传的
文档里解出嵌图，同一条链路会把私有截图挂在一个只要猜中哈希就能取的地址上。

⚠️ **这张表是 `chunks.images` 的派生副本，不是替代品。** 双写期内正文标记、
块上的对照表都不变，检索仍然从 `chunks.images` 出发。所以 downgrade 直接
drop 是安全的——丢的只是一份可以重新算出来的索引，没有任何原始数据。

⚠️ **回填不读磁盘。** 跑 migration 的时候数据目录不一定在手边（备份恢复、
预发库、别人的开发机），为一个"尽力而为"的 sha256 让整个迁移依赖文件系统
不划算。`sha256` / `file_size` 因此历史行为 NULL，此后由 `assets.py`
在每次入库时补上——鉴权只依赖 owner，不依赖这两个值。

回滚说明：这张表只在 `/api/images/{id}` 和入库双写两处被用到，downgrade 后
私有图会退回没有资产行的状态（也就是 M14-B 之前的样子）。生产上如果已经
写入了新数据，优先用备份恢复，不要靠 downgrade 找补。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6d2c48b17f5"
down_revision: Union[str, Sequence[str], None] = "d4b1e63a920c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 块上记下来的每一条图片引用。JSON 里的 null 用 jsonb_typeof 挡掉——
# 见 models.py 文件头那条 NullableJSONB 的注释，库里的"空"有两种
_REFS = """
    SELECT c.document_id,
           img->>'url' AS url,
           img->>'id'  AS marker
    FROM chunks c
    CROSS JOIN LATERAL jsonb_array_elements(c.images) AS img
    WHERE jsonb_typeof(c.images) = 'array'
"""

# 后缀 → MIME。和 `copilot.assets._MIME_BY_SUFFIX` 是同一份表的两个副本，
# 不 import 的理由同 c3a7d82e5f19 的种子：migration 记录的是当天发生了什么
_MIME = """
    CASE lower(substring(g.url from '\\.([a-zA-Z0-9]+)$'))
        WHEN 'png'  THEN 'image/png'
        WHEN 'jpg'  THEN 'image/jpeg'
        WHEN 'jpeg' THEN 'image/jpeg'
        WHEN 'gif'  THEN 'image/gif'
        WHEN 'webp' THEN 'image/webp'
        WHEN 'bmp'  THEN 'image/bmp'
        ELSE 'application/octet-stream'
    END
"""


def upgrade() -> None:
    op.create_table(
        "image_assets",
        sa.Column(
            "id", sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 从所属文档冗余下来。**鉴权只看这一列**
        sa.Column("owner_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "knowledge_space_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_spaces.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("marker", sa.String(8), nullable=True),
        sa.Column("mime_type", sa.String(64), nullable=False, server_default="image/png"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_image_assets_document_id", "image_assets", ["document_id"])
    op.create_index("ix_image_assets_owner_id", "image_assets", ["owner_id"])
    op.create_index("ix_image_assets_sha256", "image_assets", ["sha256"])
    # 同一篇文档里同一个文件只该有一行。重新入库靠它做 upsert
    op.create_index(
        "ux_image_assets_document_path",
        "image_assets",
        ["document_id", "storage_path"],
        unique=True,
    )

    conn = op.get_bind()

    # ── 1. 先看有没有不认识的地址 ──
    # 认不出前缀就没法决定这张图归谁管，而"先放过去"意味着一张私有图
    # 留在没有鉴权的 nginx 路径上。宁可让 migration 当场炸掉。
    odd = conn.execute(
        sa.text(f"SELECT count(*) FROM ({_REFS}) r WHERE r.url NOT LIKE '/images/%'")  # noqa: S608
    ).scalar_one()
    if odd:
        raise RuntimeError(
            f"chunks.images 里有 {odd} 条不是 /images/ 开头的地址，回填不认识它们。"
            "先查清这些图是谁写进去的——它们的访问权限没人管得了。"
        )

    # ── 2. 回填。一行 = 一个（文档，磁盘文件） ──
    conn.execute(
        sa.text(
            "INSERT INTO image_assets "
            "(document_id, owner_id, knowledge_space_id, storage_path, marker, mime_type) "
            "SELECT g.document_id, g.owner_id, g.knowledge_space_id, "
            f"       substring(g.url from 9), g.marker, {_MIME} "
            "FROM ("
            "  SELECT d.id AS document_id, d.owner_id, d.knowledge_space_id, "
            "         r.url, min(r.marker) AS marker "
            f"  FROM ({_REFS}) r JOIN documents d ON d.id = r.document_id "  # noqa: S608
            "  WHERE r.url LIKE '/images/%' "
            "  GROUP BY d.id, d.owner_id, d.knowledge_space_id, r.url"
            ") g "
            "ON CONFLICT DO NOTHING"
        )
    )

    # ── 3. 校验。漏一张的表现是「这张私有图永远打不开」，没有报错 ──
    expected = conn.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT DISTINCT r.document_id, substring(r.url from 9) "
            f"  FROM ({_REFS}) r WHERE r.url LIKE '/images/%'"  # noqa: S608
            ") x"
        )
    ).scalar_one()
    got = conn.execute(sa.text("SELECT count(*) FROM image_assets")).scalar_one()
    if expected != got:
        raise RuntimeError(f"图片资产回填对不上：块里有 {expected} 张，表里只有 {got} 行。")

    # 冗余下来的两列必须与所属文档一致——这条规则和 chunks.owner_id 同级
    bad = conn.execute(
        sa.text(
            "SELECT count(*) FROM image_assets a JOIN documents d ON d.id = a.document_id "
            "WHERE a.owner_id IS DISTINCT FROM d.owner_id "
            "   OR a.knowledge_space_id IS DISTINCT FROM d.knowledge_space_id"
        )
    ).scalar_one()
    if bad:
        raise RuntimeError(f"有 {bad} 行资产的 owner / 知识版本和所属文档不一致。")


def downgrade() -> None:
    """整表删掉。**不动 `chunks.images`** —— 它才是事实来源，这张表是副本。"""
    op.drop_table("image_assets")
