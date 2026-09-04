"""文档勘误的审核流程：**提交 ≠ 修改公共事实**。

这一族守的是一条产品规则和一条安全规则，它们碰巧是同一句话：

    任何登录用户都可以**提交**勘误
    只有管理员发布之后，它才**影响公共知识库**

这条路走过两个都不对的极端，两次都在这个文件里留了反向用例：

    ~2026-09-02   `CurrentUser` + 保存即重新入库
                  任何拿到邀请码的人都能把一篇语雀原文整个换掉，
                  当场对全站生效、无人审核，还能删掉别人写的勘误
    2026-09-03    收紧成 `CurrentAdmin`
                  安全了，但把用户挡在了纠错之外

⭐⭐ **最要紧的那几道是「生效」那一组**：它们直接查
`ingest.corrections.load_db_corrections`——RAG 真正读的那个函数。
只验接口返回 403/409 是不够的：审核状态加在表上、而入库那一路忘了过滤的话，
接口全绿、审核队列照常显示 pending，**而公共知识库照样被那条 pending 改掉**。
那种失败没有任何症状。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import PASSWORD
from sqlalchemy import delete, select

from copilot import corrections_flow as flow
from copilot.auth.invites import create_invite_codes
from copilot.auth.security import create_access_token
from copilot.db.models import Correction, InviteCode, User
from copilot.ingest.corrections import load_db_corrections

URL = "https://www.yuque.com/wdterpqjb/review/case"
URL2 = "https://www.yuque.com/wdterpqjb/review/case-2"


@pytest.fixture
def no_reingest(monkeypatch):
    """把重新入库换成「成功，3 个片段」。

    ⚠️ 真的那个要扫 786 篇语雀原文再跑一次 embedding。**这里换掉它是安全的**：
    这一族验的是「什么状态下**会不会调**它」和「入库那一路读到了什么」，
    不是入库本身对不对。
    """
    from copilot.api.routes import corrections as mod

    calls: list[str] = []

    async def fake(session, target_url):
        calls.append(target_url)
        return 3

    monkeypatch.setattr(mod, "reingest_one", fake)
    monkeypatch.setattr(mod, "_reingest_one", fake)
    return calls


async def _register(api_client, maker) -> tuple[uuid.UUID, dict, str]:
    """建一个普通用户，返回 (id, Bearer 头, 邀请码)。

    ⚠️ 用 Bearer 而不是 cookie：`api_client` 的罐子是共享的，而
    `extract_token` **先看 cookie 再看 Authorization**——不清干净的话，
    「A 看不到 B 的」这类断言可能是同一个人跑出来的。
    """
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    email = f"rev-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "inviteCode": code}
    )
    assert r.status_code == 201, r.text
    uid = uuid.UUID(r.json()["id"])
    return uid, {"Authorization": f"Bearer {create_access_token(uid)}"}, code


@pytest.fixture
async def people(api_client, maker):
    """两个普通用户 + 一个管理员，收尾时把他们和他们的勘误全清掉。"""
    api_client.cookies.clear()
    alice, alice_h, code_a = await _register(api_client, maker)
    bob, bob_h, code_b = await _register(api_client, maker)
    root, root_h, code_r = await _register(api_client, maker)
    async with maker() as s:
        (await s.get(User, root)).is_admin = True
        await s.commit()
    api_client.cookies.clear()

    yield {
        "alice": (alice, alice_h),
        "bob": (bob, bob_h),
        "admin": (root, root_h),
    }

    async with maker() as s:
        await s.execute(delete(Correction).where(Correction.target_url.in_([URL, URL2])))
        await s.execute(delete(InviteCode).where(InviteCode.code.in_([code_a, code_b, code_r])))
        await s.execute(delete(User).where(User.id.in_([alice, bob, root])))
        await s.commit()


def _payload(url: str = URL, **over) -> dict:
    body = {
        "target_url": url,
        "title": "打印位置",
        "reason": "原文写的上限是 100，实际是 300，已和产品确认",
        "body": "# 正确的内容\n\n操作位置：设置-打印设置。",
    }
    body.update(over)
    return body


async def _submit(api_client, headers, url: str = URL, **over) -> str:
    r = await api_client.post("/api/corrections", json=_payload(url, **over), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["correction"]["id"]


async def _publish(api_client, admin_h, cid: str) -> None:
    assert (
        await api_client.post(
            f"/api/admin/doc-corrections/{cid}/review",
            json={"decision": "approve", "note": "看过了"},
            headers=admin_h,
        )
    ).status_code == 200
    r = await api_client.post(
        f"/api/admin/doc-corrections/{cid}/publish", json={}, headers=admin_h
    )
    assert r.status_code == 200, r.text


# ─────────────────────────── 一、权限 ───────────────────────────


async def test_a_user_can_submit_and_see_only_their_own_pending(api_client, people, no_reingest):
    """A 提的待审勘误，B 看不到。

    ⭐ 一条 pending 是「某人的意见」，不是公共事实。摆在公共列表上，
    别人会以为它已经生效了——而它随时可能被作者改掉或撤回。
    """
    _, alice_h = people["alice"]
    _, bob_h = people["bob"]
    cid = await _submit(api_client, alice_h)

    mine = (await api_client.get("/api/corrections/mine", headers=alice_h)).json()
    assert [c["id"] for c in mine] == [cid]

    theirs = (await api_client.get("/api/corrections", headers=bob_h)).json()
    assert cid not in [c["id"] for c in theirs], "B 看到了 A 还没审的提交"
    assert (await api_client.get(f"/api/corrections/{cid}", headers=bob_h)).status_code == 404


async def test_a_user_cannot_edit_or_withdraw_someone_elses(api_client, people, no_reingest):
    """B 改不了、也撤不掉 A 的提交。**404 不是 403**——403 等于确认这个 id 是真的。"""
    _, alice_h = people["alice"]
    _, bob_h = people["bob"]
    cid = await _submit(api_client, alice_h)

    r = await api_client.patch(
        f"/api/corrections/{cid}", json={"body": "# 我改的"}, headers=bob_h
    )
    assert r.status_code == 404
    r = await api_client.patch(
        f"/api/corrections/{cid}", json={"action": "withdraw"}, headers=bob_h
    )
    assert r.status_code == 404


async def test_a_plain_user_cannot_review_or_publish(api_client, people, no_reingest):
    """普通用户碰不到审核和发布。**这两个接口是公共知识库唯一的入口。**"""
    _, alice_h = people["alice"]
    cid = await _submit(api_client, alice_h)

    for path, body in (
        (f"/api/admin/doc-corrections/{cid}/review", {"decision": "approve"}),
        (f"/api/admin/doc-corrections/{cid}/publish", {}),
    ):
        r = await api_client.post(path, json=body, headers=alice_h)
        assert r.status_code == 403, f"{path} 放行了普通用户"

    assert (
        await api_client.get("/api/admin/doc-corrections", headers=alice_h)
    ).status_code == 403


async def test_a_plain_user_cannot_retire_a_published_one(api_client, people, no_reingest):
    """已经生效的东西不该由提交人单方面收回——那时它已经是公共事实了。"""
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)
    await _publish(api_client, admin_h, cid)

    assert (await api_client.delete(f"/api/corrections/{cid}", headers=alice_h)).status_code == 403


async def test_two_users_may_both_submit_for_the_same_doc(api_client, people, no_reingest, maker):
    """⭐ 两个人给同一篇各提一条待审的，是完全正常的事。

    原来 `target_url` 是**全表**唯一，接口做 upsert——在提交队列下那等于
    「后一个人**改写**前一个人还没审的意见」。唯一性现在收窄成
    「同一篇最多一条**已发布**」。
    """
    _, alice_h = people["alice"]
    _, bob_h = people["bob"]
    a = await _submit(api_client, alice_h, body="# A 的说法")
    b = await _submit(api_client, bob_h, body="# B 的说法")
    assert a != b

    async with maker() as s:
        rows = list(
            (await s.execute(select(Correction).where(Correction.target_url == URL))).scalars()
        )
    assert len(rows) == 2
    assert {r.status for r in rows} == {"pending"}


# ─────────────────────── 二、什么时候才进 RAG ───────────────────────
#
# ⭐⭐ 这一组直接问 `load_db_corrections`——**RAG 真正读的那个函数**。
# 只验接口状态码是不够的：状态加在表上而入库那一路忘了过滤的话，
# 接口全绿、队列照常显示 pending，而公共库照样被改掉。


async def _live_urls(maker) -> set[str]:
    async with maker() as s:
        return set(await load_db_corrections(s))


async def test_pending_never_reaches_the_knowledge_base(api_client, people, no_reingest, maker):
    """pending：**一个字都不进 RAG，也不许触发重新入库。**"""
    _, alice_h = people["alice"]
    await _submit(api_client, alice_h)

    assert URL not in await _live_urls(maker)
    assert no_reingest == [], "提交那一刻碰了公共知识库"


async def test_approved_but_not_published_still_does_not_reach_it(
    api_client, people, no_reingest, maker
):
    """⭐ approved 也还不算数。

    `approved` 是「管理员看过了、认可这个内容」，`published` 才是
    「它现在真的在影响所有人的答案」。合成一步的话，发布出问题时
    你分不清是"审得不对"还是"入库这一步炸了"。
    """
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)

    r = await api_client.post(
        f"/api/admin/doc-corrections/{cid}/review",
        json={"decision": "approve", "note": "内容没问题"},
        headers=admin_h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    assert URL not in await _live_urls(maker)
    assert no_reingest == [], "审核通过那一步就入库了"


async def test_published_is_what_finally_reaches_it(api_client, people, no_reingest, maker):
    """published：这一刻才生效，而且要**当场**重新入库。

    只改状态不入库的话，管理员点完发布再问同一个问题，答案一个字都不会变——
    他只会以为发布是假的。
    """
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)
    await _publish(api_client, admin_h, cid)

    assert URL in await _live_urls(maker)
    assert no_reingest == [URL], "发布之后没有重新入库"


async def test_rejected_and_withdrawn_never_reach_it(api_client, people, no_reingest, maker):
    """被拒绝的、被撤回的，都不进 RAG。"""
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]

    a = await _submit(api_client, alice_h)
    r = await api_client.post(
        f"/api/admin/doc-corrections/{a}/review",
        json={"decision": "reject", "note": "和产品确认过，原文是对的"},
        headers=admin_h,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"

    b = await _submit(api_client, alice_h, url=URL2)
    r = await api_client.patch(
        f"/api/corrections/{b}", json={"action": "withdraw"}, headers=alice_h
    )
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"

    live = await _live_urls(maker)
    assert URL not in live and URL2 not in live


async def test_retiring_takes_it_back_out_of_the_knowledge_base(
    api_client, people, no_reingest, maker
):
    """撤销之后那一篇回到语雀原文，而且**记录还在**。

    ⭐ 还原不需要第二条路径：`reingest_one` 读的是**已发布**的勘误，
    这一条一旦不在 published 里，重新入库拿到的自然就是语雀原文。
    """
    _, alice_h = people["alice"]
    admin_id, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)
    await _publish(api_client, admin_h, cid)
    assert URL in await _live_urls(maker)

    assert (await api_client.delete(f"/api/corrections/{cid}", headers=admin_h)).status_code == 204
    assert URL not in await _live_urls(maker)
    assert no_reingest == [URL, URL], "撤销之后没有重新入库，库里留着的还是改过的内容"

    async with maker() as s:
        row = await s.get(Correction, uuid.UUID(cid))
    assert row is not None, "被物理删掉了——历史没了"
    assert row.status == flow.RETIRED
    assert row.published_at is not None, "published_at 被清空了：它曾经生效过这件事没了"
    assert row.reviewed_by == admin_id


async def test_publishing_a_second_one_supersedes_the_first(
    api_client, people, no_reingest, maker
):
    """同一篇发布第二条时，第一条自动让位成 `superseded`。

    ⚠️ 两条同时 published 会撞上部分唯一索引 `ux_corrections_published_target`——
    **那正是它存在的意义**：同一篇有两条生效，`apply_corrections` 的行为
    就取决于字典构造顺序，而那种错的样子是「答案时好时坏」，最难查。

    ⚠️⚠️ **这道题抓出过一个真 bug（2026-09-03）。** 第一版实现把「旧的降级」
    和「新的升为 published」放在同一次 flush 里提交，而部分唯一索引是
    **逐条语句**检查的（不能 DEFERRABLE）——SQLAlchemy 先发哪条 UPDATE 不定，
    先发新的那条就当场撞索引 **500**。表现是间歇性的：随机顺序下 3 次红 2 次。
    修法是在降级之后显式 `await session.flush()`（见 `admin.publish_doc_correction`）。

    ⭐ 值得记住的是**发现方式**：它不是被想出来的，是被一道会随机变红的题
    逼出来的。当时第一反应是"共享开发库又污染了"——如果按那个结论把题改绿，
    这个生产 bug 会一直留着，而线上的表现是「同一篇发布第二条勘误偶尔失败」。
    """
    _, alice_h = people["alice"]
    _, bob_h = people["bob"]
    _, admin_h = people["admin"]

    first = await _submit(api_client, alice_h, body="# 第一版")
    await _publish(api_client, admin_h, first)
    second = await _submit(api_client, bob_h, body="# 第二版")
    await _publish(api_client, admin_h, second)

    async with maker() as s:
        rows = {
            str(r.id): r
            for r in (
                await s.execute(select(Correction).where(Correction.target_url == URL))
            ).scalars()
        }
    assert rows[first].status == flow.SUPERSEDED
    assert rows[second].status == flow.PUBLISHED
    # 让位的那条也要留着「它曾经生效过」
    assert rows[first].published_at is not None

    async with maker() as s:
        live = await load_db_corrections(s)
    assert live[URL].body == "# 第二版"


# ─────────────────────────── 三、生命周期 ───────────────────────────


async def test_pending_can_be_edited_by_its_author(api_client, people, no_reingest):
    _, alice_h = people["alice"]
    cid = await _submit(api_client, alice_h)
    r = await api_client.patch(
        f"/api/corrections/{cid}", json={"body": "# 改过的正文", "reason": "又问了一遍产品"},
        headers=alice_h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "# 改过的正文"
    assert r.json()["status"] == "pending"


async def test_a_published_one_can_no_longer_be_edited(api_client, people, no_reingest):
    """⭐ 发布之后作者不能再改内容。

    能改的话，管理员看过的和线上生效的就不是同一段文字——那等于没有审核。
    """
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)
    await _publish(api_client, admin_h, cid)

    r = await api_client.patch(
        f"/api/corrections/{cid}", json={"body": "# 偷偷改掉"}, headers=alice_h
    )
    assert r.status_code == 409, r.text


async def test_an_illegal_transition_is_refused(api_client, people, no_reingest):
    """没审过就发布，直接 409。状态机只有一份，接口都来查它。"""
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)

    r = await api_client.post(
        f"/api/admin/doc-corrections/{cid}/publish", json={}, headers=admin_h
    )
    assert r.status_code == 409, r.text


async def test_the_optimistic_lock_stops_a_stale_review(api_client, people, no_reingest):
    """拿着旧版本号来审核的那个必须失败，而不是默默覆盖前一个人的结论。"""
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)

    r = await api_client.post(
        f"/api/admin/doc-corrections/{cid}/review",
        json={"decision": "approve", "version": 999},
        headers=admin_h,
    )
    assert r.status_code == 409


# ─────────────────────────── 四、审计 ───────────────────────────


async def test_every_step_leaves_who_and_when(api_client, people, no_reingest, maker):
    """⭐⭐ 走完一整轮之后，「谁提的、谁审的、什么时候、为什么」一个都不能丢。

    这些字段是这条流程存在的**理由**本身：半年后要能回答
    「公共库里这段话是怎么来的」。
    """
    alice_id, alice_h = people["alice"]
    admin_id, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)

    async with maker() as s:
        row = await s.get(Correction, uuid.UUID(cid))
        assert row.author_id == alice_id, "submitted_by 丢了"
        assert row.created_at is not None
        assert row.status == flow.PENDING
        assert row.reviewed_by is None and row.published_at is None

    await api_client.post(
        f"/api/admin/doc-corrections/{cid}/review",
        json={"decision": "approve", "note": "和产品确认过，上限确实是 300"},
        headers=admin_h,
    )
    async with maker() as s:
        row = await s.get(Correction, uuid.UUID(cid))
        assert row.reviewed_by == admin_id
        assert row.reviewed_at is not None
        assert "300" in (row.review_note or ""), "review_note 丢了"
        assert row.published_at is None, "还没发布就写了 published_at"

    await api_client.post(f"/api/admin/doc-corrections/{cid}/publish", json={}, headers=admin_h)
    async with maker() as s:
        row = await s.get(Correction, uuid.UUID(cid))
        assert row.status == flow.PUBLISHED
        assert row.published_at is not None
        assert row.author_id == alice_id, "发布把提交人覆盖掉了"


async def test_admin_queue_only_shows_pending_by_default(api_client, people, no_reingest):
    """审核队列默认只看 pending——那是唯一需要人动手的一档。"""
    _, alice_h = people["alice"]
    _, admin_h = people["admin"]
    cid = await _submit(api_client, alice_h)

    q = (await api_client.get("/api/admin/doc-corrections", headers=admin_h)).json()
    assert cid in [c["id"] for c in q["items"]]
    assert [c["author_email"] for c in q["items"] if c["id"] == cid][0] is not None

    await _publish(api_client, admin_h, cid)
    q = (await api_client.get("/api/admin/doc-corrections", headers=admin_h)).json()
    assert cid not in [c["id"] for c in q["items"]]
    q = (await api_client.get("/api/admin/doc-corrections?status=all", headers=admin_h)).json()
    assert cid in [c["id"] for c in q["items"]]


async def test_a_bad_status_filter_is_422_not_an_empty_list(api_client, people, no_reingest):
    """拼错一个字母要报错，不能静默返回空列表——那会让人以为"没有待审的"。"""
    _, admin_h = people["admin"]
    r = await api_client.get("/api/admin/doc-corrections?status=pendign", headers=admin_h)
    assert r.status_code == 422


# ─────────────────────── 五、两种纠错共用同一套原则 ───────────────────────


def test_both_flows_share_one_definition_of_effective():
    """⭐ 「什么状态才算生效」只有一份定义。

    两条发布路径读的是同一个 `flow.LIVE`：
        文档勘误   `ingest.corrections.load_db_corrections` 的 where
        答案纠错   `verified.publish_correction` 只在这一步建索引
    各写一份字面量的话，改口径时漏掉一处**没有任何症状**。
    """
    assert flow.LIVE == (flow.PUBLISHED,)

    import inspect

    from copilot.ingest import corrections as ing

    assert "flow.LIVE" in inspect.getsource(ing.load_db_corrections)


def test_the_two_state_machines_agree_on_the_review_half():
    """两张迁移表在**审核那一半**必须逐字相同，只在 published 之后分岔。

    共用的是审核原则；分岔是因为文档勘误这条记录**自己就是**生效的那个东西，
    没有第二个对象可退役（答案纠错退役的是它产出的 VerifiedAnswer）。
    """
    shared = (flow.PENDING, flow.APPROVED, flow.REJECTED, flow.WITHDRAWN)
    for st in shared:
        assert flow.STATE_MACHINE[st] == flow.DOC_STATE_MACHINE[st], st

    assert flow.STATE_MACHINE[flow.PUBLISHED] == ()
    assert set(flow.DOC_STATE_MACHINE[flow.PUBLISHED]) == {flow.RETIRED, flow.SUPERSEDED}


def test_the_doc_machine_is_not_reachable_by_accident():
    """⚠️ 忘了传 `DOC_STATE_MACHINE` 的表现是「已发布的勘误撤销不了」。

    这道题把那个失败形态钉下来：默认那张表**不允许** published → retired。
    """
    with pytest.raises(flow.TransitionError):
        flow.check_transition(flow.PUBLISHED, flow.RETIRED)
    flow.check_transition(flow.PUBLISHED, flow.RETIRED, flow.DOC_STATE_MACHINE)
