"""内容治理的**共用**状态机与 Markdown 快照（M16 起）。

**状态机为什么要单独写在一处：** 「未审核的东西不许生效」这条规则，散在
四五个接口里各判一次的话，漏掉一处的表现是——某条路径上一个 pending 的纠错
被当成 approved 用了，而它看起来一切正常。所以合法迁移只有这里，
每个接口都来查它。

⭐⭐ **两种纠错共用这一层，但各有一张迁移表。**

    AnswerCorrection   改「这一轮的这个答案」  → 发布成 VerifiedAnswer
    Correction         改「哪一篇语雀原文」    → 发布后进 ingest，盖掉原文

它们的**审核原则、权限模型、审计字段完全一样**（这正是共用这个模块的理由），
但**状态图不一样**，所以不合成一张：

    答案纠错   published 是终态 —— 撤销要去动它产出的那条 VerifiedAnswer
               （`/api/verified/{id}` 退役），纠错这条记录本身不该被改写
    文档勘误   published **不是**终态 —— 这条记录**自己就是**生效的那个东西，
               没有第二个对象可退役。所以它需要 retired / superseded 两条出口

⚠️ **强行合成一张图会出事**：那样 `AnswerCorrection` 就允许 published → retired，
而那条路径没有任何实现——退役标准答案走的是另一个接口。留一条没人走、
也没人测的迁移，等于留一个以后会被误用的洞。

    ── 共用 ──────────────────────────────────────────
    pending    → approved | rejected | withdrawn
    approved   → published | rejected
    rejected   → （终态。要再来一次就重新提一条，别复活旧的）
    withdrawn  → （终态，同上）

    ── 只有文档勘误有 ────────────────────────────────
    published  → retired      管理员撤销：这条不该再生效了
               → superseded   同一篇文档有了更新的一条已发布勘误

谁能做什么（两种一致）：

    提交 / 编辑 / 撤回        作者本人，且只在 pending
    approved / rejected      **只有管理员**
    published                **只有管理员**，且必须先 approved
    retired / superseded     **只有管理员**（superseded 由发布动作自动写）

⚠️ `approved` 和 `published` 是两步，不是一步。中间那一步是「管理员看过了、
认可这个内容」，而 published 是「它现在真的在影响所有人的答案了」。
合成一步的话，审核通过的瞬间就写库、进索引、动检索——一旦发布出问题，
你分不清是「审得不对」还是「发布这一步炸了」。
两种纠错的发布都要打 embedding 接口，都会因为外部原因失败，所以这一条
对两边同样成立。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from copilot.db.models import AnswerCorrection

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
PUBLISHED = "published"
# 下面两个只有文档勘误用得上，见文件头
RETIRED = "retired"  # 管理员主动撤销一条已经生效的勘误
SUPERSEDED = "superseded"  # 同一篇文档有了更新的一条已发布勘误，这条自动让位

STATUSES = (PENDING, APPROVED, REJECTED, WITHDRAWN, PUBLISHED, RETIRED, SUPERSEDED)

# 答案纠错的合法迁移。**published 是终态**——撤销要去退役它产出的那条
# VerifiedAnswer，而不是回头改写这条纠错记录
STATE_MACHINE: dict[str, tuple[str, ...]] = {
    PENDING: (APPROVED, REJECTED, WITHDRAWN),
    APPROVED: (PUBLISHED, REJECTED),
    PUBLISHED: (),
    REJECTED: (),
    WITHDRAWN: (),
}

# 文档勘误的合法迁移。前四行和上面**逐字相同**（共用的就是这部分），
# 差别只在 published 那一行：这条记录自己就是生效的东西，没有第二个对象
# 可退役，所以它必须有出口
DOC_STATE_MACHINE: dict[str, tuple[str, ...]] = {
    PENDING: (APPROVED, REJECTED, WITHDRAWN),
    APPROVED: (PUBLISHED, REJECTED),
    PUBLISHED: (RETIRED, SUPERSEDED),
    REJECTED: (),
    WITHDRAWN: (),
    RETIRED: (),
    SUPERSEDED: (),
}

# 只有作者还能改内容的状态。审核过了再改，等于绕过审核。**两种纠错一致**
EDITABLE = (PENDING,)

# ⭐⭐ 允许进 RAG 的状态。**只有一个**——这就是「未审核不进 RAG」那条门禁。
# 两种纠错共用这一个常量，也共用这一条规矩：
#     答案纠错   `verified.publish_correction` 只在 published 这一步建索引
#     文档勘误   `ingest.corrections.load_db_corrections` 只读 published 的行
LIVE = (PUBLISHED,)


class TransitionError(ValueError):
    """不合法的状态迁移。调用方翻译成 409。"""


def check_transition(
    current: str, target: str, machine: dict[str, tuple[str, ...]] | None = None
) -> None:
    """`machine` 不传就是答案纠错那张表（历史调用方都不传，行为一字不变）。

    ⚠️ 文档勘误那条路**必须显式传 `DOC_STATE_MACHINE`**。忘了传的表现是
    「已发布的勘误撤销不了」——`check_transition` 会说不能从 published 变成
    retired，而那是另一张表的规矩。
    """
    allowed = (machine or STATE_MACHINE).get(current, ())
    if target not in allowed:
        raise TransitionError(f"不能从「{current}」变成「{target}」")


def snapshot_markdown(row: AnswerCorrection, *, submitted_by: str | None = None) -> str:
    """审核快照（路线图第 15 节）。

    ⚠️ **它不是事实来源，数据库才是。** 这份 Markdown 的用处是：审核时
    一眼看全、可以粘进 issue、可以进 Git 备份、可以拿两版来 diff。
    任何逻辑都不许回头去解析它——真要解析，就意味着同一份内容有两个
    互相可能不一致的表示。
    """
    from copilot.db.models import AnswerCorrection as _AC  # noqa: F401 - 仅为类型清晰

    def block(title: str, body: str) -> str:
        return f"# {title}\n\n{body.strip()}\n"

    cited = row.original_citations or []
    sources = (
        "\n".join(
            f"- [{c.get('n')}] {c.get('title', '')}"
            + (f" · {c.get('heading')}" if c.get("heading") else "")
            + (f"（{c.get('url')}）" if c.get("url") else "")
            for c in cited
        )
        or "（这一轮没有引用来源）"
    )
    images = (
        "\n".join(f"![图{img.get('n')}]({img.get('url')})" for img in (row.original_images or []))
        or "（这一轮没有配图）"
    )

    submitted_at = row.created_at.isoformat() if isinstance(row.created_at, datetime) else ""
    front = "\n".join(
        (
            "---",
            f'correction_id: "{row.id}"',
            f'source_trace_id: "{row.trace_id or ""}"',
            f'knowledge_space_id: "{row.knowledge_space_id or ""}"',
            f'question: "{row.original_question.replace(chr(34), chr(39))}"',
            f'submitted_by: "{submitted_by or row.submitted_by or ""}"',
            f'submitted_at: "{submitted_at}"',
            f'status: "{row.status}"',
            "---",
        )
    )

    return "\n".join(
        (
            front,
            "",
            block("问题", row.original_question),
            block("修正答案", row.corrected_answer_markdown),
            block("修改原因", row.reason),
            block("原回答", row.original_answer),
            block("原引用", sources),
            block("原配图", images),
        )
    )
