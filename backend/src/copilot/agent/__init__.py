"""M7 Agent 化：把 M1 的检索包成工具，加上多轮追问与出方案的能力。

    agent.py      主 Agent + instructions
    tools.py      四个工具（检索 / 记需求 / 出清单 / 导出 xlsx）
    planner.py    生成结构化清单的子 Agent
    checklist.py  需求档案与清单的数据形状
    deps.py       依赖注入包（⚠️ user_id 只从这里来，绝不做工具入参）
    model.py      Pydantic AI 的模型对象（OpenAI 兼容，保持可换供应商）
    runner.py     把 Agent 的事件流翻成 AI SDK 的 UI Message Stream
"""

from copilot.agent.agent import build_agent, usage_limits
from copilot.agent.deps import AgentDeps

__all__ = ["AgentDeps", "build_agent", "usage_limits"]
