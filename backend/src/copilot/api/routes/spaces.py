"""知识版本列表。聊天页顶部那个「知识版本：[旗舰版 ▼]」用它。

⚠️ **只列 `active` 且用户可选的那几个**（见 `copilot.spaces.SELECTABLE`）。
两个不列的东西各有各的理由：

    inactive 的企业版   表建好了不等于语料导进来了。列出来，用户选进去问什么
                        都得到「知识库暂无此内容」——他会以为是系统坏了
    common              它不是一个能聊天的空间，只是**检索范围**。列出来等于
                        让人选择「我要在一个没有产品知识的空间里问问题」
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from copilot import spaces
from copilot.auth.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/api/knowledge-spaces", tags=["spaces"])


class SpaceOut(BaseModel):
    """⚠️ 只出 `code` / `name` / `description`，**不出 id**。

    前端一律用 code 说话：id 每套环境都不一样（本机、服务器各建各的），
    把它写进前端就等于把两套环境绑死。会话和空间的绑定在服务端完成。
    """

    code: str
    name: str
    description: str | None = None


@router.get("", response_model=list[SpaceOut])
async def list_spaces(user: CurrentUser, session: SessionDep) -> list[SpaceOut]:
    """用户能选的知识版本，按固定顺序。"""
    rows = await spaces.selectable(session)
    return [SpaceOut(code=r.code, name=r.name, description=r.description) for r in rows]
