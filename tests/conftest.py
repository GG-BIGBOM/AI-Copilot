"""测试夹具。

关键：每个 async 测试跑在各自的事件循环里，而连接池会把上一个循环里建的
连接留给下一个测试用——那个循环已经关了，asyncpg 会直接崩。
所以测试一律用 NullPool 的独立引擎，用完即弃。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from copilot.auth.invites import create_invite_codes
from copilot.config import get_settings
from copilot.db.models import (
    Chunk,
    Conversation,
    Document,
    InviteCode,
    KnowledgeSpace,
    Message,
    User,
)

# ─────────────────────────────────────────────────────────
# 知识版本（M14-A）
#
# `documents` / `chunks` / `conversations` 上的 `knowledge_space_id` 是 NOT NULL，
# 而这份测试里有二十几处直接 new 出这些对象的地方。给每一处都手写一个 id，
# 改动大、噪声大，而且和绝大多数测试想验的东西无关。
#
# ⚠️⚠️ **所以这里装一个只在测试里生效的填充器：没写空间的，自动补 flagship。**
# 它的危险是显而易见的——**生产代码忘了传空间，测试也不会红**。
# 所以真正要守的那几条写入路径，各自有不依赖这个填充器的断言：
#
#     ingest 写块        test_isolation.py::test_ingested_chunks_inherit_the_document_space
#     新建会话           test_multiturn.py::test_a_new_conversation_is_pinned_to_a_space
#     用户上传           test_api_documents.py::test_an_upload_lands_in_the_chat_space
#
# 那三条断言直接读库里的值，填充器补出来的和真代码写进去的在它们眼里不一样
# （填充器补的是 flagship，而它们验的是"跟着来源走"）。
# ─────────────────────────────────────────────────────────
_FLAGSHIP_ID: uuid.UUID | None = None


@pytest.fixture(autouse=True, scope="session")
def _seed_spaces() -> None:
    """建库时把四个知识版本补齐，并记住 flagship 的 id 给填充器用。"""
    import asyncio

    from copilot import spaces

    async def go() -> uuid.UUID:
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with maker() as s:
                await spaces.ensure_seeded(s)
                return await spaces.default_id(s)
        finally:
            await eng.dispose()

    global _FLAGSHIP_ID
    _FLAGSHIP_ID = asyncio.run(go())


@event.listens_for(Document, "before_insert", propagate=True)
@event.listens_for(Chunk, "before_insert", propagate=True)
@event.listens_for(Conversation, "before_insert", propagate=True)
def _fill_space(mapper, connection, target) -> None:  # noqa: ARG001 - SQLAlchemy 的签名
    if getattr(target, "knowledge_space_id", None) is None:
        target.knowledge_space_id = _FLAGSHIP_ID


def flagship_space_id() -> uuid.UUID:
    """旗舰版的 id，**给不能用夹具的地方**（同步的工厂函数）用。

    夹具版本在下面。两个入口取的是同一个值。
    """
    assert _FLAGSHIP_ID is not None, "_seed_spaces 没跑到"
    return _FLAGSHIP_ID


@pytest.fixture
def flagship_id() -> uuid.UUID:
    """旗舰版的 id。要显式指定空间的测试用它。"""
    assert _FLAGSHIP_ID is not None, "_seed_spaces 没跑到"
    return _FLAGSHIP_ID


@pytest.fixture
async def other_space(maker) -> KnowledgeSpace:
    """另一个**活着的**知识版本，用来验跨空间隔离。

    用 `enterprise_desktop`：种子里它是 inactive（语料还没导入），
    这里只把它取出来，不改状态——隔离测试要的是「另一个空间」，
    和它能不能被用户选中无关。
    """
    from copilot import spaces

    async with maker() as s:
        return await spaces.by_code(s, spaces.ENTERPRISE_DESKTOP)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_shared_engine():
    """每道测试跑完，把**模块级**那个引擎的连接池倒空。

    ⭐⭐ **这是文件头那条规矩唯一漏掉的地方。**
    头上写着「测试一律用 NullPool 的独立引擎，用完即弃」，而
    `copilot.db.session.engine` 是模块级的、带一个真的连接池
    （`pool_size=5, max_overflow=5`）——凡是走 `SessionLocal` 的代码
    （`copilot spaces` / `mcp_server.call_tool` / CLI 那几条）都用的是它。

    表现极其难查：一道测试用完把连接还回池子，池子把它留给下一道测试，
    而下一道测试跑在**另一个事件循环**里，asyncpg 于是在
    `_cancel_current_command` 里撞上 `RuntimeError: Event loop is closed`。
    ⚠️ **单跑那道测试必过，混在一起才红**——和 E1 那次
    （`test_first_person_question_keeps_public_material`）是同一类故障，
    只是共享的东西从"开发库里的语料"换成了"连接池里的连接"。

    ⚠️ 代价：走 `SessionLocal` 的测试每次要重连一次。实测整份套件几乎无感
    （绝大多数测试用的是上面那个 NullPool 夹具，池子本来就是空的）。
    """
    yield
    from copilot.db.session import engine as shared

    await shared.dispose()


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def api_client(engine, maker):
    """打 ASGI 的 httpx 客户端，会自动带 cookie——正好用来验登录态。

    `get_session` 换成测试引擎（NullPool），理由同上：每个测试一个事件循环，
    共用连接池必崩。
    """
    from httpx import ASGITransport, AsyncClient

    from copilot.api.app import app
    from copilot.db.session import get_session

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────
# 聊天相关的夹具
#
# 放这里而不是某个测试文件里：test_api_chat 和 test_multiturn 都要用，
# 而**夹具不能跨模块 import**（pytest 靠名字收集）。
# 假 provider 那几个类在 chat_helpers.py，见那边的文件头。
# ─────────────────────────────────────────────────────────

from chat_helpers import PASSWORD, FakeEmbedder, FakeLLM, TopOneReranker  # noqa: E402


@pytest.fixture
async def public_chunk(maker):
    """往公共库塞一篇文档，让检索有东西可召回。"""
    tag = uuid.uuid4().hex[:8]
    title = f"电子面单设置指南-{tag}"
    body = f"打印电子面单前先在设置里绑定物流账号-{tag}"

    async with maker() as s:
        doc = Document(
            owner_id=None,
            source_type="yuque",
            title=title,
            source_url="https://www.yuque.com/wdterpqjb/test",
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
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=title,
                heading="绑定物流账号",
                source_url="https://www.yuque.com/wdterpqjb/test",
            )
        )
        await s.commit()
        doc_id = doc.id

    yield title, body

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


@pytest.fixture
def fake_providers(monkeypatch, maker):
    """把 provider 和会话工厂都换成测试用的。

    chat 路由在流里自己开会话（StreamingResponse 的响应体在依赖退出之后才被消费），
    所以这里得连 `SessionLocal` 一起换掉，否则流里那部分会打到真实连接池上。

    ⚠️ **`trace` 那个模块也有自己的 `SessionLocal`，同样要换**（M11 P1）。
    它是**故意**自己开会话的（流里那个 session 可能已经因为取消而半死），
    代价就是这里要多换一处。漏了的话，症状是这样的：
    答案照常出来、测试也不报错，只有 journal 里多一行
    「写 request_trace 失败：Event loop is closed」——因为它打到了
    上一个用例留下的、事件循环已经关掉的真实连接池上。
    台账那边刻意「失败只记日志」，于是这个漏接线**不会让任何用例变红**，
    只会让所有关于 trace 的断言查不到行。
    """
    from copilot.api import providers
    from copilot.api import trace as trace_module
    from copilot.api.routes import chat as chat_module

    llm = FakeLLM("先绑定物流账号[1]，再打印面单。")
    monkeypatch.setattr(providers, "get_embedder", FakeEmbedder)
    monkeypatch.setattr(providers, "get_reranker", TopOneReranker)
    monkeypatch.setattr(providers, "get_llm", lambda: llm)
    monkeypatch.setattr(chat_module, "SessionLocal", maker)
    monkeypatch.setattr(trace_module, "SessionLocal", maker)
    return llm


@pytest.fixture
async def logged_in(api_client, maker):
    """注册一个用户并保持登录态；结束时把它连同会话记录一起删干净。"""
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)

    email = f"chat-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": code},
    )
    assert r.status_code == 201, r.text
    user_id = uuid.UUID(r.json()["id"])

    yield user_id

    async with maker() as s:
        convs = list(
            (
                await s.execute(select(Conversation.id).where(Conversation.user_id == user_id))
            ).scalars()
        )
        if convs:
            await s.execute(delete(Message).where(Message.conversation_id.in_(convs)))
            await s.execute(delete(Conversation).where(Conversation.id.in_(convs)))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


@pytest.fixture
def no_space_filler():
    """临时摘掉上面那个自动补 flagship 的填充器。

    ⭐⭐ **给「验证生产代码自己写了知识版本」的测试用。**

    填充器省掉了二十几处夹具的手写空间，代价写在它自己的注释里：
    **生产代码忘了传空间，测试也不会红**。2026-08-24 这个代价真的兑现了——
    上传接口从 M14-A 上线起每一次都 500（`documents.knowledge_space_id`
    是 NOT NULL，而那条路径从来没写过它），而全套测试一直是绿的，
    因为填充器替它补上了。

    所以凡是「这条写入路径自己必须写空间」的断言，都要挂上这个夹具。
    """
    from sqlalchemy import event as _event

    _event.remove(Document, "before_insert", _fill_space)
    _event.remove(Chunk, "before_insert", _fill_space)
    _event.remove(Conversation, "before_insert", _fill_space)
    try:
        yield
    finally:
        _event.listen(Document, "before_insert", _fill_space, propagate=True)
        _event.listen(Chunk, "before_insert", _fill_space, propagate=True)
        _event.listen(Conversation, "before_insert", _fill_space, propagate=True)
