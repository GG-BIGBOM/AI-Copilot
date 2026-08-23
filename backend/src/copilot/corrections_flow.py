"""答案纠错的状态机与 Markdown 快照（M16）。

**状态机为什么要单独写在一处：** 「未审核的东西不许生效」这条规则，散在
四五个接口里各判一次的话，漏掉一处的表现是——某条路径上一个 pending 的纠错
被当成 approved 用了，而它看起来一切正常。所以合法迁移只有这一张表，
每个接口都来查它。

    pending    → approved | rejected | withdrawn
    approved   → published | rejected
    published  → （终态。要改就改标准答案本身，并留一条修订记录）
    rejected   → （终态。要再来一次就重新提一条，别复活旧的）
    withdrawn  → （终态，同上）

谁能做什么：

    withdrawn                作者本人（管理员也可以，替人撤回）
    approved / rejected      **只有管理员**
    published                **只有管理员**，且必须先 approved

⚠️ `approved` 和 `published` 是两步，不是一步。中间那一步是「管理员看过了、
认可这个内容」，而 published 是「它现在真的在影响所有人的答案了」。
合成一步的话，审核通过的瞬间就写库、进索引、动检索——一旦发布出问题，
你分不清是「审得不对」还是「发布这一步炸了」。
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

STATUSES = (PENDING, APPROVED, REJECTED, WITHDRAWN, PUBLISHED)

# 合法迁移。**全项目唯一一份**
STATE_MACHINE: dict[str, tuple[str, ...]] = {
    PENDING: (APPROVED, REJECTED, WITHDRAWN),
    APPROVED: (PUBLISHED, REJECTED),
    PUBLISHED: (),
    REJECTED: (),
    WITHDRAWN: (),
}

# 只有作者还能改内容的状态。审核过了再改，等于绕过审核
EDITABLE = (PENDING,)

# 允许进 RAG 的状态。**只有一个**——这就是「未审核不进 RAG」那条门禁
LIVE = (PUBLISHED,)


class TransitionError(ValueError):
    """不合法的状态迁移。调用方翻译成 409。"""


def check_transition(current: str, target: str) -> None:
    if target not in STATE_MACHINE.get(current, ()):
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
