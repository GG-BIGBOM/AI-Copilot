"""会话级「已确认事实」表（W2.2）。

⭐ **这一层要解决的不是"记不住"，是"靠回忆记"。**

在此之前，一条会话里所有的记忆都活在末 6 轮原文里（`qa.HISTORY_TURNS`）。
第 7 轮开始，第 1 轮说过的话就从上下文里消失了——用户在第 15 轮问
「我一开始说的是哪个版本」，模型手里根本没有那句话。它有两种表现，
都不好：Agent 那条路会说「当前上下文只保留最近几轮」（诚实，但像失忆），
直路则会拿当前窗口里的第一条**冒充**整段会话的第一条（自信，且错）。

**把窗口开大不是解**。上下文是有限的、也是要花钱的，而且真正的问题不在长度：
一个会话里真正需要跨窗口活着的事实只有几条（哪一版 ERP、哪家客户、几个仓），
它们加起来不到 100 个 token，却被埋在几万字的对话原文里，
指望模型每一轮都从原文里重新读出来。**该结构化的东西不要交给回忆。**

所以这里做的是一张**表**：

    ERP 版本      从 `conversations.knowledge_space_id` 直接读 —— 结构上就是对的
    客户 / 主体   复用 `qa._SUBJECT_SUFFIX_RE`，用户点过名就记下
    需求那 7 项   Agent 的 `save_requirement` 填进 `profile` 之后镜像过来

⚠️ **一条都不靠模型抽取。** 让模型每轮抽一次事实，等于给防幻觉的墙上开一个
新洞：抽错的那一条会被**钉在上下文里**，之后每一轮都在重复同一个错误，
而它长着"系统确认过"的样子。宁可这张表少几项——少了只是回到今天的行为
（不知道就说不知道），错了才是新增的伤害。

⚠️ **它不是参考材料。** 表里写着「仓库数量：4」不代表旺店通有个叫仓库数量的
字段该填 4。注入的那段话里写死了这一条，见 `_PREFACE` 末尾。

⚠️ 存的是 `conversations.facts`，**不是 `profile`**。`profile is not None`
是「这条会话在走 Agent」的路由标记（见 routes/chat.py），
往里塞东西会把普通问答的会话也路由到 Agent 上去。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from copilot.agent.checklist import REQUIREMENT_FIELDS

# 这张表自己的两项。剩下 7 项从 `REQUIREMENT_FIELDS` 派生——
# **不抄一份**：那边加一个字段而这边忘了加，表现是那一项永远不进事实表，
# 不报错、也没人会发现（同 checklist.py 文件头那条规矩）
_OWN_FIELDS: dict[str, str] = {
    "knowledge_space": "ERP 版本",
    "subject": "客户 / 主体",
}

# 字段 → 给用户和模型看的名字。顺序就是注入时的顺序：
# 先说这是哪一版 ERP、给谁做，再说业务规模。前两项决定后面几项的意义
FACT_LABELS: dict[str, str] = {
    **_OWN_FIELDS,
    **{f: label for f, (label, _) in REQUIREMENT_FIELDS.items()},
}

# 改口历史最多留几条。留**第一条**是刚需（「我一开始说的」问的就是它），
# 留最近两条是为了说清现状；中间那些对回答没有帮助，只占 token
_MAX_REVISIONS = 3

# 单条事实的长度上限。用户可能把一整段话当成"平台"填进来，
# 而这张表每一轮都要进 prompt——它的成本是按轮数乘的
_VALUE_LIMIT = 120

# 「我一开始说的是哪个**版本**」里的那个词 → 对应哪一项事实。
# 只给 `answers()` 用，判「这句话问的是不是我手里有的东西」。
#
# ⚠️ **这张表天生是不全的，而且必须偏向漏判**（同 `qa._SUBJECT_SUFFIX_RE`
# 上面那段的取舍）：漏判 = 退回今天的边界话术「我无法确认」，
# 误判 = 让一个本该说不知道的问题走进模型，而模型手里有一张看着很权威的表。
# 所以这里只放**几乎只会出现在问这一项时**的词。
#
# ⚠️⚠️ 加词前先问一句：这个词会不会出现在一句正常的 ERP 产品问题里？
# 「设置」「数量」「模式」都试过又删掉——「怎么设置仓库数量上限」里
# 每一个词都在这张表上，而那是一道该走检索的产品问题。
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "knowledge_space": ("哪个版本", "哪一版", "什么版本", "旗舰版", "企业版"),
    "subject": ("哪家客户", "哪个客户", "客户是", "客户叫"),
    "platforms": ("哪些平台", "哪几个平台", "对接平台", "接了哪些"),
    "shop_count": ("几个店铺", "多少店铺", "几家店"),
    "warehouse_mode": ("仓库模式", "自营还是", "云仓还是"),
    "warehouse_count": ("几个仓", "多少个仓", "几个发货仓", "仓库数量"),
    # ⚠️ 这里原来有一个裸的「多少单」，被 `test_normal_product_questions_...`
    # 当场抓掉了：「**多少单**以上要走批量打印」是一道该走检索的产品题，
    # 而它会被认成"在问日均单量"。留下的三个都带着"我们"或"日均"
    "daily_orders": ("日均单量", "单量是", "我们多少单"),
    "logistics": ("哪几家快递", "哪些快递", "用的什么快递"),
    "specials": ("特殊业务",),
}


@dataclass(slots=True)
class SessionFacts:
    """一条会话里已经确认下来的事实。

    存储形状（`conversations.facts` 的 JSONB）：

        {"platforms": {"value": "淘宝、抖音", "turn": 5,
                       "was": [{"value": "淘宝", "turn": 2}]}}

    `value`/`turn` 是**现在**的说法，`was` 是被它顶掉的那些（从旧到新）。
    两个都要留：问「现在是几个仓」和问「我一开始说的是几个仓」
    是两个不同的问题，而后者恰恰是这一层存在的理由。
    """

    facts: dict[str, dict] = field(default_factory=dict)

    # ---------- 进出库 ----------

    @classmethod
    def load(cls, raw: dict | None) -> SessionFacts:
        """从库里那一列还原。**脏数据一律跳过，不抛异常。**

        这一列是 W2.2 才加的，早于它的会话读出来是 None；而一条读坏的事实
        不该让整轮提问变成 500——它最坏的后果只是这一轮少注入一条。
        """
        out: dict[str, dict] = {}
        for f in FACT_LABELS:
            rec = (raw or {}).get(f)
            if isinstance(rec, dict) and isinstance(rec.get("value"), str) and rec["value"]:
                out[f] = {
                    "value": rec["value"][:_VALUE_LIMIT],
                    "turn": int(rec.get("turn") or 0),
                    "was": [
                        w
                        for w in (rec.get("was") or [])
                        if isinstance(w, dict) and isinstance(w.get("value"), str)
                    ][:_MAX_REVISIONS],
                }
        return cls(facts=out)

    def dump(self) -> dict:
        """写回库里那一列。空表返回 `{}` 而不是 None——
        `{}` 的意思是「这条会话开始记事实了，只是还什么都没记到」，
        和「这条会话早于 W2.2」不是一件事。"""
        return {f: dict(rec) for f, rec in self.facts.items()}

    # ---------- 记一条 ----------

    def note(self, field_name: str, value: str | None, turn: int) -> bool:
        """记下一条事实，返回**这次是不是真的改了**（调用方据此决定要不要写库）。

        ⚠️ **同一个值重复说不算改口。** 用户每一轮都提「星辰电商」是常态，
        每次都往 `was` 里塞一条的话，第 15 轮那段注入会变成一屏改口记录。
        """
        if field_name not in FACT_LABELS:
            raise KeyError(f"不认识的事实字段：{field_name!r}")
        text = (value or "").strip()[:_VALUE_LIMIT]
        if not text:
            return False

        old = self.facts.get(field_name)
        if old is None:
            self.facts[field_name] = {"value": text, "turn": turn, "was": []}
            return True
        if old["value"] == text:
            return False

        was = [*old.get("was", []), {"value": old["value"], "turn": old["turn"]}]
        # 超了就从**中间**丢，第一条永远留着——「我一开始说的」问的就是它
        if len(was) > _MAX_REVISIONS:
            was = [was[0], *was[-(_MAX_REVISIONS - 1) :]]
        self.facts[field_name] = {"value": text, "turn": turn, "was": was}
        return True

    def note_requirements(self, profile, turn: int) -> bool:
        """把 Agent 收集到的需求档案镜像进来。返回有没有改动。

        ⭐ **镜像而不是共用同一份**：`profile` 是「出方案还缺什么」的工作状态，
        它会被 `generate_plan` 读、被 `missing()` 判空；事实表是「用户说过什么」的
        账本，要留改口历史、要跨窗口注入。两者的生命周期和读者都不一样，
        合成一份的话，任何一边加个字段都会悄悄改变另一边的行为。
        """
        changed = False
        for f in REQUIREMENT_FIELDS:
            if self.note(f, getattr(profile, f, None), turn):
                changed = True
        return changed

    # ---------- 给模型看 ----------

    def first_value(self, field_name: str) -> str | None:
        """这一项**最早**的说法。没有改口历史时就是当前值。"""
        rec = self.facts.get(field_name)
        if rec is None:
            return None
        was = rec.get("was") or []
        return was[0]["value"] if was else rec["value"]

    def answers(self, question: str) -> str | None:
        """这句话问的是不是表里**已经有**的某一项？是就返回字段名。

        ⭐ 给 `agent/runner.py` 那道「历史被裁掉了」的闸门用。在 W2.2 之前，
        「我一开始说的是哪个版本」会被那道闸门直接短路成一句
        「当前上下文只保留最近几轮，我无法确认」——**而版本这件事从来就不在
        对话记录里**，它是会话创建时钉死的。明明知道却回一句不知道，
        比忘了更糟：用户会以为这个系统连自己选的版本都记不住。

        ⚠️ **只在两个条件都成立时返回**：问句里出现了这一项的别名，
        **并且**表里真的有这一项。差一个都退回原来的边界话术——
        那个方向（说不知道）是安全的，反过来才会让模型去编。
        """
        for f, words in _FIELD_ALIASES.items():
            if f in self.facts and any(w in question for w in words):
                return f
        return None

    def human(self) -> str:
        """注入到 system prompt 里的那一段。空表返回空串（调用方据此不注入）。"""
        lines: list[str] = []
        for f, label in FACT_LABELS.items():
            rec = self.facts.get(f)
            if rec is None:
                continue
            lines.append(f"- {label}：{rec['value']}{_provenance(f, rec)}")
        return _PREFACE + "\n".join(lines) + _EPILOGUE if lines else ""


def _provenance(field_name: str, rec: dict) -> str:
    """这一条是哪来的、改过没有。

    ⭐ **轮次必须写出来。** 「我一开始说的是哪个版本」问的是轮次，
    只给一个当前值的话，模型要么答现在这个（可能是改口后的），
    要么去翻已经被裁掉的对话记录猜——两条都是错的。
    """
    if field_name == "knowledge_space":
        # 这一条不是"说"出来的，是会话创建时钉死的（见 models.Conversation）。
        # 写成「第 1 轮说的」会让模型以为它和别的字段一样可以改口
        return "（本会话开始时选定，中途不可更改）"
    was = rec.get("was") or []
    if not was:
        return f"（第 {rec['turn']} 轮说的）"
    first = was[0]
    return f"（第 {rec['turn']} 轮改的；最早在第 {first['turn']} 轮说的是「{first['value']}」）"


_PREFACE = """

⭐ 本会话**已确认的事实**（系统按结构记录，**不受"只保留最近几轮"的限制**）：

"""

# ⚠️⚠️ 后面这三段一句都别删，每一句挡的是一种具体的错法：
#
#   第 1 句  挡「把事实表当材料引用」——给它标 [n]，用户点开发现来源列表里没有
#   第 2 句  挡「表里没有就去翻对话记录猜」——那正是这一层要消灭的行为
#   第 3 句  挡最贵的那一种：把「仓库数量：4」读成"旺店通有个字段该填 4"。
#            事实表说的是**用户的情况**，不是**产品的配置**。这两件事混起来，
#            就等于给了模型一条绕过"界面路径只能来自材料"的新路
_EPILOGUE = """

这几条是**用户自己说过 / 系统能直接确定**的信息，不是参考材料——
**不要给它们标来源编号 [n]**。
用户问「我一开始说的 X 是什么」「我们是几个仓来着」这类问题时，**以这张表为准**，
不要去翻对话记录猜；表里没有的那一项，就直说这一项没记录到，**不要编**。
⚠️ 这张表只说明**用户的情况**，不是旺店通的配置依据：
界面路径、菜单层级、字段名、参数取值仍然只能来自参考材料。"""
