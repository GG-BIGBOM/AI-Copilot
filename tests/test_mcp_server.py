"""MCP server（W3.1）。

⭐ **这一节里真正重要的只有一件事：身份不是工具入参。**

MCP server 极容易做成一个无鉴权的私有文档读取口。`agent/deps.py` 那条红线
（`user_id` / `space_id` 只能从登录态来，绝不能变成工具签名的一部分）
在这里要**再守一遍**，因为这是一个全新的入口，而它长得和 Agent 完全不一样。

其余几道题守的是"MCP 是个外壳、不是第二套 RAG"：
隔离、拒答时不挂来源、解析不出身份就退出。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from copilot.db.models import Chunk, Document, User
from copilot.mcp_server import TOOL_SPECS, Identity, call_tool, resolve_identity

DIM = 1024


def _vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for i, ch in enumerate(text[:64]):
        v[(ord(ch) * 7 + i) % DIM] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


@pytest.fixture
async def two_users(maker, flagship_id):
    """两个人各传一份文档。隔离题要的最小配置。"""
    tag = uuid.uuid4().hex[:8]
    ids: list[uuid.UUID] = []
    doc_ids: list[uuid.UUID] = []
    async with maker() as s:
        for who in ("a", "b"):
            u = User(email=f"mcp-{who}-{tag}@t.local", password_hash="x")
            s.add(u)
            await s.flush()
            ids.append(u.id)
            d = Document(
                owner_id=u.id,
                source_type="upload",
                title=f"{who.upper()}家实施约定-{tag}",
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
                knowledge_space_id=flagship_id,
            )
            s.add(d)
            await s.flush()
            doc_ids.append(d.id)
            body = f"{who.upper()} 家的对账规则-{tag}：按整体发货不拆"
            s.add(
                Chunk(
                    document_id=d.id,
                    owner_id=u.id,
                    ordinal=0,
                    content=body,
                    embedding=_vec(body),
                    title=d.title,
                    knowledge_space_id=flagship_id,
                )
            )
        await s.commit()

    yield ids, tag

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
        await s.execute(delete(Document).where(Document.id.in_(doc_ids)))
        await s.execute(delete(User).where(User.id.in_(ids)))
        await s.commit()


# ═══════════════ 一、红线：身份不许出现在工具签名里 ═══════════════


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=[t["name"] for t in TOOL_SPECS])
def test_no_tool_takes_an_identity_parameter(spec):
    """⚠️⚠️ **这道题是这个文件存在的理由。**

    凡是能出现在工具签名里的东西，一句 prompt injection 就能让模型去填。
    `user_id` 变成入参 = 任何人都能读任何人的私有文档；
    `space` 变成入参 = 一句话就能把提问切到另一版 ERP 的材料上，
    而答案会写得和真的一样确定。

    判据写成"名字里带这些字的参数一个都不许有"而不是"参数必须正好是那几个"：
    后者在加一个正当参数（比如 `top_k`）时会误报，而误报多了这道题就会被删。
    """
    banned = ("user", "owner", "space", "tenant", "account", "email")
    for name in spec["inputSchema"].get("properties", {}):
        assert not any(b in name.lower() for b in banned), (
            f"{spec['name']} 的入参 {name!r} 长得像身份——身份只能来自启动参数"
        )


def test_identity_is_frozen():
    """工具处理函数拿到的是同一个对象。可变的话，一个写错的处理函数
    就能改掉下一次调用的身份——而那种越权没有任何症状。"""
    ident = Identity(user_id=None, email="", space_id=uuid.uuid4(), space_code="flagship")
    with pytest.raises(Exception):  # noqa: B017 - dataclass 冻结抛的是 FrozenInstanceError
        ident.user_id = uuid.uuid4()  # type: ignore[misc]


# ═══════════════ 二、启动时解析身份：解析不出来就退出 ═══════════════


async def test_a_typo_in_the_email_exits_instead_of_falling_back(maker):
    """⚠️ **不能退回"匿名 + 默认空间"。**

    退回去的表现是：一个拼错了邮箱的人拿到一个能用的 MCP server，
    问什么都答得出来（公共库照样能检索），于是他以为自己连上了自己的账号——
    直到某天发现"我传的文档一份都搜不到"。
    """
    async with maker() as s:
        with pytest.raises(SystemExit):
            await resolve_identity(s, "nobody-" + uuid.uuid4().hex + "@t.local", "flagship")


async def test_an_unknown_space_exits(maker):
    async with maker() as s:
        with pytest.raises(SystemExit):
            await resolve_identity(s, "", "no-such-space")


async def test_an_inactive_space_exits(maker):
    """语料还没导入的空间，连上去只会得到「知识库暂无此内容」，
    而用户会以为是系统坏了（同 `spaces activate` 那道闸门）。"""
    async with maker() as s:
        with pytest.raises(SystemExit):
            await resolve_identity(s, "", "enterprise_desktop")


async def test_a_disabled_account_cannot_connect(maker, two_users):
    ids, _ = two_users
    async with maker() as s:
        user = await s.get(User, ids[0])
        user.is_active = False
        await s.commit()
        email = user.email
    async with maker() as s:
        with pytest.raises(SystemExit):
            await resolve_identity(s, email, "flagship")


async def test_anonymous_mode_is_allowed_but_has_no_private_docs(maker, flagship_id):
    async with maker() as s:
        ident = await resolve_identity(s, "", "flagship")
    assert ident.user_id is None
    assert ident.space_id == flagship_id
    assert "匿名" in await call_tool("my_documents", {}, ident)


# ═══════════════ 三、隔离：换个身份就看不到别人的东西 ═══════════════


async def test_my_documents_is_scoped_to_the_connected_account(maker, two_users, flagship_id):
    ids, tag = two_users
    async with maker() as s:
        a = await resolve_identity(s, (await s.get(User, ids[0])).email, "flagship")
        b = await resolve_identity(s, (await s.get(User, ids[1])).email, "flagship")

    out_a = await call_tool("my_documents", {}, a)
    out_b = await call_tool("my_documents", {}, b)
    assert f"A家实施约定-{tag}" in out_a
    assert f"B家实施约定-{tag}" not in out_a, f"泄漏了别人的文档：{out_a}"
    assert f"B家实施约定-{tag}" in out_b
    assert f"A家实施约定-{tag}" not in out_b


async def test_an_unknown_tool_name_explains_itself(maker, flagship_id):
    """⚠️ 未知工具名要说清楚，不能抛异常：MCP 客户端会把异常显示成一句
    「服务器错误」，而真实原因往往是客户端缓存了一份旧的工具清单。"""
    async with maker() as s:
        ident = await resolve_identity(s, "", "flagship")
    out = await call_tool("delete_everything", {}, ident)
    assert "没有名为" in out
    for spec in TOOL_SPECS:
        assert spec["name"] in out


async def test_empty_arguments_do_not_blow_up(maker, flagship_id):
    async with maker() as s:
        ident = await resolve_identity(s, "", "flagship")
    assert "空的" in await call_tool("search_kb", {}, ident)
    assert "空的" in await call_tool("answer_kb", {"question": "  "}, ident)
