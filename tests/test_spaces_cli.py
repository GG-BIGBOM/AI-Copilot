"""`copilot spaces` 与 `--space` 的几条闸门（M18 / W3.3）。

这一节全是**闸门**，一条都不是功能：

    root_for 拼错就抛      默默落回默认目录 = 企业版语料写进旗舰版那棵树
    activate 数块不数文档   161 篇文档 0 块的空间照样能激活 = 闸门守不住
    勘误层只对旗舰版        企业版入库会打出一大片"没有对上"的假警告

⭐ 三条的共同点：**错的那一面都没有报错**。
"""

from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from copilot import spaces
from copilot.cli import app

runner = CliRunner()


# ═══════════════ 一、`--space` 拼错当场退出 ═══════════════


def test_root_for_maps_each_space_to_its_own_tree():
    """⚠️ 旗舰版是**历史遗留路径**，不搬。

    搬了要全量重新向量化（几千次付费 embedding），换来的只是目录好看。
    把"它是特例"这件事写在一处，而不是在每个用到路径的地方各写一个 if。
    """
    flagship = spaces.root_for(spaces.FLAGSHIP)
    desktop = spaces.root_for(spaces.ENTERPRISE_DESKTOP)
    web = spaces.root_for(spaces.ENTERPRISE_WEB)

    assert flagship.parts[-2:] == ("raw", "yuque"), "旗舰版必须还是那条历史路径"
    assert desktop != flagship != web
    assert desktop != web
    # 三棵树互不包含——`_manifest.json` 共用一份的话，增量判定会串
    assert not str(desktop).startswith(str(flagship))
    assert not str(web).startswith(str(desktop))


def test_a_typo_in_space_raises_instead_of_falling_back():
    """⚠️⚠️ **默默落回默认目录是这一步最危险的错法。**

    `--space enterprise_desktp`（少一个 o）会把企业版语料写进旗舰版那棵树，
    然后被下一次 `copilot ingest` 当成旗舰版语料灌进去——**没有任何症状**。
    要发现它得靠有人问了一个企业版的问题、在旗舰版会话里拿到企业版的答案。
    """
    with pytest.raises(spaces.SpaceNotFound):
        spaces.root_for("enterprise_desktp")
    with pytest.raises(spaces.SpaceNotFound):
        spaces.root_for("")


def test_sync_yuque_exits_on_an_unknown_space():
    """⚠️ 要在**碰网络之前**就退出——抓完几百篇再报错就晚了。"""
    result = runner.invoke(app, ["sync-yuque", "someone", "--space", "no-such-space"])
    assert result.exit_code == 1
    assert "没有" in result.output


async def test_ingest_exits_on_an_unknown_space(capsys):
    """⚠️ 走 `_ingest` 而不是 CliRunner。

    `ingest` 那条命令体是 `asyncio.run(_ingest(...))`，用 CliRunner 调它等于
    **在 pytest 的事件循环里再开一个循环**——它自己会过，但会把这个循环搞坏，
    表现是**同一个文件里后面几道 async 测试莫名其妙地红**，
    而单跑它们又全过。这正是这一轮修 E1 时遇到的同一类"测试间共享状态"。
    """
    import typer

    from copilot.cli import _ingest

    with pytest.raises(typer.Exit):
        await _ingest("", force=False, limit=0, owner="", space="no-such-space")
    assert "没有" in capsys.readouterr().out


# ═══════════════ 二、`spaces list` / `activate` / `deactivate` ═══════════════


async def test_spaces_list_shows_every_space_with_its_counts(capsys):
    """⚠️ 走 `_spaces_list` 而不是 CliRunner：命令里是 `asyncio.run(...)`，
    在 pytest 的事件循环里再开一个循环会炸在 asyncpg 的连接取消上。
    测的是同一段逻辑，只是不经过 typer 那层壳。"""
    from copilot.cli import _spaces_list

    await _spaces_list()
    out = capsys.readouterr().out
    for code in (
        spaces.FLAGSHIP,
        spaces.ENTERPRISE_DESKTOP,
        spaces.ENTERPRISE_WEB,
        spaces.COMMON,
    ):
        assert code in out
    assert "active" in out


async def test_activate_refuses_a_space_with_no_chunks(capsys):
    """⚠️⚠️ **判据是块，不是文档。**

    实测：开发库里 `enterprise_desktop` 有 161 篇文档、**0 块**
    （测试留下的孤儿行）。按文档数判的话这道闸门当场放行，
    而检索一条都召不回——**一个看起来在守、其实守不住的闸门，
    比没有闸门更糟**，因为不会再有人去看它。

    ⭐ 能被检索到的是块，闸门就该数块。
    """
    import typer

    from copilot.cli import _spaces_set_status

    with pytest.raises(typer.Exit):
        await _spaces_set_status(spaces.ENTERPRISE_DESKTOP, "active", force=False)
    out = capsys.readouterr().out
    assert "一个块都没有" in out
    assert "--force" in out, "得告诉人怎么绕过去，否则他会去改代码"


async def test_activate_and_deactivate_round_trip(maker):
    """⭐ `deactivate` 是 M18 的回滚手段：导错了先让用户看不见，
    数据留库里慢慢查。比删语料快，也不会连带删掉图片。"""
    from copilot.cli import _spaces_set_status

    code = spaces.ENTERPRISE_WEB
    try:
        await _spaces_set_status(code, "active", force=True)
        async with maker() as s:
            assert (await spaces.by_code(s, code)).status == "active"
        await _spaces_set_status(code, "inactive", force=True)
        async with maker() as s:
            assert (await spaces.by_code(s, code)).status == "inactive"
    finally:
        async with maker() as s:
            sp = await spaces.by_code(s, code)
            sp.status = "inactive"
            await s.commit()


async def test_activating_an_unknown_space_exits():
    import typer

    from copilot.cli import _spaces_set_status

    with pytest.raises(typer.Exit):
        await _spaces_set_status(f"nope-{uuid.uuid4().hex[:6]}", "active", force=True)
