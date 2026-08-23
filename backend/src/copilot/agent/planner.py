"""生成结构化的实施配置清单。

这是一个**独立的子 Agent**，不是主 Agent 的一个工具输出。理由：

主 Agent 的输出类型是 `str`（要流式吐给用户看），而这里要的是一个
`Checklist` 对象——两者不能是同一个 Agent。Pydantic AI 的做法是让子 Agent 的
`output_type=Checklist`，模型必须按 schema 填，填不对会被要求重填
（`ModelRetry`），而不是给出一段「看起来像表格」的文本。

**清单必须有据。** 所以生成前先按需求档案主动检索几轮，把材料塞给子 Agent，
并在 prompt 里要求 `why` 字段只能引用材料。知识库里没有的，写进
`open_questions`，不许编——ERP 实施里一条编出来的配置能让客户的订单卡住。
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent

from copilot.agent.checklist import REQUIREMENT_FIELDS, Checklist
from copilot.agent.deps import AgentDeps
from copilot.agent.model import build_model
from copilot.config import get_settings
from copilot.retrieve import search

logger = logging.getLogger(__name__)

PLANNER_INSTRUCTIONS = """你是旺店通旗舰版 ERP 的资深实施顾问。根据「客户需求」和「知识库材料」，
输出一份可执行的实施配置清单。

铁律：
1. 每一项的 `why` 必须是材料里的依据。**材料里没写的配置不要列**——
   ERP 实施里一条编出来的配置能让客户的订单卡住。
2. `value` 要给具体值（具体的选项名、路径、数字）。写「按需配置」「视情况而定」
   等于没写，那种清单交给实施顾问是废纸。
3. `area` 写清界面路径，如「设置–基本设置–店铺」，让人能照着点。
4. 需要人工确认的、材料里没有答案的，放进 `open_questions`。**这一项宁多勿少。**
5. 按实施顺序排：先店铺与仓库这类地基，再流程与打印，最后是特殊业务。
6. 条目控制在 8–20 项。堆到五十项没人会看。"""

# 生成清单前主动检索的方向。按需求档案里填了什么来选，不是固定几句。
# key 是需求字段，value 是要检索的话题
_TOPIC_BY_FIELD = {
    "platforms": "店铺对接步骤与授权配置",
    "warehouse_mode": "仓库设置与发货仓配置",
    "daily_orders": "订单审核与自动化流程设置",
    "logistics": "物流设置与电子面单模板",
    "specials": "组合装 预售 分销 生产 设置",
}


async def _gather_material(deps: AgentDeps) -> str:
    """按已收集的需求，检索几段相关材料。

    只检索**填过的字段**对应的话题：客户没有分销业务时，把分销文档塞进
    prompt 只会挤掉真正有用的材料，还会诱导模型列一堆用不上的配置。
    """
    filled = deps.profile.filled()
    parts: list[str] = []
    for field, topic in _TOPIC_BY_FIELD.items():
        if field not in filled:
            continue
        query = f"{filled[field]} {topic}"
        try:
            result = await search(
                deps.session,
                query,
                deps.embedder,
                deps.reranker,
                # ⚠️ 出方案也要钉在这条会话的知识版本上：旗舰版的实施清单里
                # 混进企业版的配置项，交付出去就是错的
                user_id=deps.user_id,
                space_id=deps.space_id,
            )
        except Exception as e:  # noqa: BLE001 - 少一段材料也能生成，别整个失败
            logger.warning("生成方案时检索失败 topic=%s：%s", topic, e)
            continue
        if result.is_empty:
            continue
        bundle = result.build_context()
        deps.merge_citations([c.to_dict() for c in result.citations])
        deps.retrieved.append(bundle.text)
        parts.append(f"### 关于{REQUIREMENT_FIELDS[field][0]}\n{bundle.text}")
    return "\n\n".join(parts) or "（没有检索到相关材料）"


async def build_checklist(deps: AgentDeps) -> Checklist:
    s = get_settings()
    material = await _gather_material(deps)

    planner = Agent(
        build_model(),
        output_type=Checklist,
        instructions=PLANNER_INSTRUCTIONS,
        # 结构化输出偶尔会填不对 schema。给两次重试，比整轮失败好
        retries=2,
    )
    prompt = (
        f"客户需求：\n{deps.profile.human()}\n\n"
        f"知识库材料（`[n]` 是来源编号，可在 why 里引用）：\n{material[:12000]}"
    )
    result = await planner.run(
        prompt,
        model_settings={"temperature": 0.2},
        usage_limits=_limits(s),
    )
    return result.output


def _limits(s):
    from pydantic_ai import UsageLimits

    return UsageLimits(request_limit=s.agent_max_requests)
