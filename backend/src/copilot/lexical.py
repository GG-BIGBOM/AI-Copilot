"""中文词法检索：jieba 分词 + Postgres 全文索引 + RRF 融合。W1.2。

**为什么要加这一路。** 纯向量对**专有名词、物流编码、版本号、字段名、报错串**
最弱——它们在 embedding 空间里没有"意思"，`JTSD` 和 `YUNDA` 挨得很近，
而问的人要的恰恰是那一个格子。而 ERP 的问题里全是这些。

⚠️ **它不放宽任何东西。** 词法召回只做一件事：把一些块塞进**候选池**，
让它们获得一次被重排打分的机会。最终留谁仍然由重排分和阈值决定——
和 M11 P3 的私有库召回名额是同一个形状（见 `retrieve.PRIVATE_RECALL_K`）。
所以这条路径**不可能**把一个低于阈值的东西放进答案里。

⚠️⚠️ **可见性和空间过滤照旧只有一处。** 词法查询的 where 直接用
`retrieve._visibility_filter` / `retrieve._space_filter`，不在这里抄一份。
这是第三条召回路径，而旁路正是隔离最容易漏的地方——M11 P3 那个私有库旁路
当初就漏了空间过滤（见 `retrieve.py` 里那段 ⚠️⚠️）。

**为什么不引 Elasticsearch。** 一台 1.6GB 内存的机器，ES 的 JVM 自己就要
1GB 起步。Postgres 的 `tsvector` + GIN 是同一个进程里的事，
本项目的语料是 5000 块量级——差的那点检索质量，远小于多一个必须运维的
有状态服务带来的代价。这条写进 DECISIONS.md 的 ADR。

**为什么是 `simple` 配置。** Postgres 没有中文分词配置，`chinese` 不存在。
做法是**在 Python 侧用 jieba 切好、用空格连起来**，再交给 `simple`——
`simple` 不做词干还原、不删停用词，正好是"你给我什么词我就索引什么词"。
换成 `english` 会把 `JTSD` 之类的编码做词干还原，那正是我们要保住的东西。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 一次查询最多带几个词。中文长句切出四五十个词很常见，而 `to_tsquery`
# 的词数直接决定 GIN 扫多少个 posting list——不封顶的话，
# 一句「把整段报错贴进来」的提问能让这条 SQL 跑上几秒
MAX_QUERY_TERMS = 32

# 切出来但没有检索价值的词。**不是停用词表**——这里只滤掉纯标点和空白。
# 真正的停用词（"的""了"）留着不删：它们在 `simple` 配置下确实会进索引，
# 但 `ts_rank_cd` 对高频词本来就给低权重，而手工维护一张中文停用词表
# 是个只会越来越错的东西
_JUNK_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)

_jieba: Any = None
_jieba_missing = False


def _get_jieba() -> Any:
    """懒加载 jieba。**没装就返回 None，不抛。**

    `HYBRID_ENABLED` 默认关，服务器上也默认不装 `hybrid` 这个 extra。
    打开开关却没装包时该退回纯向量检索——那是个**能用**的系统，
    而抛异常会把每一次提问都变成 500。
    """
    global _jieba, _jieba_missing
    if _jieba is not None or _jieba_missing:
        return _jieba
    try:
        import jieba

        # jieba 默认往 stderr 打一段初始化日志，systemd 下会污染 journal
        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    except ImportError:
        _jieba_missing = True
        logger.warning(
            "HYBRID_ENABLED=true 但没装 jieba，本次退回纯向量检索。"
            "装法：cd backend && uv sync --extra hybrid"
        )
    return _jieba


def available() -> bool:
    return _get_jieba() is not None


def cut(text: str) -> list[str]:
    """切词。**搜索引擎模式**——长词会被再切一遍，短词多、召回高。

    ⚠️ 索引侧和查询侧必须用**同一个**切法。用了不同模式的表现是
    「明明库里有这个词却搜不到」：索引里存的是「电子面单」，
    查询切出来的是「电子」「面单」，对不上就是零召回，而且没有任何报错。
    """
    jb = _get_jieba()
    if jb is None:
        return []
    return [t for t in jb.cut_for_search(text or "") if t.strip() and not _JUNK_RE.match(t)]


def tokenize(text: str) -> str:
    """入库用：切好的词用空格连起来，交给 `to_tsvector('simple', …)`。"""
    return " ".join(cut(text))


def _escape_lexeme(term: str) -> str:
    """把一个词包成 `to_tsquery` 认的带引号词素。

    ⚠️ **必须转义，而且必须先转反斜杠。** 用户的问题里出现一个单引号
    （"客户说'单子卡住了'"）时，不转义拼出来的是一段语法错误的 tsquery，
    Postgres 直接抛 `syntax error in tsquery` —— 表现是这一轮提问 500，
    而它看起来完全是一句正常的话。
    """
    return "'" + term.replace("\\", "\\\\").replace("'", "''") + "'"


def query_terms(query: str) -> list[str]:
    """切词、去重、封顶。**保序**——去重靠 dict，不靠 set，
    因为词序决定了词太多时砍掉的是哪几个。"""
    return list(dict.fromkeys(cut(query)))[:MAX_QUERY_TERMS]


def lexemes(terms: list[str]) -> list[str]:
    """把词包成 `to_tsquery` 认的带引号词素，一词一个。"""
    return [_escape_lexeme(t) for t in terms]


def or_query(terms: list[str]) -> str:
    """用 `|` 连成一个 OR 查询。空列表返回空串。

    ⭐ **是 OR 不是 AND。** `plainto_tsquery` 把所有词 AND 起来，
    那意味着「京东电子面单模板怎么设置」要求块里同时出现全部六个词——
    真正含答案的那一块多半只有其中四个，于是零召回。
    这一路负责"广"，"准"由后面的重排负责，和向量召回的分工完全一样。
    """
    return " | ".join(lexemes(terms))


def to_tsquery(query: str) -> str:
    """一步到位：切词 → OR 查询。**不做稀有度筛选**（那要连库，见
    `retrieve._rare_terms`）。留着给测试和不需要连库的调用方用。"""
    return or_query(query_terms(query))


def rrf_fuse(
    ranked_lists: list[list[Any]], *, k: int = 60, limit: int | None = None
) -> list[Any]:
    """Reciprocal Rank Fusion。**纯函数，不碰库也不碰 jieba。**

        score(d) = Σ 1 / (k + rank(d))          rank 从 1 起

    ⭐ **为什么是 RRF 而不是给两路分数加权求和。**
    向量的 cosine 距离和 `ts_rank_cd` 的分数**没有可比的量纲**：
    前者在 0~2 之间、后者能到几十，而且两者的分布随查询变化。
    加权求和要先做归一化，而归一化的参数得靠调——调出来的那组数
    只对标定它的那批查询成立。RRF 只看名次，没有任何要调的东西。

    `k=60` 是原论文（Cormack 2009）的取值。它决定"排名差一位值多少分"：
    k 越小，头部名次权重越大。

    ⚠️ 元素要能进 set 做键（这里传的是 chunk id）。
    融合结果按融合分降序；**同分时按第一个列表里的先后**——
    不定序的话同一份语料能跑出两个不同的候选池，评测就再也复现不了。
    """
    scores: dict[Any, float] = {}
    order: dict[Any, int] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            order.setdefault(item, len(order))
    fused = sorted(scores, key=lambda it: (-scores[it], order[it]))
    return fused[:limit] if limit else fused
