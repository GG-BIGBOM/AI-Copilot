"""MCP server（W3.1）——把知识库接进客户已经在用的 AI 工具。

    copilot mcp --as-user you@example.com

Claude Desktop / Cursor 用 stdio 起这个进程，然后就能直接问旺店通的问题、
拿到带引用的答案。**客户不用改造任何东西**——这正是 FDE 每天在解释的事。

配置长这样（Claude Desktop 的 `claude_desktop_config.json`）：

    {
      "mcpServers": {
        "wdt-copilot": {
          "command": "/opt/copilot/.venv/bin/copilot",
          "args": ["mcp", "--as-user", "you@example.com"]
        }
      }
    }

─────────────────────────────────────────────────────────
⚠️⚠️ **鉴权：这一节是这个文件存在的全部风险，先说清楚。**

MCP server 极容易做成一个**无鉴权的私有文档读取口**。这里的三条规矩：

1. **`user_id` 不是工具入参。** 它在进程启动时由 `--as-user` 解析一次，
   之后每次调用都从闭包里取。模型看不见它、说不出它、也就改不了它——
   这和 `agent/deps.py` 那条红线是同一条，理由也一样：
   凡是能出现在工具签名里的东西，一句 prompt injection 就能让模型去填。
2. **`space_id`（哪一版 ERP）同理。** 让模型能指定它，等于一句话就能把提问
   切到另一个产品的材料上，而答案会写得和真的一样确定。
3. **这个进程是本机的、不是网络服务。** 它读 `.env` 里的库连接，
   信任的是「谁能在这台机器上跑这条命令」——和 `copilot ask` 完全同一个
   信任模型，不是一个新的鉴权边界。⚠️ 所以**别把它挂到公网上**：
   那需要真正的令牌体系（每用户一枚、可吊销、有审计），这里没有。

⭐ 面试时值得讲的是第 1 条的**理由**，不是"我做了 MCP"：
「我做 MCP 时第一件事是想清楚 user_id 从哪来。」

─────────────────────────────────────────────────────────
⚠️ **工具实现一律复用现成的那条路**（`retrieve.search` / `qa.ask_stream`），
不另写一套。MCP 是个**外壳**——它长成第二套 RAG 的那一天，
这个项目就有两处地方决定"哪些材料能被看到"，而隔离是这里唯一一条
错了就不可挽回的规则。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 一次列多少份文档。和 `agent/tools.my_documents` 同一个数
MY_DOCS_LIMIT = 20


@dataclass(frozen=True)
class Identity:
    """这个进程代表谁、在哪一版 ERP 上。**启动时定死，之后只读。**

    ⚠️ 它是 `frozen=True` 的，不是为了好看：工具处理函数拿到的是同一个对象，
    可变的话，一个写错的处理函数就能把下一次调用的身份改掉——
    而那种越权没有任何症状。
    """

    user_id: uuid.UUID | None
    email: str
    space_id: uuid.UUID
    space_code: str


async def resolve_identity(session, email: str, space_code: str) -> Identity:
    """把 `--as-user` 那个邮箱和 `--space` 那个代号解析成 id。**只在启动时跑一次。**

    ⚠️ **解析不出来就抛，不要退回"匿名 + 默认空间"。**
    退回去的表现是：一个拼错了邮箱的人拿到一个能用的 MCP server，
    问什么都答得出来（公共库照样能检索），于是他以为自己连上了自己的账号——
    直到某天发现"我传的文档一份都搜不到"。fail closed 的方向是安全的那一边。
    """
    from sqlalchemy import select

    from copilot import spaces
    from copilot.db.models import User

    try:
        space = await spaces.by_code(session, space_code)
    except spaces.SpaceNotFound as e:
        # ⚠️ 转成 SystemExit：这是启动参数写错了，不是运行时故障。
        # 让它带着栈往上抛的话，用户在 Claude Desktop 里只会看到
        # 「server disconnected」，一个字的原因都没有
        raise SystemExit(f"没有这个知识版本：{space_code}") from e
    if space.status != "active":
        # 语料还没导入的空间，激活前连上去只会得到「知识库暂无此内容」，
        # 而用户会以为是系统坏了（同 `copilot spaces activate` 那道闸门）
        raise SystemExit(f"知识版本 {space_code} 还没启用（status={space.status}）")

    if not email:
        # 只读公共库的匿名模式。**明说**，别让人以为自己已经登录了
        logger.warning("没有 --as-user：这次只能检索公共库，你自己传的文档搜不到")
        return Identity(user_id=None, email="", space_id=space.id, space_code=space_code)

    user = (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(f"没有这个账号：{email}")
    if not user.is_active:
        # 和 `auth/deps.py` 同一条：停用的账号连不上任何入口
        raise SystemExit(f"账号已停用：{email}")
    return Identity(user_id=user.id, email=user.email, space_id=space.id, space_code=space_code)


# ─────────────────────────────────────────────────────────
# 三个工具。**签名里没有 user_id、也没有 space** —— 见文件头第 1、2 条
# ─────────────────────────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "name": "answer_kb",
        "description": (
            "回答关于旺店通旗舰版 ERP 的问题：操作步骤、参数配置、异常排查、"
            "功能限制、界面路径。返回一段带 [n] 引用编号的答案和对应的来源清单。"
            "知识库里没有的内容它会明说，不会编。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的问题，用中文，尽量保留原话。",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "search_kb",
        "description": (
            "检索旺店通 ERP 知识库，返回带编号来源的**原文片段**（不生成答案）。"
            "想自己读材料、或者要把多篇材料放在一起比较时用它。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索词。含界面名、功能名、编码时更准。",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "my_documents",
        "description": "列出当前账号上传到私有知识库的文档，以及它们解析好了没有。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def call_tool(name: str, args: dict, identity: Identity) -> str:
    """跑一个工具，返回给模型看的纯文本。**协议无关**，所以能直接被测试。

    ⚠️ 未知工具名要**说清楚**而不是抛异常：MCP 客户端会把异常显示成一句
    「服务器错误」，而真实原因往往是客户端缓存了一份旧的工具清单。
    """
    from copilot.db.session import SessionLocal

    async with SessionLocal() as session:
        if name == "my_documents":
            return await _my_documents(session, identity)
        if name == "search_kb":
            return await _search_kb(session, identity, str(args.get("query") or ""))
        if name == "answer_kb":
            return await _answer_kb(session, identity, str(args.get("question") or ""))
    return f"没有名为 {name!r} 的工具。可用的是：" + "、".join(t["name"] for t in TOOL_SPECS)


def _providers():
    from copilot.providers.siliconflow import SiliconFlowEmbedder, SiliconFlowReranker

    return SiliconFlowEmbedder(), SiliconFlowReranker()


async def _my_documents(session, identity: Identity) -> str:
    from sqlalchemy import desc, select

    from copilot.db.models import Document

    if identity.user_id is None:
        return "这次是匿名模式（启动时没给 --as-user），没有私有文档。"
    stmt = (
        select(Document.title, Document.status)
        # ⚠️ 隔离红线：owner 只能来自启动时定死的身份。
        # 严格等于当前用户——公共库那几百篇（owner_id IS NULL）不属于任何人
        .where(Document.owner_id == identity.user_id)
        .order_by(desc(Document.created_at))
        .limit(MY_DOCS_LIMIT)
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        return f"{identity.email} 的私有知识库是空的，还没有上传过文档。"
    lines = [
        f"- {title}" + ("" if status == "done" else f"（{status}，还不能被检索到）")
        for title, status in rows
    ]
    more = f"（只列最近 {MY_DOCS_LIMIT} 份）" if len(rows) == MY_DOCS_LIMIT else ""
    return f"{identity.email} 上传了 {len(rows)} 份文档{more}：\n" + "\n".join(lines)


async def _search_kb(session, identity: Identity, query: str) -> str:
    from copilot.retrieve import search

    if not query.strip():
        return "检索词是空的。"
    embedder, reranker = _providers()
    result = await search(
        session,
        query,
        embedder,
        reranker,
        # ⚠️ 隔离红线：两样都来自启动时定死的身份，不是入参
        user_id=identity.user_id,
        space_id=identity.space_id,
    )
    if result.is_empty:
        return f"知识库里没有检索到与「{query}」相关的内容。"
    return result.build_context().text


async def _answer_kb(session, identity: Identity, question: str) -> str:
    """⚠️ 走 `qa.ask_stream` 整条，不是自己拼 prompt 再调模型。

    这一层的全部意义就是"和线上跑同一条路"：防幻觉的铁律、拒答判定、
    引用编号、注入防线、私有块的处理，一样都不能少。绕过去就等于
    在 MCP 这个入口上重新实现一遍 RAG，而那一遍不会有任何评测看着。
    """
    from copilot.providers.llm import ChatLLM
    from copilot.qa import ask_stream, is_no_answer

    if not question.strip():
        return "问题是空的。"
    embedder, reranker = _providers()
    streamed = await ask_stream(
        session,
        question,
        embedder,
        reranker,
        ChatLLM(),
        # ⚠️ 隔离红线：同上
        user_id=identity.user_id,
        space_id=identity.space_id,
    )
    answer = "".join(piece for kind, piece in streamed.stream if kind == "content")

    # ⚠️ **拒答时一条来源都不列。** 一旦答案是「暂无此内容」，下面却挂着五条
    # 来源，读的人会以为答案是有依据的——这比不做防幻觉更糟（M1 的坑 #2，
    # 直路和前端各有一处同样的判定，这里是第三个入口）
    if is_no_answer(answer) or not streamed.citations:
        return answer
    lines = "\n".join(
        f"[{c.n}] {c.title}" + (f" · {c.heading}" if c.heading else "")
        + (f"  {c.source_url}" if c.source_url else "")
        for c in streamed.citations
    )
    return f"{answer}\n\n来源：\n{lines}"


# ─────────────────────────────────────────────────────────
# stdio 外壳。上面那些和协议无关，这一段才碰 MCP SDK
# ─────────────────────────────────────────────────────────


async def serve(email: str, space_code: str) -> None:
    """起一个 stdio MCP server。阻塞直到客户端断开。"""
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    from copilot.db.session import SessionLocal, engine

    async with SessionLocal() as session:
        identity = await resolve_identity(session, email, space_code)
    who = identity.email or "匿名（只有公共库）"
    logger.info("MCP server：%s / 知识版本 %s", who, identity.space_code)

    server = Server("wdt-copilot")

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(**spec) for spec in TOOL_SPECS]

    @server.call_tool()
    async def _call(name: str, arguments: dict | None) -> list[types.TextContent]:
        # ⚠️ 一个工具挂掉不该把整个 server 带走：客户端看到的是
        # 「连接断了」，而真实原因（比如 embedding 接口欠费）一个字都看不到
        try:
            text = await call_tool(name, arguments or {}, identity)
        except Exception as e:  # noqa: BLE001 - 同 agent/tools.py 文件头第 2 条
            logger.warning("MCP 工具 %s 失败：%s", name, e, exc_info=True)
            text = f"工具 {name} 出错了：{type(e).__name__}。可以再试一次。"
        return [types.TextContent(type="text", text=text)]

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await engine.dispose()
