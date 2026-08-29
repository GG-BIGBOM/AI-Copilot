"""知识版本（KnowledgeSpace）的常量与解析。

⚠️ **这一层是隔离的第二根轴，写法上有一条硬规矩：程序里一律用 `code`，
不用中文名、也不用 id。** 中文名会改（「客户端企业版」哪天改叫「桌面版」），
id 每套环境都不一样（本机、服务器、测试库各建各的），只有 code 是稳定的。

四个空间：

    flagship            旗舰版          现有语雀语料全部属于它
    enterprise_desktop  客户端企业版    M18 才导入
    enterprise_web      网页版企业版    M18 才导入
    common              通用知识        跨版本都适用

⚠️ **`common` 不是一个能聊天的空间。** 它只作为**检索范围**存在：在任何空间
提问，都能召回 `common` 里的材料。把它放进用户可选列表等于让人选择
「我要在一个没有产品知识的空间里问问题」。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.db.models import KnowledgeSpace

FLAGSHIP = "flagship"
ENTERPRISE_DESKTOP = "enterprise_desktop"
ENTERPRISE_WEB = "enterprise_web"
COMMON = "common"

# 建库时预置这四个。**顺序就是用户看到的顺序。**
#
# ⚠️ 企业版预置成 `inactive`：表建好了不等于语料导进来了。留成 active 的话，
# 用户能选一个空空如也的版本，问什么都得到「知识库暂无此内容」——
# 那比看不到这个选项糟得多，因为他会以为是系统坏了。
# M18 真正导入之后再改成 active（`copilot spaces activate <code>`）。
SEED: tuple[tuple[str, str, str, str], ...] = (
    (FLAGSHIP, "旗舰版", "旺店通旗舰版 ERP。当前语雀知识库的全部内容。", "active"),
    (
        ENTERPRISE_DESKTOP,
        "客户端企业版",
        "旺店通企业版（客户端）。语料尚未导入。",
        "inactive",
    ),
    (
        ENTERPRISE_WEB,
        "网页版企业版",
        "旺店通企业版（网页版）。语料尚未导入。",
        "inactive",
    ),
    (
        COMMON,
        "通用知识",
        "跨版本都适用的通用内容。只作为检索范围，不是可选的聊天空间。",
        "active",
    ),
)

# 用户能在聊天页选的。`common` 不在里面，理由见模块头
SELECTABLE = (FLAGSHIP, ENTERPRISE_DESKTOP, ENTERPRISE_WEB)

# 新会话、新上传在没有明说时落到哪个空间
DEFAULT = FLAGSHIP

# ⭐ 抓下来的原始文件落在哪个目录（M18）。
#
# ⚠️ **旗舰版是历史遗留路径，不搬。** 搬了要全量重新向量化（几千次付费
# embedding），换来的只是目录好看。把"它是特例"这件事写在**一处**，
# 而不是在每个用到路径的地方各写一个 if。
#
#     flagship            data/raw/yuque/                 ← 历史路径，原地不动
#     enterprise_desktop  data/raw/spaces/enterprise_desktop/
#     enterprise_web      data/raw/spaces/enterprise_web/
SPACE_ROOTS: dict[str, tuple[str, ...]] = {
    FLAGSHIP: ("raw", "yuque"),
    ENTERPRISE_DESKTOP: ("raw", "spaces", ENTERPRISE_DESKTOP),
    ENTERPRISE_WEB: ("raw", "spaces", ENTERPRISE_WEB),
}


def root_for(code: str):
    """这个空间抓下来的原始文件放哪。**拼错就抛，绝不落回默认目录。**

    ⚠️ 默默落回默认目录是这一步最危险的错法：`--space enterprise_desktp`
    （少一个 o）会把企业版语料写进旗舰版那棵树，然后被下一次
    `copilot ingest` 当成旗舰版语料灌进去——**没有任何症状**，
    而且要发现它得靠有人问了一个企业版的问题、拿到企业版的答案，
    却在旗舰版会话里。
    """
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

    幂等：已经存在的按 code 跳过，**不覆盖**已有的 name/status——
    覆盖的话，M18 把企业版改成 active 之后，下一次跑这个函数又给改回 inactive。
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
