"""知识版本（KnowledgeSpace）的常量与解析。

⚠️ **这一层是隔离的第二根轴，写法上有一条硬规矩：程序里一律用 `code`，
不用中文名、也不用 id。** 中文名会改，id 每套环境都不一样（本机、服务器、
测试库各建各的），只有 code 是稳定的。

两个空间：

    flagship   旗舰版    现有语雀语料全部属于它，唯一可聊天的空间
    common     通用知识  跨版本都适用，只作为检索范围

⚠️ **`common` 不是一个能聊天的空间。** 它只作为**检索范围**存在：在旗舰版
提问也能召回 `common` 里的材料。把它放进用户可选列表等于让人选择
「我要在一个没有产品知识的空间里问问题」。

这一层机制原本是为多知识版本（企业版等）设计的隔离基础，多空间管理那层
（CLI、跨空间题集）2026-08-30 已经移除——旗舰版单独上线不需要。
`knowledge_space_id` 和 `_space_filter` 留着：旗舰版自己的检索隔离
就是靠它做的，不是装饰。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.db.models import KnowledgeSpace

FLAGSHIP = "flagship"
COMMON = "common"

SEED: tuple[tuple[str, str, str, str], ...] = (
    (FLAGSHIP, "旗舰版", "旺店通旗舰版 ERP。当前语雀知识库的全部内容。", "active"),
    (
        COMMON,
        "通用知识",
        "跨版本都适用的通用内容。只作为检索范围，不是可选的聊天空间。",
        "active",
    ),
)

# 用户能在聊天页选的。`common` 不在里面，理由见模块头
SELECTABLE = (FLAGSHIP,)

# 新会话、新上传在没有明说时落到哪个空间
DEFAULT = FLAGSHIP

SPACE_ROOTS: dict[str, tuple[str, ...]] = {
    FLAGSHIP: ("raw", "yuque"),
}


def root_for(code: str):
    """这个空间抓下来的原始文件放哪。**拼错就抛，绝不落回默认目录。**"""
    from copilot.config import get_settings

    if code not in SPACE_ROOTS:
        raise SpaceNotFound(
            f"没有 code={code!r} 这个知识版本，可选：{'、'.join(SPACE_ROOTS)}"
        )
    return get_settings().data_dir.joinpath(*SPACE_ROOTS[code])


class SpaceNotFound(LookupError):
    """按 code 找不到空间。**调用方必须 fail closed**，不要退回默认值——

    退回默认值意味着一次拼错的 code 会静静地把提问送进旗舰版，
    而用户以为自己在问企业版。那种错误没有任何症状。
    """


async def by_code(session: AsyncSession, code: str) -> KnowledgeSpace:
    """按 code 取一个空间。找不到就抛，不返回 None——见 `SpaceNotFound`。"""
    row = (
        await session.execute(select(KnowledgeSpace).where(KnowledgeSpace.code == code))
    ).scalar_one_or_none()
    if row is None:
        raise SpaceNotFound(f"没有 code={code!r} 这个知识版本")
    return row


async def default_id(session: AsyncSession) -> uuid.UUID:
    """默认空间的 id。回填、新建会话、上传都用它。"""
    return (await by_code(session, DEFAULT)).id


async def common_id(session: AsyncSession) -> uuid.UUID | None:
    """`common` 的 id；没建过就返回 None（检索那边据此跳过这一支）。"""
    try:
        return (await by_code(session, COMMON)).id
    except SpaceNotFound:
        return None


async def selectable(session: AsyncSession) -> list[KnowledgeSpace]:
    """用户可选的空间，按 `SELECTABLE` 的顺序返回，只要 `active` 的。"""
    rows = list(
        (
            await session.execute(
                select(KnowledgeSpace).where(
                    KnowledgeSpace.code.in_(SELECTABLE), KnowledgeSpace.status == "active"
                )
            )
        ).scalars()
    )
    order = {code: i for i, code in enumerate(SELECTABLE)}
    return sorted(rows, key=lambda r: order.get(r.code, len(order)))


async def ensure_seeded(session: AsyncSession) -> int:
    """把 `SEED` 里缺的空间补齐，返回新建了几个。

    幂等：已经存在的按 code 跳过，**不覆盖**已有的 name/status。
    """
    existing = set(
        (await session.execute(select(KnowledgeSpace.code))).scalars()
    )
    added = 0
    for code, name, description, status in SEED:
        if code in existing:
            continue
        session.add(
            KnowledgeSpace(code=code, name=name, description=description, status=status)
        )
        added += 1
    if added:
        await session.commit()
    return added
