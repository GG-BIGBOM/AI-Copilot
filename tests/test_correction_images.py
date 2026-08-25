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
from copilot.auth.security import create_access_token
from copilot.config import get_settings
from copilot.db.models import (
    AnswerCorrection,
    Chunk,
    Conversation,
    Document,
    ImageAsset,
    Message,
    User,
    VerifiedAnswer,
    VerifiedAnswerRevision,
)

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


# ─────────── 5. 发布：私有截图在这一刻变成公共图 ───────────


@pytest.fixture
async def admin_headers(maker):
    email = f"root-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        u = User(email=email, password_hash="x", is_active=True, is_admin=True)
        s.add(u)
        await s.commit()
        token, admin_id = create_access_token(u.id), u.id

    yield {"Authorization": f"Bearer {token}"}

    async with maker() as s:
        await s.execute(delete(User).where(User.id == admin_id))
        await s.commit()


@pytest.fixture
async def scrub_verified(maker):
    """把发布出来的标准答案连同它的索引文档一起删掉。"""
    yield
    async with maker() as s:
        for d in list(
            (
                await s.execute(select(Document).where(Document.source_type == "verified"))
            ).scalars()
        ):
            await s.execute(delete(ImageAsset).where(ImageAsset.document_id == d.id))
            await s.execute(delete(Chunk).where(Chunk.document_id == d.id))
            await s.delete(d)
        await s.execute(delete(VerifiedAnswerRevision))
        await s.execute(delete(VerifiedAnswer))
        await s.commit()


async def publish(api_client, headers, correction_id) -> dict:
    # ⚠️ **cookie 优先于 Authorization 头**（`auth/deps.extract_token`）。
    # 不清掉 cookie 的话，带着管理员 token 的请求仍然会被认成刚才那个
    # 普通用户，403——而报错看起来像"管理员守卫写错了"
    api_client.cookies.clear()
    r = await api_client.post(
        f"/api/admin/corrections/{correction_id}/review",
        json={"decision": "approve"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await api_client.post(
        f"/api/admin/corrections/{correction_id}/publish", json={}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_publishing_makes_the_screenshot_public(
    api_client, logged_in, answered, admin_headers, scrub_images, scrub_verified,
    fake_providers, maker,
):
    """⚠️⚠️ **发布 = 把一个人的私有截图变成全站可见。**

    标准答案是公共的（它要盖住所有人的错误答案），那么它的配图也必须是——
    一张只有本人能取的图挂在一条人人都会读到的答案里，
    表现是**所有其他人看到一个裂图**。

    两件事缺一不可：文件搬到公共目录、`owner_id` 改成 None。
    只做一件的表现都是"图裂了"，而且都不报错。
    """
    shot = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"正确的做法：\n\n{shot['markdown']}")
    correction_id = r.json()["id"]

    out = await publish(api_client, admin_headers, correction_id)
    assert out["applied"] is True

    async with maker() as s:
        row = await s.get(ImageAsset, uuid.UUID(shot["id"]))
        answer = (await s.execute(select(VerifiedAnswer.answer))).scalars().first()

    assert row.owner_id is None, "还是私有图——别人读这条标准答案时会看到裂图"
    assert assets.absolute_path(row.storage_path, private=False).is_file(), "文件没搬到公共目录"
    assert not assets.absolute_path(row.storage_path, private=True).exists(), "私有目录里还留着一份"

    # ⭐ 正文里的地址也要跟着换。`/api/images/{id}` 不是 `storage_path_of()`
    # 认识的形状，留着它的话切块时配不出资产行，检索层会把这张图直接丢掉
    assert f"/api/images/{shot['id']}" not in answer
    assert f"/images/{row.storage_path}" in answer


async def test_the_published_image_is_readable_by_anyone(
    api_client, logged_in, answered, admin_headers, scrub_images, scrub_verified,
    fake_providers, maker,
):
    """发布之后，没登录的人也该取得到这张图——那正是"公共"的意思。"""
    shot = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"看这里：\n\n{shot['markdown']}")
    await publish(api_client, admin_headers, r.json()["id"])

    api_client.cookies.clear()
    got = await api_client.get(f"/api/images/{shot['id']}")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"


async def test_the_verified_chunk_carries_the_image(
    api_client, logged_in, answered, admin_headers, scrub_images, scrub_verified,
    fake_providers, maker,
):
    """图要真的进到索引块里，否则答案发出去是没有配图的。"""
    shot = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"看这里：\n\n{shot['markdown']}")
    await publish(api_client, admin_headers, r.json()["id"])

    async with maker() as s:
        doc = (
            await s.execute(select(Document).where(Document.source_type == "verified"))
        ).scalars().first()
        chunks = list(
            (await s.execute(select(Chunk).where(Chunk.document_id == doc.id))).scalars()
        )
    urls = [img["url"] for c in chunks for img in (c.images or [])]
    assert urls, "块上一张图都没有——发布出去的答案不会有配图"
    assert all(u.startswith("/images/") for u in urls), urls


async def test_a_rejected_correction_keeps_its_screenshot_private(
    api_client, logged_in, answered, admin_headers, scrub_images, maker
):
    """没发布就不该动它的归属。审核不通过的稿子里那张图仍然只有本人能看。"""
    shot = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"看这里：\n\n{shot['markdown']}")
    cid = r.json()["id"]

    api_client.cookies.clear()  # 同 `publish()`：cookie 优先于 Bearer
    rejected = await api_client.post(
        f"/api/admin/corrections/{cid}/review",
        json={"decision": "reject", "note": "不对"},
        headers=admin_headers,
    )
    assert rejected.status_code == 200, rejected.text

    async with maker() as s:
        row = await s.get(ImageAsset, uuid.UUID(shot["id"]))
    assert row.owner_id == logged_in
    assert assets.absolute_path(row.storage_path, private=True).is_file()


async def test_a_second_correction_on_the_same_question_publishes_its_own_image(
    api_client, logged_in, answered, admin_headers, scrub_images, scrub_verified,
    fake_providers, maker,
):
    """同一个问题发第二版：新的截图也要变成公共图，标准答案是改它 + 加一版。

    ⚠️ 同一张图**不能**跨纠错复用（绑定那一层会 400），所以第二版换一张图——
    这也正是真实用法：改稿时截了一张新图。
    """
    from copilot.config import get_settings as _settings

    cookie_name = _settings().cookie_name
    user_cookie = api_client.cookies.get(cookie_name)

    first_shot = (await upload(api_client)).json()
    first = await submit(api_client, answered, f"第一版：\n\n{first_shot['markdown']}")
    await publish(api_client, admin_headers, first.json()["id"])

    api_client.cookies.set(cookie_name, user_cookie)  # 换回本人再提交第二版
    second_shot = (await upload(api_client, data=PNG + b"\x01")).json()
    second = await submit(api_client, answered, f"第二版：\n\n{second_shot['markdown']}")
    assert second.status_code == 201, second.text
    out = await publish(api_client, admin_headers, second.json()["id"])
    assert out["applied"] is True

    async with maker() as s:
        rows = list(
            (
                await s.execute(select(VerifiedAnswer).order_by(VerifiedAnswer.version.desc()))
            ).scalars()
        )
        newest = await s.get(ImageAsset, uuid.UUID(second_shot["id"]))
    assert len(rows) == 1 and rows[0].version == 2, "同一个问题该是改它 + 加一版，不是再插一条"
    assert newest.owner_id is None
    assert assets.absolute_path(newest.storage_path, private=False).is_file()


async def test_reusing_an_image_across_corrections_is_refused(
    api_client, logged_in, answered, scrub_images
):
    """一张图只能挂在一条纠错上。

    允许复用的话，删掉其中一条纠错会把另一条的图一起带走（外键级联），
    而那条纠错的审核界面上会突然少一张图。
    """
    shot = (await upload(api_client)).json()
    first = await submit(api_client, answered, f"第一版：\n\n{shot['markdown']}")
    assert first.status_code == 201, first.text

    again = await submit(api_client, answered, f"另一条：\n\n{shot['markdown']}")
    assert again.status_code == 400
    assert "另一条纠错" in again.json()["detail"]


async def test_the_review_screen_can_see_the_screenshots_and_whether_they_are_public(
    api_client, logged_in, answered, admin_headers, scrub_images, scrub_verified,
    fake_providers, maker,
):
    """审核界面要拿得到这些截图，并且看得出「发布之后会不会变成公开」。

    ⚠️ **这不是装饰**：一个人截图里可能有客户名、订单号、他自己的后台账号。
    发布会把它变成全站可见——审核的人必须在按下那个按钮**之前**知道这件事。
    """
    from copilot.config import get_settings as _settings

    cookie_name = _settings().cookie_name
    user_cookie = api_client.cookies.get(cookie_name)

    shot = (await upload(api_client)).json()
    r = await submit(api_client, answered, f"看这里：\n\n{shot['markdown']}")
    cid = r.json()["id"]

    api_client.cookies.clear()
    detail = await api_client.get(f"/api/admin/corrections/{cid}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    images = detail.json()["images"]
    assert [i["id"] for i in images] == [shot["id"]]
    assert images[0]["public"] is False, "还没发布就说成公开的，审核的人会以为已经泄漏了"

    api_client.cookies.set(cookie_name, user_cookie)
    await publish(api_client, admin_headers, cid)

    after = await api_client.get(f"/api/admin/corrections/{cid}", headers=admin_headers)
    assert after.json()["images"][0]["public"] is True, "发布之后仍然显示私有，提示会一直挂着"
