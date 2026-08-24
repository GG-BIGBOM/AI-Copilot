"""清掉没人认领的纠错截图（M17.1）。

⭐ **和台账清理同一个理由：这条命令挂在 timer 上，没有人会看着它。**
坏掉的两种样子都不报错——删多了是别人的截图从答案里消失，删少了是磁盘
慢慢涨而库里没有任何一行指向那些文件。后一种 2026-08-24 真的发生过：
上传接口 500 之后，生产的 `data/uploads/` 里躺了两个孤儿文件，
**没有任何地方看得出来它们存在**。

纠错图天生就会产生这类孤儿：贴图的时候还没有 correction 行可挂
（先传、后绑），而写了一半关掉页面的那些永远不会有人来绑。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from copilot import assets
from copilot.cli import ORPHAN_IMAGE_HOURS, _prune_images
from copilot.db.models import ImageAsset

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
async def images(maker, logged_in):
    """造几张不同年龄、不同归属的截图，用完把行和文件都清干净。"""
    made: list[uuid.UUID] = []
    paths: set[str] = set()

    async def add(*, hours_ago: int, correction_id=None, payload: bytes = PNG) -> ImageAsset:
        rel, _ = assets.store_bytes(payload, ".png", private=True)
        paths.add(rel)
        async with maker() as s:
            row = ImageAsset(
                document_id=None,
                correction_id=correction_id,
                source="correction",
                owner_id=logged_in,
                storage_path=rel,
                mime_type="image/png",
                created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
            )
            s.add(row)
            await s.commit()
            made.append(row.id)
            return row

    yield add

    async with maker() as s:
        await s.execute(delete(ImageAsset).where(ImageAsset.id.in_(made)))
        await s.commit()
    for rel in paths:
        assets.absolute_path(rel, private=True).unlink(missing_ok=True)


async def alive(maker, image_id) -> bool:
    async with maker() as s:
        return (await s.get(ImageAsset, image_id)) is not None


async def test_a_dry_run_deletes_nothing(maker, images, capsys):
    """**默认只预演。** 一个默认就删的命令，配错一个参数就是每天悄悄多删一批。"""
    old = await images(hours_ago=ORPHAN_IMAGE_HOURS + 1)

    await _prune_images(apply=False, hours=ORPHAN_IMAGE_HOURS, maker=maker)

    assert await alive(maker, old.id)
    assert assets.absolute_path(old.storage_path, private=True).is_file()


async def test_an_old_orphan_is_removed_with_its_file(maker, images):
    old = await images(hours_ago=ORPHAN_IMAGE_HOURS + 1)
    path = assets.absolute_path(old.storage_path, private=True)

    await _prune_images(apply=True, hours=ORPHAN_IMAGE_HOURS, maker=maker)

    assert not await alive(maker, old.id)
    assert not path.exists(), "行删了、文件留着 = 又一个没人看得见的孤儿"


async def test_a_fresh_orphan_is_left_alone(maker, images):
    """传了图、写到一半去开会——这条路要活得下来。"""
    fresh = await images(hours_ago=1)

    await _prune_images(apply=True, hours=ORPHAN_IMAGE_HOURS, maker=maker)

    assert await alive(maker, fresh.id)


async def test_a_bound_image_is_never_touched(maker, images, logged_in, flagship_id):
    """已经挂在纠错上的图不算孤儿，多老都不能删。"""
    from copilot.db.models import AnswerCorrection, Conversation, Message

    async with maker() as s:
        conv = Conversation(user_id=logged_in, knowledge_space_id=flagship_id, title="t")
        s.add(conv)
        await s.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="原答案")
        s.add(msg)
        await s.flush()
        correction = AnswerCorrection(
            conversation_id=conv.id,
            message_id=msg.id,
            submitted_by=logged_in,
            knowledge_space_id=flagship_id,
            original_question="问题",
            original_answer="原答案",
            corrected_answer_markdown="改过的答案",
            reason="理由",
            status="pending",
        )
        s.add(correction)
        await s.commit()
        ids = (conv.id, correction.id)

    bound = await images(hours_ago=ORPHAN_IMAGE_HOURS * 10, correction_id=ids[1])

    await _prune_images(apply=True, hours=ORPHAN_IMAGE_HOURS, maker=maker)

    assert await alive(maker, bound.id)
    assert assets.absolute_path(bound.storage_path, private=True).is_file()

    async with maker() as s:
        await s.execute(delete(ImageAsset).where(ImageAsset.correction_id == ids[1]))
        await s.execute(delete(AnswerCorrection).where(AnswerCorrection.id == ids[1]))
        await s.execute(delete(Message).where(Message.conversation_id == ids[0]))
        await s.execute(delete(Conversation).where(Conversation.id == ids[0]))
        await s.commit()


async def test_a_file_still_used_by_another_row_survives(maker, images, logged_in, flagship_id):
    """⚠️ 图按内容寻址：同一个文件可能同时被另一行用着。

    删早了，那边就成了一个指向空文件的行——表现是"图裂了"，
    而且是在**另一个人的**纠错里裂。
    """
    from copilot.db.models import AnswerCorrection, Conversation, Message

    async with maker() as s:
        conv = Conversation(user_id=logged_in, knowledge_space_id=flagship_id, title="t")
        s.add(conv)
        await s.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="原答案")
        s.add(msg)
        await s.flush()
        correction = AnswerCorrection(
            conversation_id=conv.id,
            message_id=msg.id,
            submitted_by=logged_in,
            knowledge_space_id=flagship_id,
            original_question="问题",
            original_answer="原答案",
            corrected_answer_markdown="改过的答案",
            reason="理由",
            status="pending",
        )
        s.add(correction)
        await s.commit()
        ids = (conv.id, correction.id)

    # 同样的字节 → 同一个磁盘文件，一行悬空、一行挂在纠错上
    orphan = await images(hours_ago=ORPHAN_IMAGE_HOURS + 1)
    keeper = await images(hours_ago=ORPHAN_IMAGE_HOURS + 1, correction_id=ids[1])
    assert orphan.storage_path == keeper.storage_path, "夹具没造出「共用一个文件」的情形"

    await _prune_images(apply=True, hours=ORPHAN_IMAGE_HOURS, maker=maker)

    assert not await alive(maker, orphan.id)
    assert await alive(maker, keeper.id)
    assert assets.absolute_path(keeper.storage_path, private=True).is_file(), (
        "文件被删了，另一条纠错里的图当场裂掉"
    )

    async with maker() as s:
        await s.execute(delete(ImageAsset).where(ImageAsset.correction_id == ids[1]))
        await s.execute(delete(AnswerCorrection).where(AnswerCorrection.id == ids[1]))
        await s.execute(delete(Message).where(Message.conversation_id == ids[0]))
        await s.execute(delete(Conversation).where(Conversation.id == ids[0]))
        await s.commit()


async def test_document_images_are_not_orphans(maker, images, logged_in):
    """文档图有 `document_id`，怎么都不该被这条命令碰到。"""
    async with maker() as s:
        before = await s.scalar(
            select(ImageAsset.id).where(ImageAsset.document_id.is_not(None)).limit(1)
        )
    if before is None:
        pytest.skip("库里没有文档图，这条断言无从谈起")

    await _prune_images(apply=True, hours=0, maker=maker)

    async with maker() as s:
        assert await s.get(ImageAsset, before) is not None
