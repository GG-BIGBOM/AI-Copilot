# 功能清单

> **这份文件回答「有什么」，不回答「为什么」。**
>
> | 想知道 | 看哪 |
> |---|---|
> | 系统**有哪些能力**、每一项在什么状态、入口在哪 | 这里 |
> | 每一层**怎么工作**，隔离和流式在哪里收口 | [ARCHITECTURE.md](ARCHITECTURE.md) |
> | 为什么**不那么做**（不用 Docker / Redis / ES…） | [DECISIONS.md](DECISIONS.md) |
> | 指标口径、A/B 规则、baseline | [EVALUATION.md](EVALUATION.md) |
> | 部署、备份、日志、事故处置 | [OPERATIONS.md](OPERATIONS.md) |
> | **知道了但这一轮没修**的东西 | [ISSUES.md](ISSUES.md) |
>
> 校对基准：`main` @ `3851f73`（2026-09-03）。清单里的每一项都能在代码里
> 指到具体文件，改代码时**这份文件要跟着改**——一份对不上代码的功能清单，
> 比没有更坏：它会让人以为某个开关是开着的。

**状态标记**（全文统一）：

| 标记 | 含义 |
|---|---|
| ✅ | 已上线，生产在跑 |
| 🔸 | 代码在、**默认关**。开关名见[第十六节](#十六配置开关与限额清单) |
| 🧪 | 只在本机 / 评测 / 本地 compose 里用，不上生产 |
| ⛔ | 已移除或明确不做，留一行免得下次再问 |

---

## 一、功能总表

| # | 域 | 能力数 | 一句话 |
|---|---|---|---|
| 二 | 问答与生成 | 16 | 带引用回答、两档模式、三道防幻觉闸门 |
| 三 | 检索（RAG） | 9 | 向量 + 重排为主，词法那一路默认关 |
| 四 | Agent | 8 | 七个工具，`answer_kb` 是终结工具 |
| 五 | 会话 | 9 | 列表、搜索、批删、导出、历史还原 |
| 六 | 文档与语料 | 14 | 上传解析入库、语雀抓取、配图镜像 |
| 七 | 内容治理 | 8 | 两层勘误 + 用户纠错审核队列 |
| 八 | 账号与权限 | 8 | 邀请码注册、JWT cookie、配额与限流 |
| 九 | 管理台 | 6 | 只读概览 + 纠错审核（唯一两个写接口） |
| 十 | 可观测性 | 8 | 一轮一行台账 + span 树（线上未开） |
| 十一 | 评测与门禁 | 7 | 五套题集、六项门禁、退出码非 0 挡部署 |
| 十二 | 运维与部署 | 10 | 七步部署、systemd、备份与恢复演练 |

---

## 二、问答与生成

| 功能 | 状态 | 实现 | 说明 |
|---|---|---|---|
| 带引用回答 | ✅ | `qa.py` | 每句有依据的结论标 `[n]`，点得开、溯得回 |
| 配图渲染 | ✅ | `retrieve.build_context` + 前端 `image-rendering.ts` | 原文有配图的步骤，`[图1]` 边流边换成真图 |
| 两档回答 | ✅ | `mode: fast \| deep` | 简答走 DeepSeek，详解走 Kimi。**防幻觉铁律两档一字不差** |
| 流式输出 | ✅ | `api/stream.py` | 自编 SSE，协议是 AI SDK data stream |
| 推理草稿透传 | ✅ | `reasoning` part | 详解档首个正文字要 8–60s，不发草稿前端就是几十秒空白 |
| 边流边落库 | ✅ | `routes/chat.py` | 用户点停止 / 刷新，半截答案还在 |
| 停止生成 | ✅ | 前端 `composer.tsx` | 中断的那一轮照样进台账（`shield` 住取消） |
| 多轮改写 | ✅ | `qa.py` | 把「那不良品呢」补全成独立问题，**只拿它去检索** |
| 寒暄短路 | ✅ | `qa.small_talk_reply` | 招呼/道谢/告别/问能力，一次模型调用都不花 |
| 常识兜底 | ✅ | `ALLOW_GENERAL_KNOWLEDGE=true` | 行业术语可按通用理解答且**不标来源**；界面路径/字段/上限绝不编 |
| 拒答 | ✅ | 铁律 3 | 「知识库暂无此内容」是一等公民，不是失败 |
| 提示注入防线 | ✅ **默认开** | `injection.py` + 材料围栏 + 私有块摘链接 | A/B：注入成功率 44.4% → 0.0% |
| 越过工具直答硬拦截 | ✅ | `agent/guard.py` | 一个工具都没调却写出**操作步骤**的，一律拦下 |
| 窗口外指代闸门（直路侧） | 🔸 | `DIRECT_BOUNDARY_ENABLED` | Agent 路一直有；直路缺口仍在（[ISSUES.md](ISSUES.md) I-9） |
| 上下文预算装配器 + 滚动摘要 | 🔸 | `HISTORY_BUDGET_ENABLED` | A/B：长会话跨窗口解析 54.5% → 90.9%。**摘要不调模型** |
| 会话级已确认事实 | 🔸 | `SESSION_FACTS_ENABLED` | ⚠️ 关着时**照常记录**（写库、跨轮累积），只是不注入 prompt |

**三道防幻觉闸门**（`qa.py` 收口）：

```
第一道  检索层    一条都没召回 → 兜底关时直接兜底不调 LLM；开着时让路
第二道  prompt    八条铁律。← 主闸门
第三道  guard.py  没调工具却写出操作步骤 → 拦下
```

---

## 三、检索（RAG）

| 功能 | 状态 | 参数 / 开关 | 说明 |
|---|---|---|---|
| 向量召回 | ✅ | `RETRIEVE_TOP_K=20`，bge-m3 / 1024 维 | Postgres + pgvector |
| 重排 | ✅ | `RERANK_TOP_K=5`，阈值 `0.005` | bge-reranker-v2-m3。阈值只滤明显垃圾 |
| 词法召回 + RRF 融合 | 🔸 | `HYBRID_ENABLED=false` | jieba → tsvector + GIN → `ts_rank_cd`，RRF k=60 |
| 高频词过滤 | 🔸 | `HYBRID_DF_MAX_RATIO=0.02` | ⚠️ 词法那一路**最要紧的一步**，判据是语料自己的文档频率 |
| 公共 / 私有隔离 | ✅ | `owner_id`（`Chunk` 上冗余一份） | **这个项目唯一错了就不可挽回的规则**，`tests/test_isolation.py` 守着 |
| 知识版本隔离 | ✅ | `knowledge_space_id` / `_space_filter` | 机制在，但今天只有 `flagship` 一个可选空间 |
| 标准答案优先命中 | ✅ | `verified.py` | 命中时**一次模型调用都不花**，台账记 `answer_source=verified` |
| 来源标签 | ✅ | `build_context` | 私有块标「你的文档《…》」，公共块标「公共知识库」。**只改送进模型的上下文** |
| 上下文与图片编号同产 | ✅ | `ContextBundle` | 分两个方法算迟早出现「答案配了错误的截图」，且**没有任何报错** |

⚠️ 词法那一路三个前提缺一个都**静默退回纯向量**：没装 jieba、没跑迁移、
没回填 `content_tsv`。静默是刻意的。

⛔ **混合检索一度默认开，2026-08-29 被付费评测打回 `false`**：救活了裸粘贴
关键词召回（6/15 → 15/15），同时把幻觉率从 0% 顶到 10%。推翻记录在 ADR-16。

---

## 四、Agent

**七个工具**（`agent/agent.py::TOOLS`）：

| 工具 | 前端标签 | 作用 |
|---|---|---|
| `answer_kb` | 查知识库 | ⭐ **终结工具**：返回的正文直接给用户，Agent 不复述不加工 |
| `whoami` | 自我介绍 | 和寒暄回复共用同一份文案 |
| `current_time` | 查当前时间 | 报北京时间 |
| `my_documents` | 查我的文档 | 列「你的文档」 |
| `save_requirement` | 记录需求 | 多轮收集实施需求（七个字段） |
| `generate_plan` | 生成配置方案 | 生成配置清单 |
| `export_excel` | 导出 Excel | 三个工作表：清单 / 待确认 / 生成信息 |

| 功能 | 状态 | 说明 |
|---|---|---|
| 终结工具收口 | ✅ | M7→M10：准确率 87.8% → **100%**，幻觉率 12.5% → **0%** |
| 多轮需求收集 → 方案 → xlsx | ✅ | 状态（`profile`/`checklist`/`export_path`）落在 `conversations` 表上，不能只放内存 |
| 灰度路由 | ✅ | 白名单先于百分比。⚠️ 线上现为 `AGENT_ROLLOUT=1.0`，实际没人走直路 |
| 跑飞硬闸门 | ✅ | 问答档 3 请求 / 3 工具；出方案档 8 请求 / **16 工具**（曾按 8 炸过线上） |
| 工作过程可视化 | ✅ | `agent-trace.tsx`，做完自己收起来；**不显示工具返回的原始内容** |
| 输出校验器 | ✅ | 收集需求中却一个工具都没调 → `ModelRetry` |
| `search_kb`（材料级工具） | ⛔ | 函数还在，**不挂在主 Agent 上**——挂上去 M10 等于白做 |
| 校验 Agent（W3.2） | ⛔ | 2026-08-30 移除。四条硬指标 0.0% → 0.0%，没动（ADR-22） |

**三条路由**（`routes/chat.py`，顺序不能反）：

```
用户提问
 ├─ 寒暄短路 canned     ← 已在多轮流程里的会话不走这条
 ├─ Agent（灰度）
 └─ 直路 _chat_stream   ← P13 门禁未通过，旧路保留
```

---

## 五、会话

| 功能 | 状态 | 入口 |
|---|---|---|
| 新建 / 自动命名 | ✅ | 首句问题截断成标题（`_title_from`），**没有重命名接口** |
| 会话列表 | ✅ | `GET /api/conversations` |
| 会话搜索 | ✅ | Ctrl/Cmd + K（`conversation-search.tsx`） |
| 历史消息还原 | ✅ | 引用、配图、👍👎 状态一起还原（`trace_id` 塞进同名片段） |
| 单条删除 / 批量删除 | ✅ | `DELETE /api/conversations/{id}`、`POST /api/conversations/bulk-delete` |
| 答案导出 Markdown | ✅ | `lib/export-answer.ts`，纯字符串拼接，可测 |
| 答案导出 PDF | ✅ | ⭐ **走浏览器打印**，不做服务端渲染（内存 + AGPL 许可两条红线各否掉一种做法） |
| 方案 xlsx 下载 | ✅ | `GET /api/conversations/{id}/export` |
| 侧边栏折叠记忆 | ✅ | localStorage + `useSyncExternalStore` |

---

## 六、文档与语料

### 用户上传

| 功能 | 状态 | 限额 | 说明 |
|---|---|---|---|
| 上传 | ✅ | 20MB / 份，200 份 / 人 | 立刻返回 `pending`，**不在请求里解析** |
| md / txt | ✅ | — | 带 GBK 兜底（中文 Windows 记事本默认不是 UTF-8） |
| docx / pptx | ✅ | — | → Markdown，**标题样式转成 `#`**（章节路径就是引用里那句「第 2 节 …」） |
| xlsx | ✅ | — | 一个工作表一节；嵌图是「有限支持」（openpyxl 私有属性） |
| pdf | ✅ | 扫描件 ≤ 20 页 | pypdf 纯文本；扫描件逐页走视觉模型（**一道花钱的闸门**） |
| 图片 | ✅ | — | 视觉模型转写。⚠️ 能不能真解析取决于 `VISION_API_KEY` |
| 嵌图解析 | ✅ | 30 张 / 篇，≥ 4KB | 太小的多半是图标和分隔线 |
| ZIP 炸弹防护 | ✅ | `ingest/zipguard.py` | 只读 ZIP 中央目录、不解压，且**先于 python-docx 执行** |
| 内容去重 | ✅ | `content_hash` | 同一个人传同一份文件不重复烧 embedding |
| 失败重传 | ✅ | 复用同一行 | 免得列表里堆一串同名失败记录 |
| 状态轮询 | ✅ | 只在有 pending/parsing 时轮询 | 不上 WebSocket（1.6GB） |
| 删除 | ✅ | 级联删块与图 | `tests/test_delete_lifecycle.py` |

### 公共语料

| 功能 | 状态 | 命令 |
|---|---|---|
| 语雀抓取 | ✅ | `copilot sync-yuque`（限速 1.5/s） |
| 配图镜像 | ✅ | `MIRROR_IMAGES=true`。⚠️ 语雀 CDN 防盗链，带 Referer 取图直接 403 |
| 本地 markdown 灌入 | ✅ | `copilot ingest` |
| 垃圾块清理 | ✅ | `copilot prune-junk`（语雀内嵌表格的压缩载荷） |
| 词法索引回填 | ✅ | `copilot backfill-tsv` |
| lakesheet 表格文档 | ⛔ | 50 篇 0 块、一个字都搜不到。**决定不写解析器**（ISSUES I-13） |

### 图片出口

```
公共图   nginx 直接发 /images/            静态，谁猜中文件名谁就能取
私有图   GET /api/images/{id}             后端逐次校验 owner
```

⚠️⚠️ 两者**物理分目录**（`assets.absolute_path` 按 `owner_id` 决定路径）。
分目录不是「再加一道保险」，是**让写错的那种代码写不出来**。

---

## 七、内容治理

两层，改的东西不一样：

| 功能 | 状态 | 表 | 谁能做 |
|---|---|---|---|
| 文档级勘误 | ✅ | `corrections` | 登录用户 |
| 答案订正（标准答案） | ✅ | `verified_answers` | **管理员**（写接口挂 `CurrentAdmin`） |
| 订正版本历史 | ✅ | `verified_answer_revisions` | 只读 |
| 用户提交纠错 | ✅ | `answer_corrections` | 登录用户，**进审核队列** |
| 纠错截图 | ✅ | ≤ 5MB，未提交悬空上限 20 张 / 人 | 先传后绑 |
| 管理员审核（通过/拒绝） | ✅ | 留 `reviewed_by`/`reviewed_at`/`review_note` | 管理员 |
| 发布成标准答案 | ✅ | 额外留一版 `VerifiedAnswerRevision` | 管理员 |
| 勘误导出 md | ✅ | `copilot corrections-export` | CLI |

⚠️⚠️ **M16 起：提交 ≠ 生效。** 在那之前任何登录用户点一下保存，
那段文字就对全站立刻生效、无人审核。

⚠️ `VerifiedAnswer` **不是检索之外的另一条路**：保存时写成一篇
`source_type="verified"` 的公共文档 + 若干块，照常向量化、照常参与检索。

⚠️ 网页写的勘误**只进数据库**，绝不往服务器 `corrections/` 目录写文件——
那个目录每次部署都会被仓库版本整个覆盖（`deploy.sh` 是 `rm -rf` 再解包）。

---

## 八、账号与权限

| 功能 | 状态 | 说明 |
|---|---|---|
| 邀请码注册 | ✅ | ⚠️ **核销判据是 `used_at` 不是 `used_by`**（后者 `ON DELETE SET NULL`，删个号就能把码放回池子） |
| 登录 / 登出 / me | ✅ | JWT 放 **HttpOnly cookie**，不放 localStorage |
| 邀请码生成 | ✅ | 网页（管理员）+ `copilot invite` |
| 管理员位 | ✅ | `users.is_admin`，`copilot admin <email>` 设置 |
| 每日 token 配额 | ✅ | `users.daily_token_quota` + `token_usage` 表；超额 429。⭐ 检查必须在 `StreamingResponse` **之前** |
| 限流 | ✅ | login 20/5min · register 5/1h · chat 20/1min，进程内令牌桶 |
| 本机免限流 | ✅ | `EXEMPT_IPS`。⭐ 否则评测跑到第 20 题全变错误，排查方向会被带到模型上 |
| 演示账号 | 🧪 | `copilot seed-user`，**绕过邀请码**，只给 docker-compose 用 |

---

## 九、管理台

全部 `CurrentAdmin`，**默认只读**：

| 页面 / 接口 | 状态 | 说明 |
|---|---|---|
| 概览（24h / 7d / 30d） | ✅ | ⚠️ **不出现任何原始问题文本**——后端就不给，不是靠前端不显示 |
| 用户列表 + 详情 | ✅ | 分页**在 SQL 里做**（1.6GB，Python 切片等于把整张表读进内存） |
| 反馈中心 | ✅ | 👍👎 + 差评原因，可翻回当时的检索情况 |
| 纠错审核队列 | ✅ | M16 引入的**两个写接口**（review / publish），各自留审计记录 |
| 启用 / 禁用用户 | ⛔ | 属于 M15-B，要配审计记录，未做 |
| 评测结果页 | ⛔ | 进 M19-B，和评测中心一起做 |

⚠️ 前端 `/admin` guard **只管体验**，挡不住任何一个会开控制台的人。
鉴权在服务端，每个接口都挂 `CurrentAdmin`，一个都不能漏。

---

## 十、可观测性

| 功能 | 状态 | 说明 |
|---|---|---|
| `request_trace` 一轮一行 | ✅ | ⭐ **不是中间件**——中间件看不到答案、工具、检索命中 |
| 👍👎 + 六类差评原因 | ✅ | `wrong` / `incomplete` / `should_know` / `bad_source` / `unclear` / `no_image`。**写在同一张表上**，不另建 feedback 表 |
| `answer_source` 六值 | ✅ | `kb` / `general_knowledge` / `canned` / `tool` / `no_answer` / `verified` |
| M19-B 补的四列 | ✅ | `verified_answer_id` / `correction_id` / `general_knowledge_used` / `image_count`。⚠️ 全部可空、老数据不回填 |
| 写失败不影响回答 | ✅ | 整条包在 try 里，自己开会话，`shield` 住取消 |
| span 树（OpenTelemetry） | 🔸 | `TRACING_ENABLED=false`，**且是可选依赖**（`uv sync --extra obs`） |
| 质量报告 | ✅ | `copilot quality-report --days 30`，和管理台**同一套函数** |
| 台账清理 | ✅ | `copilot prune-traces`，**默认只预演** |

⚠️ **线上追踪默认关着**，所以「p95 的 9.8 秒花在哪一段」目前**说不出来**。
拿到线上 p95 的分段构成之前，不动任何缓存或并行的优化。

---

## 十一、评测与门禁

### 题集

| 文件 | 题数 | 量什么 |
|---|---|---|
| `eval/dataset.yaml` | 94 | 公共库 / 私有库，直路与 Agent 两条路径 |
| `eval/risk_boundary.yaml` | 56 | 风险边界：高风险幻觉、假引用、提示注入 |
| `eval/routing.yaml` | 63 | 路由判对没有 |
| `eval/keyword.yaml` | 45 | W1.2 混合检索的 A/B（30 完整问句 + 15 裸粘贴） |
| `eval/longchat.yaml` | 11 | 长会话跨窗口指代 |

### 门禁（`eval/gate.py`）

六项：公共库直路 / 公共库 Agent / 私有库直路 / 私有库 Agent / 风险边界 / 路由。
（跨空间那一项随 M18 一起移除。）

```
PASS         达标、可信、没过期
FAIL         破线，或者根本没有这一套的证据
UNRELIABLE   有证据，但判分器失效率超线，或语料指纹对不上
             ⚠️ UNRELIABLE 不是通过
```

红线（`==0`）四条，都是**会伤到人**的：高风险幻觉、假引用、跨 ERP 版本串台、
提示注入照做。✅ 2026-08-30 重跑为 PASS，退出码 0。

⭐ **门禁读的是证据，不是重新制造证据**——跑一次全量是两百多次付费调用。
所以每条证据都带 `max_age_days` 和语料指纹。

**判分器**：`deepseek-reasoner`，⚠️ 必须和答题模型不同源。
**`INVALID`（判分器自己挂了）不计入准确率。**

### 测试

| | 数量 | 命令 |
|---|---|---|
| 后端 | 55 个测试文件 / 846 用例 | `uv run pytest` |
| 前端 | 5 个 `lib/*.test.ts` | `npm run verify`（单测 + lint + 类型 + 构建） |
| 迁移 | 22 个 alembic 版本 | `uv run alembic upgrade head` |

⚠️ 前端**只有 `npm run verify` 这一条命令**，CI 和 `deploy.sh` 调的是同一个。
清单曾经抄在三个地方，2026-08-25 就是这么破的——`tests/test_ci_contract.py` 现在盯着。

⚠️ 单跑一个后端测试文件要带 `-c pyproject.toml`，否则 rootdir 变成仓库根，
读不到 `asyncio_mode=auto`。

---

## 十二、运维与部署

| 功能 | 状态 | 文件 |
|---|---|---|
| 七步部署 | ✅ | `deploy/deploy.sh <ai\|other\|security>` |
| 新机器初始化 | ✅ | `deploy/setup-server.sh` |
| 安全加固 | ✅ | `deploy/harden.sh`（关 SSH 密码登录、装 fail2ban）。**要在放数据之前** |
| nginx 配置 | ✅ | `deploy/nginx.conf` |
| systemd 常驻 | ✅ | `copilot-api.service`（600M）、`copilot-worker.service`（400M） |
| systemd 定时 | ✅ | `copilot-backup` / `copilot-prune` / `copilot-sync`（各 `.service` + `.timer`） |
| 备份 | ✅ | `deploy/backup.sh`（机上）、`backup-pull.sh`（拉回本机） |
| 恢复演练 | ✅ | `deploy/restore-drill.sh`（已做过一轮） |
| 部署 commit 记录 | ✅ | 服务器上 `/opt/copilot/DEPLOYED`（2026-09-02 起才有） |
| 本地一键跑 | 🧪 | `docker compose up`。⛔ **生产不用 Docker**（ADR-1，1.6GB） |

**服务器上永不执行 `npm run build`、永不加载 ML 模型**——这两条是生死线。

⚠️ 服务器地址**不在仓库里**（公开仓库），放在 `deploy/.env`。没填的话
`deploy.sh` 直接报错退出，而不是「默认推到某台机器」。

---

## 十三、HTTP 接口清单

### 认证 `/api/auth`

| 方法 | 路径 | 鉴权 |
|---|---|---|
| POST | `/register` | 公开（需邀请码） |
| POST | `/login` | 公开 |
| POST | `/logout` | 公开 |
| GET | `/me` | 登录 |

### 问答与会话 `/api`

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/chat` | 登录 | SSE 流。401 未登录 / 429 超配额 |
| GET | `/conversations` | 登录 | 会话列表 |
| GET | `/conversations/{id}/messages` | 登录 | 历史消息（含引用、配图、反馈） |
| GET | `/conversations/{id}/export` | 登录 | 下载方案 xlsx |
| DELETE | `/conversations/{id}` | 登录 | |
| POST | `/conversations/bulk-delete` | 登录 | |

### 文档 `/api/documents`

| 方法 | 路径 | 鉴权 |
|---|---|---|
| POST | `` | 登录 |
| GET | `` | 登录 |
| DELETE | `/{id}` | 登录 |

### 勘误与订正

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET/POST | `/api/corrections` | 登录 | 文档级勘误 |
| DELETE | `/api/corrections/{id}` | 登录 | |
| POST | `/api/answer-corrections` | 登录 | 提交纠错（进审核队列） |
| POST | `/api/answer-corrections/images` | 登录 | 贴截图，先传后绑 |
| GET | `/api/answer-corrections/mine` | 登录 | |
| GET | `/api/answer-corrections/{id}` | 登录 | |
| GET | `/api/answer-corrections/{id}/markdown` | 登录 | |
| PATCH | `/api/answer-corrections/{id}` | 登录 | |
| GET | `/api/verified` | 登录 | 标准答案列表 |
| GET | `/api/verified/{id}/revisions` | 登录 | 版本历史 |
| POST | `/api/verified` | **管理员** | |
| DELETE | `/api/verified/{id}` | **管理员** | 退役 |

### 其他

| 方法 | 路径 | 鉴权 |
|---|---|---|
| POST | `/api/feedback` | 登录 |
| GET | `/api/feedback/recent` | 登录 |
| GET | `/api/images/{id}` | 可选登录（私有图校验 owner） |
| GET | `/api/knowledge-spaces` | 登录 |
| GET/POST | `/api/invites` | 管理员 |

### 管理台 `/api/admin`（全部 `CurrentAdmin`）

| 方法 | 路径 |
|---|---|
| GET | `/overview` |
| GET | `/users` · `/users/{id}` |
| GET | `/feedback` |
| GET | `/corrections` · `/corrections/{id}` |
| POST | `/corrections/{id}/review` · `/corrections/{id}/publish` |

---

## 十四、CLI 清单（`copilot <cmd>`）

| 命令 | 作用 |
|---|---|
| `serve` | 启动 API 服务 |
| `worker` | 启动后台解析 worker |
| `ingest` | 把本地 Markdown 切分、向量化、写入公共库（或某人的私有库） |
| `sync-yuque` | 抓取语雀公开知识库到本地 |
| `ask` | 检索知识库并生成带引用的答案 |
| `invite` | 生成注册邀请码 |
| `admin` | 把某个账号设为管理员 |
| `seed-user` | 🧪 建演示账号，**绕过邀请码**，只给 compose 用 |
| `correct` | 修正语雀文档里写错的内容 |
| `corrections` | 列出所有人工勘误，并标出哪些已经过期 |
| `corrections-export` | 把网页上写的勘误导成 `corrections/*.md` |
| `backfill-tsv` | 给存量块补上词法索引 `content_tsv` |
| `prune-junk` | 清掉索引里的二进制垃圾块 |
| `prune-images` | 清掉没人认领的纠错截图。**默认只预演** |
| `prune-traces` | 按保留策略清理 `request_trace`。**默认只预演** |
| `quality-report` | 最近 N 天的质量与成本概览（默认 7 天） |

⚠️ 服务器上只能 `.venv/bin/copilot`；本机 `backend/` 下别裸跑 `uv sync`
（会卸掉 `parse`/`agent`/`eval` extra），要 `uv sync --all-extras`。

---

## 十五、前端页面

| 路径 | 说明 |
|---|---|
| `/` | 分流：登录了去 `/chat`，没登录去 `/login` |
| `/login` · `/register` | 注册需邀请码 |
| `/chat` | 主界面。侧边栏 + 会话 + Composer |
| `/documents` | 知识库工作台：上传、看解析状态、删除 |
| `/admin` | 概览 |
| `/admin/users` · `/admin/feedback` · `/admin/corrections` | 管理台三个子页 |

**关键交互约定**：Enter 发送 · Shift+Enter 换行 · **中文输入法选词时的 Enter
永远不发送** · Ctrl/Cmd+K 搜会话。

⚠️ Composer 底下**只放真接了后端的控件**（今天只有回答档位）。
摆一个点了没反应的图标，比少一个功能更伤信任。

⚠️ 没有 `traceId` 就不显示 👍👎——宁可少一个按钮，也不要一个点下去悄悄失败的。

---

## 十六、配置开关与限额清单

### 功能开关

| 开关 | 默认 | 影响 |
|---|---|---|
| `ALLOW_GENERAL_KNOWLEDGE` | `true` | 知识库没有时能否用模型常识答 |
| `INJECTION_GUARD_ENABLED` | **`true`** | 材料围栏 + 私有块摘链接 |
| `AGENT_ENABLED` / `AGENT_ROLLOUT` / `AGENT_ALLOW_EMAILS` | 线上 rollout=1.0 | Agent 灰度（**按用户分桶，不是按请求**） |
| `HYBRID_ENABLED` | `false` | 词法召回 + RRF |
| `HISTORY_BUDGET_ENABLED` | `false` | 上下文预算装配器 + 滚动摘要 |
| `SESSION_FACTS_ENABLED` | `false` | 已确认事实注入 prompt（记录不受影响） |
| `DIRECT_BOUNDARY_ENABLED` | `false` | 直路的窗口外指代闸门 |
| `TRACING_ENABLED` | `false` | OpenTelemetry span 树 |
| `MIRROR_IMAGES` | `true` | 语雀配图镜像到本地 |

⭐ 这一串开关有一条共同的规矩：**凡是「会让答案变、但绝不会报错」的改动，
一律先做成开关、默认关，等 A/B 数字出来再谈开不开。**

### 限额

| 项 | 值 |
|---|---|
| 上传单文件 / 每人份数 | 20MB / 200 |
| 文档嵌图 | 30 张/篇，≥ 4KB |
| 纠错截图 | ≤ 5MB，未提交悬空 ≤ 20 张/人 |
| 扫描件 PDF | ≤ 20 页，150 DPI |
| Agent 问答档 | 3 请求 / 3 工具 |
| Agent 出方案档 | 8 请求 / **16 工具** |
| 限流 | login 20/5min · register 5/1h · chat 20/1min |
| 检索 | top_k 20 → rerank 5，阈值 0.005 |
| 历史窗口 | 关：`history[-6:]` × 600 字；开：1200 字预算 + 400 字摘要 |

---

## 十七、数据模型

| 表 | 作用 |
|---|---|
| `users` | 账号、`is_admin`、`daily_token_quota` |
| `invite_codes` | 邀请码。**核销看 `used_at`** |
| `knowledge_spaces` | 知识版本。今天只有 `common` + `flagship` |
| `documents` | 文档。`owner_id IS NULL` = 公共库 |
| `chunks` | 块。`owner_id` / `knowledge_space_id` 从 document **冗余**下来，检索时直接过滤 |
| `image_assets` | 配图。`source` 分 document / correction，私有图物理分目录 |
| `jobs` | Postgres 队列，`FOR UPDATE SKIP LOCKED` |
| `conversations` | 会话 + 多轮状态（`profile` / `checklist` / `export_path` / `facts`） |
| `messages` | 消息 + 引用 + 配图 |
| `request_trace` | ⭐ 一轮一行的台账，👍👎 也写在这张表上 |
| `token_usage` | 按天按人的 token 与请求数 |
| `corrections` | 文档级勘误 |
| `verified_answers` + `verified_answer_revisions` | 标准答案与版本历史 |
| `answer_corrections` | 用户提交的纠错，带审核状态 |

---

## 十八、明确不做 / 已移除

| 项 | 状态 | 理由在哪 |
|---|---|---|
| Docker（生产） | ⛔ | ADR-1，机器只有 1.6GB |
| Redis / Celery | ⛔ | Postgres `FOR UPDATE SKIP LOCKED` 已经在生产跑着 |
| Elasticsearch | ⛔ | Postgres `tsvector` + GIN 够用 |
| 独立向量库 | ⛔ | pgvector 够用；**能说清「什么规模才需要换」比换掉它值钱** |
| Server Actions / Route Handlers | ⛔ | 静态导出，所有逻辑在 FastAPI，零损失 |
| 服务端 PDF 渲染 | ⛔ | 内存 + AGPL 许可两条红线 |
| WebSocket（解析状态） | ⛔ | 几十秒才变一次的状态不值一条常驻连接 |
| 多知识版本（原 M18） | ⛔ | 2026-08-30 移除，隔离机制本身保留（ADR-23） |
| 校验 Agent（W3.2） | ⛔ | 指标没动，只可能把对的答案降级成拒答（ADR-22） |
| MCP server（W3.1） | ⛔ | 2026-08-30 移除 |
| lakesheet 解析器 | ⛔ | 50 篇封顶且全是纯表格，和核心场景不匹配（ISSUES I-13） |
| `/api/admin/evaluations` | ⛔ | 进 M19-B，现在做会被 M19-A 的契约整个推翻 |
| 启用 / 禁用用户 | ⛔ | 属于 M15-B，要先配审计记录 |

---

## 十九、还没做的

| 里程碑 | 状态 | 下一步 |
|---|---|---|
| M19-B · 评测中心与持续回归 | 🟡 第 1 项（`request_trace` 补四列）已上线 | 趋势页 + 定时回归 |
| M20 · 生产验证与 Agent 路由收敛 | ⬜ 前置条件 4 缺 2 | 扩大真实使用样本，按生产数据决定路由收敛 |

逐条待办见 [plan.md](plan.md)（**只留还没做的事**），
「知道了但这一轮没修」见 [ISSUES.md](ISSUES.md)（每条都写「什么条件下必须修」）。
