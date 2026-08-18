"""网页版勘误：/api/corrections。

和 `test_corrections.py`（文件那一路）分开：那边测的是 `corrections/*.md`
的解析与覆盖，这边测的是**数据库那一路**和它的接口。

⚠️ 这里一律把 `_reingest_one` 换成假的。真的那个要扫 786 篇语雀原文
再跑一次 embedding——放进单元测试就是让整套测试慢十倍、还依赖外部 API。
「重新入库真的生效了吗」由端到端手测覆盖，不在这里。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import PASSWORD
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.db.models import Correction, InviteCode, User

URL = "https://www.yuque.com/wdterpqjb/test/correction-case"


@pytest.fixture
def no_reingest(monkeypatch):
    """把重新入库换成「成功，3 个片段」。"""
    from copilot.api.routes import corrections as mod

    async def fake(session, target_url):
        return 3

    monkeypatch.setattr(mod, "_reingest_one", fake)
    return fake


@pytest.fixture
async def author(api_client, maker):
    """注册并登录一个用户，结束时把他和他写的勘误一起删掉。"""
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    email = f"corr-{uuid.uuid4().hex[:10]}@test.local"
    r = await api_client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "inviteCode": code}
    )
    assert r.status_code == 201, r.text
    user_id = uuid.UUID(r.json()["id"])

    yield user_id

    async with maker() as s:
        await s.execute(delete(Correction).where(Correction.author_id == user_id))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


def _payload(**over):
    body = {
        "target_url": URL,
        "title": "打印位置",
        "reason": "原文写的上限是 100，实际是 300，已和产品确认",
        "body": "# 正确的内容\n\n操作位置：设置-打印设置。",
    }
    body.update(over)
    return body


async def test_save_reports_whether_it_took_effect(api_client, author, no_reingest, maker):
    """⭐ 回执要如实说**生效没有**，不能只说「已保存」。

    落库了不等于生效了。只说保存成功的话，用户改完再问一遍发现答案没变，
    只会认定这个功能是假的。
    """
    r = await api_client.post("/api/corrections", json=_payload())
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["applied"] is True
    assert data["chunks"] == 3
    assert "生效" in data["note"]
    assert data["correction"]["target_url"] == URL


async def test_second_save_updates_instead_of_duplicating(api_client, author, no_reingest, maker):
    """同一篇再改一次是**更新**，不是新增。

    两条勘误指向同一篇是配置错误——ingest 那边会直接抛。让唯一约束在这里就挡住。
    """
    await api_client.post("/api/corrections", json=_payload())
    r = await api_client.post("/api/corrections", json=_payload(body="# 改第二遍"))
    assert r.status_code == 201, r.text

    async with maker() as s:
        rows = list(
            (await s.execute(select(Correction).where(Correction.target_url == URL))).scalars()
        )
    assert len(rows) == 1
    assert rows[0].body == "# 改第二遍"


async def test_reason_is_required(api_client, author, no_reingest):
    """理由必填。这是**覆盖公共知识库**的东西，说不清理由的覆盖比不覆盖更危险。"""
    r = await api_client.post("/api/corrections", json=_payload(reason=""))
    assert r.status_code == 422


async def test_target_must_be_a_link(api_client, author, no_reingest):
    """target_url 是和语雀原文对齐的唯一键，填错的表现是「保存成功但一个字都没生效」。"""
    r = await api_client.post("/api/corrections", json=_payload(target_url="打印设置那篇"))
    assert r.status_code == 422


async def test_everyone_sees_every_correction(api_client, author, no_reingest):
    """列表不按作者过滤——它们改的是同一个公共知识库，谁改了什么大家都该看见。"""
    await api_client.post("/api/corrections", json=_payload())
    r = await api_client.get("/api/corrections")
    assert r.status_code == 200
    assert any(c["target_url"] == URL for c in r.json())


async def test_delete_removes_it(api_client, author, no_reingest, maker):
    """撤销之后那一篇回到语雀原文。"""
    cid = (await api_client.post("/api/corrections", json=_payload())).json()["correction"]["id"]
    assert (await api_client.delete(f"/api/corrections/{cid}")).status_code == 204

    async with maker() as s:
        rows = list(
            (await s.execute(select(Correction).where(Correction.target_url == URL))).scalars()
        )
    assert rows == []


async def test_requires_login(api_client):
    """未登录一律 401——勘误改的是所有人都会看到的内容。"""
    assert (await api_client.post("/api/corrections", json=_payload())).status_code == 401
    assert (await api_client.get("/api/corrections")).status_code == 401


# ---------- 两路合并 ----------


def test_db_wins_over_file():
    """同一篇两边都有时，以数据库那条为准。

    理由是时序：文件那条是上一次提交时的想法，数据库那条是刚刚在网页上写的。
    """
    from pathlib import Path

    from copilot.ingest.corrections import Correction as CorrectionFile
    from copilot.ingest.corrections import merge_corrections

    from_file = {
        URL: CorrectionFile(path=Path("a.md"), target_url=URL, reason="旧", body="文件版")
    }
    from_db = {URL: CorrectionFile(path=None, target_url=URL, reason="新", body="网页版")}

    merged = merge_corrections(from_file, from_db)
    assert merged[URL].body == "网页版"


def test_db_correction_has_a_readable_name():
    """数据库那条没有文件，`.name` 不能炸——cli 的告警里要用它。"""
    from copilot.ingest.corrections import Correction as CorrectionFile

    c = CorrectionFile(path=None, target_url=URL, reason="r", title="打印位置")
    assert "打印位置" in c.name
