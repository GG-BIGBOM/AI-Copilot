"""where an embedded image sits in its source document

Revision ID: a2c8f47b91d6
Revises: f1a9d5c86b24
Create Date: 2026-08-23

M17：上传文档里解出来的嵌图，要知道它在原文档的**哪个位置**。

    page_number    PDF 的第几页
    slide_number   PPT 的第几张
    sheet_name     Excel 的哪个工作表
    anchor         Excel 里钉在哪个单元格（R3C2）

⚠️ 归属错了**不会报错**，只会让答案配上另一页的截图——而用户照着点会点不到。
所以这四列不是「顺手记一下」，是嵌图这件事能不能用的前提。

M14-B 建表时刻意没加这几列（那时没有任何一处会往里写值，空列会让人以为
「解析器忘了填」）。现在有写入方了（`assets.UploadImageSink` →
`assets.sync_document_assets`），和它同一批加进来。

全部可空：语雀镜像下来的公共图没有「第几页」这回事，M17 之前的行也没有。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2c8f47b91d6"
down_revision: Union[str, Sequence[str], None] = "f1a9d5c86b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_assets", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column("image_assets", sa.Column("slide_number", sa.Integer(), nullable=True))
    op.add_column("image_assets", sa.Column("sheet_name", sa.String(128), nullable=True))
    op.add_column("image_assets", sa.Column("anchor", sa.String(64), nullable=True))


def downgrade() -> None:
    """删掉这四列。**不动图片文件，也不动别的列**——它们只是位置信息，
    丢了的后果是「答案里的图不知道来自第几页」，不是内容丢失。"""
    for col in ("anchor", "sheet_name", "slide_number", "page_number"):
        op.drop_column("image_assets", col)
