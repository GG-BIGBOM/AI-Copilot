"""聊天页的知识版本选择（M18 / W3.3 后端那一半）。

四条规矩，每一条都对应一种**没有症状**的错法：

    不传        → 默认版本。⚠️ 老前端不带这个字段，不能因此 422
    传对        → 新会话钉在那一版
    传错/未启用 → **400，绝不退回默认值**。退回去的话，一次拼错会静静地
                  把提问送进旗舰版，而用户以为自己在问企业版
    已有会话    → 一律按会话原有的版本走，传什么都不改

⚠️ 第三条还要求**在开流之前**就报错：流一旦开始，HTTP 状态码已经发出去了，
再想返回 400 也来不及——只能在流里塞一个 error 片段，
而那对客户端（尤其是脚本）来说完全是另一回事。和配额检查同一个理由。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import ask, parts
from sqlalchemy import select

from copilot import spaces
from copilot.db.models import Conversation


async def _conv_space(maker, conv_id: str) -> str:
    from copilot.db.models import KnowledgeSpace

    async with maker() as s:
        sid = (
            await s.execute(
                select(Conversation.knowledge_space_id).where(
                    Conversation.id == uuid.UUID(conv_id)
                )
            )
        ).scalar_one()
        return (await s.get(KnowledgeSpace, sid)).code


def _conv_id(response) -> str:
    return next(p for p in parts(response.text) if p["type"] == "data-conversation")["data"]["id"]


@pytest.fixture
async def a_second_selectable_space(maker):
    """临时启用第二个可选版本，跑完还原。

    ⚠️ **不能 `skip` 了事。** 种子里企业版是 `inactive`，直接跑的话
    「传对了要钉在那一版」和「已有会话不许改版本」两道题会双双 skip——
    而它们守的正是 M18 最贵的那条规则。一道**永远被跳过**的测试，
    和没有这道测试是一回事，但它看起来像有。

    用 `enterprise_web`：它 0 篇文档、0 块，启用它不会改变任何检索行为。
    """
    code = spaces.ENTERPRISE_WEB
    async with maker() as s:
        sp = await spaces.by_code(s, code)
        was = sp.status
        sp.status = "active"
        await s.commit()
    try:
        yield code
    finally:
        async with maker() as s:
            sp = await spaces.by_code(s, code)
            sp.status = was
            await s.commit()


async def test_no_space_field_still_works(api_client, logged_in, public_chunk, fake_providers):
    """⚠️ 老前端不带 `space`。它**不能因此 422**——和 `mode` 同一个处理法。"""
    _, body = public_chunk
    r = await ask(api_client, body)
    assert r.status_code == 200


async def test_no_space_field_lands_in_the_default_version(
    api_client, logged_in, public_chunk, fake_providers, maker
):
    _, body = public_chunk
    r = await ask(api_client, body)
    assert await _conv_space(maker, _conv_id(r)) == spaces.DEFAULT


async def test_an_unknown_space_is_rejected_before_the_stream_starts(
    api_client, logged_in, public_chunk, fake_providers
):
    """⚠️⚠️ **400，而且是在开流之前。**

    退回默认值的后果：一次拼错的 code 静静地把提问送进旗舰版，
    而用户以为自己在问企业版——那种错误没有任何症状，
    而且答案会写得和真的一样确定。

    ⭐ 判据里那句「在开流之前」是有分量的：`StreamingResponse` 一旦开始，
    状态码就发出去了，再想返回 400 只能在流里塞一个 error 片段。
    所以这里断言的是 `status_code`，不是流里的内容。
    """
    _, body = public_chunk
    r = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
            "space": "enterprise_desktp",  # 少一个 o
        },
    )
    assert r.status_code == 400
    assert "知识版本" in r.text


async def test_an_inactive_space_is_rejected_too(
    api_client, logged_in, public_chunk, fake_providers
):
    """⚠️ 表建好了不等于语料导进来了。

    放行一个 inactive 的空间，用户问什么都得到「知识库暂无此内容」，
    而他会以为是系统坏了。判据和 `/api/knowledge-spaces` 那个列表**同一条**
    （`spaces.selectable`），否则会出现"列表里没有、传上来却能用"的空间。
    """
    _, body = public_chunk
    r = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
            "space": spaces.ENTERPRISE_DESKTOP,
        },
    )
    assert r.status_code == 400


async def test_a_valid_space_pins_the_new_conversation(
    api_client, logged_in, public_chunk, fake_providers, maker, a_second_selectable_space
):
    """能选的那几个里挑一个，新会话就钉在那一版。"""
    _, body = public_chunk
    other = a_second_selectable_space
    r = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
            "space": other,
        },
    )
    assert r.status_code == 200
    assert await _conv_space(maker, _conv_id(r)) == other


async def test_an_existing_conversation_never_changes_version(
    api_client, logged_in, public_chunk, fake_providers, maker, a_second_selectable_space
):
    """⚠️⚠️ **已有会话的版本钉死，传什么都不改。**

    允许改的后果是：前几轮的答案来自另一个产品，而对话看起来完全连贯。
    用户回头翻这条会话，分不出哪几句是哪一版的。

    ⚠️ 而且**不报错**——报错会让一个正常的追问失败，
    而正确做法（新建会话）前端自己就能做。
    """
    _, body = public_chunk
    first = await ask(api_client, body)
    conv = _conv_id(first)
    assert await _conv_space(maker, conv) == spaces.DEFAULT

    other = a_second_selectable_space
    again = await api_client.post(
        "/api/chat",
        json={
            "id": conv,
            "messages": [{"role": "user", "parts": [{"type": "text", "text": body}]}],
            "space": other,
        },
    )
    assert again.status_code == 200, "换版本不该让一个正常的追问失败"
    assert await _conv_space(maker, conv) == spaces.DEFAULT, "已有会话的版本被改掉了"


async def test_the_listing_and_the_accepted_set_are_the_same(api_client, logged_in, maker):
    """⭐ 列表接口和 `/api/chat` 的白名单必须是同一条判据。

    两处各判一次的表现是：某个版本在下拉框里看不见，但手工 POST 能用
    （或者反过来，列表里有、传上去 400）。两种都会让人怀疑是自己看错了。
    """
    listed = {row["code"] for row in (await api_client.get("/api/knowledge-spaces")).json()}
    async with maker() as s:
        allowed = {sp.code for sp in await spaces.selectable(s)}
    assert listed == allowed
