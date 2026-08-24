"""纠错里贴的截图（M17.1 P0）：先传、后绑。

`image_assets` 在此之前每一行都必须挂在一篇文档上，而纠错稿在**发布之前
根本没有文档**——M16 当时把这件事记成 delta 推迟掉了，这里补上。

这一层守四件事，每一件错了的后果都写在对应的测试里：

1. **按魔数收，不按扩展名收。** 文件名和 Content-Type 都是上传方写的。
2. **落私有目录**，且 `owner_id` 非空——公共目录是 nginx 直发的。
3. **别人的图绑不上**，且是明确报错，不是悄悄忽略。
4. **改稿要重新绑**：删掉的图解回悬空，新贴的挂上。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from copilot import assets
from copilot.config import get_settings
from copilot.db.models import AnswerCorrection, Conversation, ImageAsset, Message, User

# 一张真的 1x1 PNG（不是"看起来像"的字节串——魔数校验认的就是这几个字节）
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
async def scrub_images(maker):
    """把这次测试造出来的图片行和文件清掉。"""
    before: set[uuid.UUID] = set()
    async with maker() as s:
        before = set((await s.execute(select(ImageAsset.id))).scalars())

    yield

    async with maker() as s:
        rows = list(
            (
                await s.execute(select(ImageAsset).where(ImageAsset.id.not_in(before or {uuid.uuid4()})))
            ).scalars()
        )
        for row in rows:
            try:
                assets.absolute_path(row.storage_path, private=row.owner_id is not None).unlink(
                    missing_ok=True
                )
            except ValueError:  # pragma: no cover - 路径越界的行本来就不该存在
                pass
        await s.execute(delete(ImageAsset).where(ImageAsset.id.not_in(before or {uuid.uuid4()})))
        await s.commit()


@pytest.fixture
async def answered(maker, logged_in, flagship_id):
    """一轮已经发生过的问答，纠错要挂在它上面。返回 assistant 消息 id。"""
    async with maker() as s:
        conv = Conversation(user_id=logged_in, knowledge_space_id=flagship_id, title="面单")
        s.add(conv)
        await s.flush()
        s.add(Message(conversation_id=conv.id, role="user", content="模板在哪里设置"))
        answer = Message(conversation_id=conv.id, role="assistant", content="在【设置】里。")
        s.add(answer)
        await s.commit()
        ids = (conv.id, answer.id)

    yield ids[1]

    async with maker() as s:
        await s.execute(delete(AnswerCorrection).where(AnswerCorrection.conversation_id == ids[0]))
        await s.execute(delete(Message).where(Message.conversation_id == ids[0]))
        await s.execute(delete(Conversation).where(Conversation.id == ids[0]))
        await s.commit()


async def upload(api_client, data: bytes = PNG, name: str = "shot.png", mime: str = "image/png"):
    return await api_client.post(
        "/api/answer-corrections/images", files={"file": (name, data, mime)}
    )


async def submit(api_client, msg_id, answer: str):
    return await api_client.post(
        "/api/answer-corrections",
        json={"messageId": str(msg_id), "correctedAnswer": answer, "reason": "第二步的图不对"},
    )


# ─────────── 1. 只收真的图片 ───────────


async def test_a_png_is_accepted_and_comes_back_as_markdown(
    api_client, logged_in, scrub_images, maker
):
    """传完给一段可以直接插进光标处的 Markdown——前端不该自己拼这个格式。"""
    r = await upload(api_client)

    assert r.status_code == 201, r.text
    got = r.json()
    assert got["url"] == f"/api/images/{got['id']}"
    assert got["markdown"] == f"![截图](/api/images/{got['id']})"

    async with maker() as s:
        row = await s.get(ImageAsset, uuid.UUID(got["id"]))
    assert row is not None
    assert row.source == "correction"
    assert row.document_id is None and row.correction_id is None, "刚传上来该是悬空的"
    assert row.mime_type == "image/png"


async def test_html_named_png_is_rejected(api_client, logged_in, scrub_images):
    """⭐ 文件名和 Content-Type 都是上传方写的，只有文件头不是。

    收下它的后果：我们会以 `image/png` 把一段 HTML 发回给别人的浏览器。
    """
    r = await upload(api_client, data=b"<html><script>alert(1)</script></html>")
    assert r.status_code == 400
    assert "png" in r.json()["detail"]


async def test_svg_is_rejected(api_client, logged_in, scrub_images):
    """⚠️ SVG 是可以带 `<script>` 的 XML——存起来再原样发出去就是存储型 XSS。

    它没有魔数，白名单顺带把它挡在外面。这条测试是为了让"顺带"变成"明写"。
    """
    r = await upload(api_client, data=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                     name="x.svg", mime="image/svg+xml")
    assert r.status_code == 400


async def test_an_empty_file_is_rejected(api_client, logged_in, scrub_images):
    r = await upload(api_client, data=b"")
    assert r.status_code == 400


async def test_an_oversized_image_is_rejected(api_client, logged_in, scrub_images, monkeypatch):
    """超限要在读完之前就停手，别让一个人反复传大文件把内存吃光。"""
    s = get_settings()
    monkeypatch.setattr(s, "correction_image_max_bytes", 1024, raising=False)
    r = await upload(api_client, data=PNG + b"\x00" * 2048)
    assert r.status_code == 413


async def test_anonymous_cannot_upload(api_client, scrub_images):
    r = await upload(api_client)
    assert r.status_code == 401


# ─────────── 2. 落私有目录，且鉴权 ───────────


async def test_the_screenshot_lands_in_the_private_directory(
    api_client, logged_in, scrub_images, maker
):
    """⚠️⚠️ **公共目录是 nginx 直发的，谁猜中文件名谁就能取。**

    纠错稿在审核通过之前只有本人和管理员该看得到，所以文件必须落在
    `data/private-images/`——而这由 `owner_id` 决定，不由调用方选。
    """
    got = (await upload(api_client)).json()
    async with maker() as s:
        row = await s.get(ImageAsset, uuid.UUID(got["id"]))

    assert row.owner_id == logged_in, "owner 为空就是把它变成了公共图"
    private = assets.absolute_path(row.storage_path, private=True)
    public = assets.absolute_path(row.storage_path, private=False)
    assert private.is_file(), "文件没落在私有目录"
    assert not public.exists(), "文件落进了 nginx 直发的公共目录"


async def test_someone_else_cannot_fetch_the_screenshot(api_client, logged_in, scrub_images):
    """越权取图是 404（不是 403）——404 才不会确认"这个 id 是真的"。"""
    got = (await upload(api_client)).json()

    api_client.cookies.clear()
    r = await api_client.get(got["url"])
    assert r.status_code == 404


# ─────────── 3. 提交时绑定 ───────────


async def test_submitting_binds_the_referenced_images(
    api_client, logged_in, answered, scrub_images, maker
):
    used = (await upload(api_client)).json()
    # 内容不同才是另一张图（按内容寻址，见 test_uploading_the_same_screenshot_twice…）
    spare = (await upload(api_client, data=PNG + b"\x00")).json()

    r = await submit(api_client, answered, f"改成这样：\n\n{used['markdown']}")
    assert r.status_code == 201, r.text
    correction_id = uuid.UUID(r.json()["id"])

    async with maker() as s:
        bound = await s.get(ImageAsset, uuid.UUID(used["id"]))
        loose = await s.get(ImageAsset, uuid.UUID(spare["id"]))
    assert bound.correction_id == correction_id
    assert loose.correction_id is None, "没被引用的图不该跟着挂上"


async def test_referencing_someone_elses_image_is_refused(
    api_client, logged_in, answered, scrub_images, maker, engine
):
    """⚠️ **明确报错，不是悄悄忽略。**

    悄悄忽略的表现是：审核界面上有一张图，而它属于另一个用户——
    图片本身仍然按 owner 鉴权，于是审核的人看到一个裂图，
    还以为是自己网络的问题。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker2 = async_sessionmaker(engine, expire_on_commit=False)
    async with maker2() as s:
        other = User(email=f"other-{uuid.uuid4().hex[:8]}@test.local", password_hash="x")
        s.add(other)
        await s.flush()
        rel, _ = assets.store_bytes(PNG, ".png", private=True)
        theirs = ImageAsset(
            document_id=None,
            correction_id=None,
            source="correction",
            owner_id=other.id,
            storage_path=rel,
            mime_type="image/png",
        )
        s.add(theirs)
        await s.commit()
        theirs_id, other_id = theirs.id, other.id

    r = await submit(api_client, answered, f"改成这样：![截图](/api/images/{theirs_id})")
    assert r.status_code == 400
    assert "不属于你" in r.json()["detail"]

    async with maker2() as s:
        row = await s.get(ImageAsset, theirs_id)
        assert row.correction_id is None, "别人的图被绑上了"
        await s.execute(delete(ImageAsset).where(ImageAsset.id == theirs_id))
        await s.execute(delete(User).where(User.id == other_id))
        await s.commit()


async def test_referencing_a_missing_image_is_refused(
    api_client, logged_in, answered, scrub_images
):
    r = await submit(api_client, answered, f"改成这样：![截图](/api/images/{uuid.uuid4()})")
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]


async def test_an_external_image_url_is_left_alone(
    api_client, logged_in, answered, scrub_images
):
    """正文里写外链图不进绑定流程——绑定的意思是"删纠错时一起删"，
    而我们只对自己收下来的那些负这个责。"""
    r = await submit(api_client, answered, "改成这样：![截图](https://example.com/x.png)")
    assert r.status_code == 201, r.text


# ─────────── 4. 改稿要重新绑 ───────────


async def test_editing_rebinds_the_images(
    api_client, logged_in, answered, scrub_images, maker
):
    """删掉的图解回悬空，新贴的挂上。

    漏了这一步的表现是"删掉的图在审核界面上还在"——审的和发的不是同一份。
    """
    first = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"第一版：\n\n{first['markdown']}")
    correction_id = r.json()["id"]

    # 换一张**内容不同**的图：图按内容寻址，传同一张回来的是同一行（见下一条）
    second = (await upload(api_client, data=PNG + b"\x00")).json()
    r2 = await api_client.patch(
        f"/api/answer-corrections/{correction_id}",
        json={"correctedAnswer": f"第二版：\n\n{second['markdown']}"},
    )
    assert r2.status_code == 200, r2.text

    async with maker() as s:
        dropped = await s.get(ImageAsset, uuid.UUID(first["id"]))
        added = await s.get(ImageAsset, uuid.UUID(second["id"]))
    assert dropped.correction_id is None, "改稿删掉的图还挂在纠错上"
    assert added.correction_id == uuid.UUID(correction_id)


async def test_pending_uploads_are_capped(api_client, logged_in, scrub_images, monkeypatch):
    """悬空图有配额：传了不提交的图没有任何行指向它，只能靠时间清。"""
    s = get_settings()
    monkeypatch.setattr(s, "correction_images_pending_max", 2, raising=False)

    assert (await upload(api_client)).status_code == 201
    # 内容不同才会是不同的文件（按内容寻址），否则第二张会复用同一个路径
    assert (await upload(api_client, data=PNG + b"\x00")).status_code == 201
    r = await upload(api_client, data=PNG + b"\x00\x00")
    assert r.status_code == 409
    assert "太多" in r.json()["detail"]


async def test_uploading_the_same_screenshot_twice_reuses_the_row(
    api_client, logged_in, scrub_images
):
    """图按内容寻址：同一张截图传两次落的是同一个文件，所以只该有一行。

    再建一行的话，两行指着同一个文件——删掉其中一条纠错时，另一行就指向了
    一个已经被删掉的文件；而同一条纠错里绑两行同路径会直接撞唯一索引，
    表现是"再传一次就 500"。
    """
    first = (await upload(api_client)).json()
    again = (await upload(api_client)).json()
    assert first["id"] == again["id"]
