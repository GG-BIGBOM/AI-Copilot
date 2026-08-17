"""后台任务：用 Postgres 当队列，不引入 Redis。

理由见 plan.md「七、约定 5」：不为假想需求先付工程费。1.6GB 的机器上，
`FOR UPDATE SKIP LOCKED` 足够撑住「几个人偶尔传份文档」这个量级，
而多一个 Redis 就是多一个常驻进程、多一份内存、多一个会挂的东西。
"""

from copilot.jobs.queue import PARSE_UPLOAD, claim_next, enqueue, finish, reclaim_stale

__all__ = ["PARSE_UPLOAD", "claim_next", "enqueue", "finish", "reclaim_stale"]
