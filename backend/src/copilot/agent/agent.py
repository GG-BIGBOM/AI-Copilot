"""主 Agent：路由 + 多轮状态 + 编排。

⭐ **M10 之后它不再是「带检索工具的问答机器人」。** ERP 答案由终结工具
`answer_kb` 产出并直接流给用户，Agent 看不到原始材料，也就写不出编造的配置。
它剩下的活是三样，每一样直路都做不了：

    路由      这句话该查知识库、看时间、还是开始收集需求
    多轮状态  需求档案一点一点填起来（profile 落库）
    编排      收齐 → generate_plan → export_excel

M7 让它拿着原始材料自己写答案，41 题上准确率 87.8% / 幻觉率 12.5%，
而同一份题直路是 100% / 0%。**M10 不是把它调得更听话，是把那支笔拿走。**
四条成因（检索词漂移 / 材料串味 / 拒答闸门变软 / 换了 prompt）见 tools.py 文件头。

**硬闸门必须有**：`UsageLimits` 限住模型请求数和工具调用数。没有它，
一个开始循环调工具的模型能把额度和时间一起烧光，而用户那边只看到一个转圈。
限额按路径分：普通问答给得很紧（决策 → 工具 → 收尾就够），
出方案那条要留出多轮收集的余量。
"""

from __future__ import annotations

import re

from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits

from copilot.agent.deps import AgentDeps
from copilot.agent.model import build_model
from copilot.agent.tools import (
    answer_kb,
    current_time,
    export_excel,
    generate_plan,
    my_documents,
    save_requirement,
    whoami,
)
from copilot.config import get_settings

INSTRUCTIONS = """你是旺店通旗舰版 ERP 的实施顾问助手。

⚠️ **你自己不掌握任何 ERP 知识。** 所有和旺店通、ERP、电商订单、仓库、物流、
售后、财务、系统设置有关的问题，一律调用 `answer_kb`——它会查知识库并**直接
回答用户**，你看不到它的答案，也不需要看到。

## 怎么选

1. **沾边就调 `answer_kb`，拿不准也调它。** 它没有参数，用的就是用户这一轮的原话。
   调完这一轮就结束了：**不要复述、不要补充、不要总结、不要追加"希望对你有帮助"。**
   ⭐ **追问也一样，一次都不能省。** 用户说「那不良品呢」「第二步呢」「那京东呢」时，
   上一轮的答案**不是**可以接着答的材料——那是上一轮查到的东西，这一轮的问题
   要重新查。前面的对话只用来**听懂这一句在问什么**，不是用来当依据的。
   照着历史答出来的东西，用户点不开任何来源，而它看起来和查来的一模一样。
2. 用户问当前时间、今天几号、星期几 → `current_time`，然后用一句话回答。
3. 用户问「你是谁」「你能做什么」「怎么用」→ `whoami`，然后本轮结束。
4. 用户问「我传了哪些文档」「我的知识库里有什么」→ `my_documents`。
5. 用户要「实施方案 / 配置方案 / 上线清单」→ 走下面那套需求收集。
6. 打招呼、道谢 → 直接答一句，不要调工具。

## 绝对禁止

- **凭自己的记忆写旺店通的界面路径、菜单层级、字段名、参数取值、数量上限。**
  这几样**每一个**都必须来自 `answer_kb`。ERP 里一条编出来的配置能让客户的
  订单卡住——这不是修辞，是这个项目存在的理由。
  ⚠️ 你觉得"这题我会"的时候，正是最容易越线的时候：你记忆里的路径和真实系统
  对不上时，用户会照着去点，而他分不清哪句是查来的、哪句是你写的。
- **自己答一句就算了事，不去查。** 哪怕问题看起来"明显不用查"、
  哪怕上一轮刚查过相关的内容——只要是关于这个产品的问题，就**先交给
  `answer_kb`**。它查得到就带着出处答；查不到，它自己会决定是按通用理解
  说一说（并标明没有出处）还是回一句「知识库暂无此内容」。
  **那个判断是它的活，不是你的。**
  ⭐ 这一条比上一条更常被违反：越线的样子不是编造，是"我已经知道了，
  就不查了"——而省掉的那一次检索，正是用户能点开来源的唯一来路。
- **替用户做和这个产品无关的事。** 写代码、翻译、查天气、算数、写文案、聊时事，
  这些都不做。
- **在调用 `answer_kb` 之前说「我查一下」「稍等」之类的开场白。** 用户看不到它，
  纯浪费一次生成。想调就直接调。

## 客户要「实施方案 / 配置方案 / 上线清单」时

这时不要急着答，先把需求问清楚：

1. 一次**只问一到两个**问题。一口气抛七个问题没人会回答。
2. 客户答了就用 `save_requirement` 记下来，它会告诉你下一个该问什么。
3. 客户一句话里说了好几项（如「我们淘宝拼多多两个店，一个仓」），
   就分别调用 `save_requirement` 逐项记录，别丢。
   「自营 / 外包」描述的是 `warehouse_mode`，不是 `platforms`；已经记录的字段
   只有在客户明确说「说错了 / 改成 / 更正」时才能覆盖。
4. 收齐后调用 `generate_plan` 生成方案，再调用 `export_excel` 导出。
5. 客户明确说「不知道」「先按常见的来」时，记下这个说法继续往下走，
   不要卡在同一个问题上反复问。

## 写法（只管你自己写的那部分——追问、时间、闲聊）

- 短。追问一次只问一到两个问题，一句话说完。
- 直接说事，不要「根据参考材料」之类的开场白。
- 不要自己拼下载链接，导出成功后页面上会有下载按钮。"""

_CONTINUE_ONLY_RE = re.compile(r"^(?:好(?:的)?[，,。 ]*)?(?:继续|接着来)[。！! ]*$")


def plan_turn_requires_tool(deps: AgentDeps, tool_calls: int) -> bool:
    """已开始收集后，含新信息的回复不能只在文字里假装“已记录”。"""
    return (
        deps.plan_flow
        and bool(deps.profile.filled())
        and tool_calls == 0
        and not _CONTINUE_ONLY_RE.fullmatch(deps.question.strip())
    )

# 主 Agent 的工具。**`search_kb` 不在这里**——那是 M7 留下的「材料级」工具，
# 一旦挂上去，模型就又有了拿原始材料自由发挥的路子，M10 等于白做。
# 函数和它的隔离测试暂时留着，跟 `_chat_stream` 一起进 P3 的删除清单。
TOOLS = [
    answer_kb,
    current_time,
    whoami,
    my_documents,
    save_requirement,
    generate_plan,
    export_excel,
]


def build_agent(model=None) -> Agent[AgentDeps, str]:
    """建主 Agent。

    Args:
        model: 只给测试用——传一个 `FunctionModel` 就能在不联网、不花钱的
            情况下把「模型决定调哪个工具」这件事写死，从而测 runner 的翻译逻辑。
    """
    agent = Agent(
        model or build_model(),
        deps_type=AgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        tools=TOOLS,
        # 单个工具最多重试 1 次。工具本身已经把失败变成了「返回一句人话」
        # （见 tools.py 文件头第 2 条），重试主要是给参数填错留一次机会
        retries=1,
        model_settings={"temperature": 0.1},
    )

    @agent.output_validator
    def require_tool_for_active_plan(ctx: RunContext[AgentDeps], output: str) -> str:
        if plan_turn_requires_tool(ctx.deps, ctx.usage.tool_calls):
            raise ModelRetry(
                "当前会话正在收集实施需求，但你没有调用任何工具。"
                "如果用户提供了需求值，必须调用 save_requirement；"
                "如果用户在问产品知识，必须调用 answer_kb；"
                "不要只用文字声称“已记录”。"
            )
        return output

    return agent


def usage_limits(*, plan_flow: bool = False) -> UsageLimits:
    """跑飞时的硬闸门。

    ⭐ **按路径分开设。** 普通问答的正常形态是「决策 → answer_kb → 结束」，
    两次模型请求封顶；给它 8 次，等于允许一个开始循环的模型多烧 6 次
    才被拦住，而用户全程只看到一个转圈。出方案那条要多轮收集，才需要余量。

    Args:
        plan_flow: 这条会话已经在收集需求（`profile` 有内容）。
    """
    s = get_settings()
    if plan_flow:
        return UsageLimits(
            request_limit=s.agent_max_requests,
            tool_calls_limit=s.agent_max_tool_calls,
        )
    return UsageLimits(
        request_limit=s.agent_max_requests_qa,
        tool_calls_limit=s.agent_max_tool_calls_qa,
    )
