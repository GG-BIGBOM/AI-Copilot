"""图片资产与私有图鉴权（M14-B）。

这一组守的是**一条今天还不存在的泄漏**：`/images/ab/xxxx.png` 线上由 nginx
直接发，不经过 Python，也就没有任何鉴权。公共库的语雀截图这样发本来就对，
可 M17 一旦从用户上传的文档里解出嵌图，同一条链路会把别人的私有截图挂在一个
只要猜中哈希就能取的公网地址上。

所以最重要的不是「资产行建出来了」，而是这三条：

    1. 私有图的地址在**离开检索层之前**就换成 `/api/images/{id}`
    2. 换不成（没有资产行）就**丢掉这一张**，不能原样放行
    3. `/api/images/{id}` 对不是本人的请求一律 404——不是 403

第 2 条最容易写反：直觉上"保守"是保留原地址，而那恰恰是把图漏出去。

⚠️ 私有图今天还产不出来（M17 才解嵌图），所以这里的私有用例是**手工造**
资产行和块的。这不是在测一条假链路——地址改写和鉴权是真代码，
M17 只是往里塞第一批真实数据。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import FakeEmbedder
from sqlalchemy import delete, select
from test_isolation import PassThroughReranker

from copilot import assets
from copilot.auth.security import create_access_token
from copilot.db.models import Chunk, Document, ImageAsset, User
from copilot.retrieve import search
from copilot.sources.images import PUBLIC_PREFIX

# 1×1 的透明 PNG。要真字节：接口是 FileResponse，读不到文件就 404
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fdff03fd0000000049454e44ae426082"
)


def _write_png(rel: str, *, private: bool = False) -> None:
    # ⚠️ M17 起公私两个根目录：私有图在 `data/private-images/`（nginx 不发它），
    # 公共图仍在 `data/images/`。写错目录的表现是接口 404——那正是
    # `/api/images/` 按 owner 选根目录在起作用，别把它当成测试夹具的毛病
    path = assets.root_for(private=private) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)


def _fresh_image(*, private: bool = False) -> tuple[str, str]:
    """造一张这次测试专用的图，返回（正文里的地址，磁盘相对路径）。"""
    ident = uuid.uuid4().hex[:16]
    rel = f"{ident[:2]}/{ident}.png"
    _write_png(rel, private=private)
    return f"{PUBLIC_PREFIX}/{rel}", rel


async def _cleanup(maker, tag: str) -> None:
    async with maker() as s:
        docs = list(
            (await s.execute(select(Document.id).where(Document.title.like(f"%{tag}%")))).scalars()
        )
        if docs:
            await s.execute(delete(ImageAsset).where(ImageAsset.document_id.in_(docs)))
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(docs)))
            await s.execute(delete(Document).where(Document.id.in_(docs)))
            await s.commit()


async def _assets_of(maker, doc_id: uuid.UUID) -> list[ImageAsset]:
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(ImageAsset)
                    .where(ImageAsset.document_id == doc_id)
                    .order_by(ImageAsset.storage_path)
                )
            ).scalars()
        )


# ---------- 双写：入库时资产行跟着块一起落 ----------


async def test_ingest_writes_one_asset_per_image(maker, other_space):
    """⭐ 真写入路径：`write_chunks` 把块里的图同步成资产行。

    ⚠️ 故意用**非默认空间**：conftest 的填充器补的是 flagship，
    所以如果这条链路根本没写空间，行会变成 flagship，和这里对不上。
    """
    from copilot.ingest.pipeline import write_chunks

    tag = uuid.uuid4().hex[:8]
    url_a, rel_a = _fresh_image()
    url_b, rel_b = _fresh_image()
    md = (
        f"# 电子面单-{tag}\n\n先在【设置-物流】里绑定物流账号，界面如下。\n\n"
        f"![]({url_a})\n\n## 打印\n\n绑定完成后回到订单列表，勾选后点打印。\n\n"
        f"![]({url_b})\n"
    )

    async with maker() as s:
        doc = Document(
            owner_id=None,
            knowledge_space_id=other_space.id,
            source_type="yuque",
            title=f"资产双写-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await write_chunks(s, doc, md, FakeEmbedder())
        await s.commit()
        doc_id = doc.id

    try:
        rows = await _assets_of(maker, doc_id)
        assert [r.storage_path for r in rows] == sorted([rel_a, rel_b])
        for r in rows:
            assert r.owner_id is None
            assert r.knowledge_space_id == other_space.id, "资产的空间没跟着文档走"
            assert r.mime_type == "image/png"
            # 文件在盘上就该量到大小和内容哈希
            assert r.file_size == len(PNG)
            assert r.sha256 is not None
    finally:
        await _cleanup(maker, tag)


async def test_reingest_drops_the_images_that_are_gone(maker, flagship_id):
    """改版后被删掉的图，资产行也要没。

    留着的表现是一个仍然可访问的孤儿：文档里早就没这张图了，
    地址却还能取到内容。
    """
    from copilot.ingest.pipeline import write_chunks

    tag = uuid.uuid4().hex[:8]
    url_a, rel_a = _fresh_image()
    url_b, _ = _fresh_image()
    both = (
        f"# 退货入库-{tag}\n\n第一步在退货单列表里找到这张单据。\n\n![]({url_a})\n\n"
        f"## 第二步\n\n核对数量后点确认入库，注意批次号要选对。\n\n![]({url_b})\n"
    )
    only_a = f"# 退货入库-{tag}\n\n第一步在退货单列表里找到这张单据。\n\n![]({url_a})\n"

    async with maker() as s:
        doc = Document(
            owner_id=None,
            knowledge_space_id=flagship_id,
            source_type="yuque",
            title=f"资产改版-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await write_chunks(s, doc, both, FakeEmbedder())
        await s.commit()
        doc_id = doc.id

    try:
        assert len(await _assets_of(maker, doc_id)) == 2

        async with maker() as s:
            doc = await s.get(Document, doc_id)
            await write_chunks(s, doc, only_a, FakeEmbedder())
            await s.commit()

        rows = await _assets_of(maker, doc_id)
        assert [r.storage_path for r in rows] == [rel_a], "改版后旧图的资产行没删掉"
    finally:
        await _cleanup(maker, tag)


async def test_deleting_a_document_takes_its_assets(api_client, maker, logged_in, flagship_id):
    """删文档要连资产行一起删——不然剩下一堆指向已删文档的孤儿。"""
    tag = uuid.uuid4().hex[:8]
    _, rel = _fresh_image()

    async with maker() as s:
        doc = Document(
            owner_id=logged_in,
            knowledge_space_id=flagship_id,
            source_type="upload",
            title=f"待删文档-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await s.flush()
        s.add(
            ImageAsset(
                document_id=doc.id,
                owner_id=logged_in,
                knowledge_space_id=flagship_id,
                storage_path=rel,
                mime_type="image/png",
            )
        )
        await s.commit()
        doc_id = doc.id

    try:
        r = await api_client.delete(f"/api/documents/{doc_id}")
        assert r.status_code == 204, r.text
        assert await _assets_of(maker, doc_id) == []
    finally:
        await _cleanup(maker, tag)


# ---------- 检索层：地址在这里定型 ----------


@pytest.fixture
async def private_doc_with_image(maker, logged_in, flagship_id):
    """一篇带图的私有文档，图有资产行。返回（正文，标题 tag，资产 id）。"""
    tag = uuid.uuid4().hex[:8]
    url, rel = _fresh_image(private=True)
    body = f"我们仓库的退货入库只走人工复核-{tag}"

    async with maker() as s:
        doc = Document(
            owner_id=logged_in,
            knowledge_space_id=flagship_id,
            source_type="upload",
            title=f"私有带图-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=logged_in,
                knowledge_space_id=flagship_id,
                ordinal=0,
                content=f"{body} [图:abcd]",
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
                images=[{"id": "abcd", "url": url}],
            )
        )
        asset = ImageAsset(
            document_id=doc.id,
            owner_id=logged_in,
            knowledge_space_id=flagship_id,
            storage_path=rel,
            marker="abcd",
            mime_type="image/png",
        )
        s.add(asset)
        await s.commit()
        ids = (body, tag, asset.id)

    yield ids

    await _cleanup(maker, tag)


async def _images_for(maker, query, space_id, user_id, tag):
    """这一轮召回里**标题带 tag 的那一块**的配图。

    ⚠️ 不能把所有块的图混在一起看：这是真库，top-20 里还有几千篇语雀文档
    带着自己的截图。断言里也要求这一块**确实被召回了**——否则"没有图"
    这个结论可能只是"这块压根没进来"，而那样的测试永远绿。
    """
    async with maker() as s:
        result = await search(
            s,
            query,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=user_id,
            space_id=space_id,
            top_k=20,
            rerank_k=20,
        )
    mine = [c for c in result.chunks if tag in c.citation.title]
    assert mine, f"目标块没被召回，这一轮什么都证明不了（tag={tag}）"
    return [img for c in mine for img in c.images]


async def test_public_images_keep_the_nginx_path(maker, flagship_id):
    """⭐ 公共图**不能**被改成 `/api/images/`。

    改了就是把几千张语雀截图的流量从 nginx 拽进 Python——那台机器一共
    1.6GB 内存。公共图人人可见，本来就不需要鉴权。
    """
    tag = uuid.uuid4().hex[:8]
    url, _ = _fresh_image()
    body = f"公共库的电子面单说明-{tag}"

    async with maker() as s:
        doc = Document(
            owner_id=None,
            knowledge_space_id=flagship_id,
            source_type="yuque",
            title=f"公共带图-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=None,
                knowledge_space_id=flagship_id,
                ordinal=0,
                content=f"{body} [图:abcd]",
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
                images=[{"id": "abcd", "url": url}],
            )
        )
        await s.commit()

    try:
        got = await _images_for(maker, body, flagship_id, None, tag)
        assert [i["url"] for i in got] == [url]
    finally:
        await _cleanup(maker, tag)


async def test_private_images_are_rewritten_to_the_api_path(
    maker, logged_in, flagship_id, private_doc_with_image
):
    """⭐ 私有图的地址在检索层就换掉，nginx 那条无鉴权的路径不许出现。"""
    body, tag, asset_id = private_doc_with_image

    got = await _images_for(maker, body, flagship_id, logged_in, tag)

    assert [i["url"] for i in got] == [f"/api/images/{asset_id}"]
    assert all(not i["url"].startswith(f"{PUBLIC_PREFIX}/") for i in got), "私有图漏在公开路径上"
    # 编号对照要跟着走：换的是地址，不是这张图的身份
    assert got[0]["id"] == "abcd"


async def test_a_private_image_without_an_asset_row_is_dropped(maker, logged_in, flagship_id):
    """⭐⭐ 查不到资产行就**丢掉**，不能原样放行。

    直觉上"保守"是保留原地址——而原地址正是那条没有鉴权的 nginx 路径，
    保留即泄漏。没有图只是少一张截图，泄漏不可挽回。
    """
    tag = uuid.uuid4().hex[:8]
    url, _ = _fresh_image()
    body = f"没有资产行的私有图-{tag}"

    async with maker() as s:
        doc = Document(
            owner_id=logged_in,
            knowledge_space_id=flagship_id,
            source_type="upload",
            title=f"缺资产行-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=logged_in,
                knowledge_space_id=flagship_id,
                ordinal=0,
                content=f"{body} [图:abcd]",
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
                images=[{"id": "abcd", "url": url}],
            )
        )
        await s.commit()  # 故意不建 ImageAsset

    try:
        assert await _images_for(maker, body, flagship_id, logged_in, tag) == []
    finally:
        await _cleanup(maker, tag)


# ---------- 接口：越权一律 404 ----------


async def test_the_owner_can_fetch_a_private_image(api_client, private_doc_with_image):
    _, _, asset_id = private_doc_with_image

    r = await api_client.get(f"/api/images/{asset_id}")

    assert r.status_code == 200, r.text
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    # 私有图不能进任何共享缓存——那等于绕过鉴权
    assert "private" in r.headers["cache-control"]


async def test_another_user_gets_404_not_403(api_client, maker, private_doc_with_image):
    """⭐ 越权是 404。403 等于告诉对方「这个 id 是真的」，而 id 可枚举。"""
    _, _, asset_id = private_doc_with_image

    email = f"mallory-{uuid.uuid4().hex[:8]}@test.local"
    async with maker() as s:
        other = User(email=email, password_hash="x", is_active=True)
        s.add(other)
        await s.commit()
        token = create_access_token(other.id)

    try:
        # ⚠️ 先清 cookie。`extract_token` **先看 cookie 再看 Authorization**，
        # 不清的话这一发请求仍然是 owner 自己打的，测试会绿得毫无意义
        api_client.cookies.clear()
        r = await api_client.get(
            f"/api/images/{asset_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 404, r.text
        assert r.content != PNG
    finally:
        async with maker() as s:
            await s.execute(delete(User).where(User.email == email))
            await s.commit()


async def test_anonymous_gets_404_for_a_private_image(api_client, private_doc_with_image):
    _, _, asset_id = private_doc_with_image

    # ⚠️ api_client 的 cookie 罐子里有 logged_in 的登录态，得显式清掉
    api_client.cookies.clear()
    r = await api_client.get(f"/api/images/{asset_id}")

    assert r.status_code == 404


async def test_a_public_image_is_served_to_anyone(api_client, maker, flagship_id):
    """公共图走这个接口也能取到（前端不必分辨走哪条路），不需要登录。"""
    tag = uuid.uuid4().hex[:8]
    _, rel = _fresh_image()

    async with maker() as s:
        doc = Document(
            owner_id=None,
            knowledge_space_id=flagship_id,
            source_type="yuque",
            title=f"公共资产-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await s.flush()
        s.add(
            ImageAsset(
                document_id=doc.id,
                owner_id=None,
                knowledge_space_id=flagship_id,
                storage_path=rel,
                mime_type="image/png",
            )
        )
        await s.commit()
        asset_id = (await _assets_of(maker, doc.id))[0].id

    try:
        api_client.cookies.clear()
        r = await api_client.get(f"/api/images/{asset_id}")
        assert r.status_code == 200, r.text
        assert r.content == PNG
    finally:
        await _cleanup(maker, tag)


@pytest.mark.parametrize("bad", ["not-a-uuid", "../../etc/passwd", str(uuid.uuid4())])
async def test_unknown_ids_are_404(api_client, bad):
    """畸形 id、不存在的 id，对调用方都该是同一件事。"""
    r = await api_client.get(f"/api/images/{bad}")
    assert r.status_code == 404


async def test_a_storage_path_that_escapes_the_image_dir_is_refused(
    api_client, maker, logged_in, flagship_id
):
    """⭐ 库里的路径也不可信。

    正常写入方只会写我们自己生成的两级路径，但一个会读文件的接口不该假设
    库里的值一定干净——真出现越界值，说明有别的写入方绕过了
    `sync_document_assets`，那更不能照着去读。
    """
    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        doc = Document(
            owner_id=logged_in,
            knowledge_space_id=flagship_id,
            source_type="upload",
            title=f"越界路径-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await s.flush()
        s.add(
            ImageAsset(
                document_id=doc.id,
                owner_id=logged_in,
                knowledge_space_id=flagship_id,
                storage_path="../../../../etc/passwd",
                mime_type="image/png",
            )
        )
        await s.commit()
        asset_id = (await _assets_of(maker, doc.id))[0].id

    try:
        r = await api_client.get(f"/api/images/{asset_id}")
        assert r.status_code == 404
    finally:
        await _cleanup(maker, tag)


# ---------- 地址解析 ----------


def test_only_known_prefixes_become_a_storage_path():
    """认两种形状，别的一律不认。

    ⚠️ **地址的形状不决定权限**——它只回答「这张图在磁盘上叫什么」。
    谁能看由 `ImageAsset.owner_id` 说了算。凭 Markdown 里写的东西判权限，
    等于把权限判断交给了正文内容。
    """
    assert assets.storage_path_of("/images/ab/cd.png") == "ab/cd.png"
    # M17：上传文档里解出来的图写成 `asset://`（浏览器不认这个 scheme，
    # 漏出去的表现是"图裂了"而不是"内容泄漏了"）
    assert assets.storage_path_of("asset://ab/cd.png") == "ab/cd.png"
    for bad in (
        "https://cdn.nlark.com/x.png",
        "/images/../../etc/passwd",
        "asset://../../etc/passwd",
        "/images/",
        "asset://",
        "/uploads/ab/cd.png",
    ):
        assert assets.storage_path_of(bad) is None, bad
