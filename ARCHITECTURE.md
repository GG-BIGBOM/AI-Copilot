# 架构

一句话：**一台 2 核 / 1.6GB 的机器上跑的 RAG + Agent，所有吃内存的模型推理都在云端。**

服务器上的常驻进程只有四个，加起来约 700MB：

| 进程 | 预算 | 说明 |
|---|---|---|
| nginx | ~40 MB | 发前端静态文件、反代 `/api`、直接发 `/images` |
| PostgreSQL | ~250 MB | `shared_buffers` 调到 128MB |
| FastAPI（uvicorn 单 worker） | ~250 MB | 问答、认证、上传 |
| 解析 worker | ~150 MB | 独立进程，`MemoryMax=400M` |

余量约 300MB，很薄。**架构上的每一个「不做」都是这个数字逼出来的**，
理由见 [DECISIONS.md](DECISIONS.md)。

---

## 一、前端

Next.js 15 + AI SDK 6，`output: 'export'` **静态导出**。

⚠️ **`next build` 峰值吃 1GB+，服务器上跑必 OOM。** 所以流程固定为
「本机构建 → 传 `out/` 产物 → nginx 直接发」。副作用是用不了 Server Actions
和 Route Handlers——本项目所有逻辑都在 FastAPI，**零损失**。

`useChat` 照常工作，走的是 SSE（见下面「流式」）。

---

## 二、FastAPI

```
api/app.py            装配
api/logging_setup.py  中间件：给每个请求一个 X-Request-Id，仅此而已
api/routes/
    auth.py           注册 / 登录 / 登出（JWT 放 HttpOnly cookie）
    chat.py           /api/chat —— 三条路的分叉点，会话与消息的 CRUD
    docs.py           上传、列表、删除
    corrections.py    勘误层（改文档）
    verified.py       答案订正（改问题的标准答案）
    feedback.py       👍👎
api/ratelimit.py      进程内令牌桶
api/trace.py          请求台账（不是中间件，见下）
```

### 认证

JWT 放 **HttpOnly cookie**，不放 localStorage——后者任何 XSS 都能读走。
注册必须带邀请码，**核销判据是 `used_at` 不是 `used_by`**
（`used_by` 是 `ON DELETE SET NULL`，按它判的话删个号就能把码放回池子里）。

---

## 三、检索（RAG）

```
问题
 ├─ 多轮改写：把「那不良品呢」补全成独立问题，**只拿它去检索**
 ├─ 向量召回 top_k=20     Postgres + pgvector，bge-m3（1024 维）
 ├─ 重排 rerank_k=5       bge-reranker-v2-m3
 ├─ 阈值 0.005            只滤明显垃圾（分数绝对值很低，靠相对差距）
 └─ build_context()       拼上下文 + 把 [图:a3f9] 重编号成 [图1][图2]
```

⭐ **重编号和拼上下文必须一起产出**（`ContextBundle`）。分成两个方法算的话，
迟早出现编号和图片对不上——而那种错的表现是「答案配了错误的截图」，
**没有任何报错**。

### 隔离：这个项目唯一错了就不可挽回的规则

```
owner_id IS NULL     公共库（语雀抓来的），所有登录用户可见
owner_id = <uuid>    私有库（用户上传的），仅本人可见
```

`Chunk.owner_id` 是从 `Document` 冗余下来的一份拷贝，为的是检索时直接过滤、
不必 join。**过滤条件只有一处实现**（`retrieve.search`），
`tests/test_isolation.py` 守着它。

⚠️ **`user_id` 只能从 cookie 里的登录态来。** 它绝不能成为 Agent 工具的入参——
一旦可以，一句 prompt injection 就能读到别人的私有文档（见 `agent/deps.py`）。

### 上下文里的来源标签

```
私有  →  [3] 来源：你的文档《客户A-实施配置约定》· 对账规则
公共  →  [3] 来源：公共知识库 · 流程中拆分条件说明
```

只改**送进模型的上下文**，不改页面上的来源清单——用户不需要被提醒
「这篇是你自己传的」，他知道。

---

## 四、生成

`qa.py` 一个文件收口：prompt、闸门、兜底话术、多轮改写。

三道防幻觉的闸门：

```
第一道  检索层    一条都没召回 → 常识兜底关时直接兜底、不调 LLM；
                  **开着时让路**（那正是最该问模型的时候）
第二道  prompt    八条铁律。材料里有的以材料为准；没有的分三种情形（铁律 3）
第三道  guard.py  一个工具都没调却写出**操作步骤**的，一律拦下
```

**第二道是主闸门。** 第一道只滤明显无关的——重排分数的绝对值很低
（实测正确答案 0.02、无关内容 0.0001），靠绝对阈值卡不住。

两档回答：`fast` 简答（DeepSeek）/ `deep` 详解（Kimi）。
**铁律两档完全一样**，差的只有写法和模型。

### 那条红线在哪

```
行业术语、概念解释、通用做法      ✅ 可以用模型自己的知识答，**不标来源编号**
界面路径、菜单层级、字段名、
参数取值、数量上限、平台规则      ❌ 只能来自材料，查不到就说查不到
```

判据是**「错了会不会伤到人」**，不是「知识的来源」。
`ALLOW_GENERAL_KNOWLEDGE=false` 一行退回严格版，两版 prompt 都留在文件里。
量它的那套评测是 `eval/risk_boundary.py`（[EVALUATION.md](EVALUATION.md)）。

---

## 五、Agent 与终结工具

Pydantic AI。工具清单：

```
answer_kb          ⭐ 终结工具：它的返回**就是**最终答案
whoami             自我介绍（和寒暄回复共用同一份文案，不抄第二遍）
current_time       报时间
my_documents       列出「你的文档」
save_requirement   多轮收集需求
generate_plan      生成配置清单
export_excel       导出 xlsx
```

⭐⭐ **`answer_kb` 是终结工具，这是整个 Agent 化的要点。**
它内部跑的是完整的直路（检索 → prompt → 生成），返回的正文直接给用户，
**Agent 不再复述、不再加工**。也就是说 **Agent 永远拿不到原始材料**，
也就没有机会用它自由发挥——M7 那次让 Agent 自由发挥，41 题准确率掉 12 个点、
幻觉率 12.5%，掉的不是常识题，是它「想起来」的界面路径和数值。

多轮状态（`profile` / `checklist` / `export_path`）**落在 conversations 表上**，
不能只放内存：一轮追问跨好几个 HTTP 请求，每个请求都是一次全新的 Agent run。

### 路由（今天仍是三条；P13 门禁通过后才会收敛）

```
用户提问
 ├─ 寒暄短路（canned）      写死的回复，一次模型调用都不花
 ├─ Agent（灰度白名单）
 └─ 直路 _chat_stream       ← P13 当前未通过门禁，旧路保留；条件见 plan.md
```

寒暄那一层**在全 Agent 化之后仍然留着**：「你好」不值得花一次模型调用，
而且让模型自由回招呼语，就是在防幻觉的墙上开一个洞。

---

## 六、流式

自己编 SSE 片段（`api/stream.py`），协议是 AI SDK 的 data stream 格式。

几条不显然的规矩：

- **推理草稿也发**（`reasoning` part）。详解档的 kimi 首个草稿字 1 秒到、
  **首个正文字要 8~60 秒**。不发的话前端那几十秒一个字都没有，
  用户看到的是「选了详解，它不回答」。
- **第一个正文字到了才发 `text-start`**。提前发会让 AI SDK 立刻切到
  `streaming`，前端那句「正在理解问题」消失、换成一条空答案加闪烁光标。
- **配图在正文之前发，引用在正文之后发。** 前端要边流边把 `[图1]` 换成真图；
  而引用必须等看到答案——模型说「暂无此内容」时不能挂来源
  （那是「不知道」底下挂着五条出处）。
- **边流边落库**。用户点停止 / 刷新页面，已经流出来的半截答案还在。

---

## 七、Postgres 队列 + 解析 worker

```sql
SELECT ... FROM jobs WHERE status='pending'
ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
```

没有 Redis、没有 Celery（[DECISIONS.md](DECISIONS.md)）。

worker 是**独立进程**，两个理由都是 1.6GB 逼出来的：解析一份 20MB pptx 是
同步 CPU 活，塞进只有一个 worker 的 API 进程会卡住事件循环；而且内存能分开限
（API 600M / worker 400M），真有人传了个吃爆内存的文件，
被 systemd 收走的是 worker，**网站还在**。

**文档状态和任务状态必须在同一个事务里改**，否则会出现「任务 done、
文档还停在解析中」——页面上就是一个永远转圈的圈。

### 上传解析

```
md / txt      直接读，带 GBK 兜底（中文 Windows 记事本默认不是 UTF-8）
docx / pptx   → Markdown，**标题样式转成 #**（章节路径就是引用里那句「第 2 节 …」）
pdf           pypdf 纯文本；扫描件走视觉模型逐页读（有页数上限，是道花钱的闸门）
图片          视觉模型转写
```

⚠️ docx / pptx **先过 `ingest/zipguard.py`**：它们本质是 ZIP，
20MB 的上传上限管不住解压后有多大。那道检查只读 ZIP 中央目录、不解压，
几毫秒，且先于 python-docx 执行。加上 `PARSER_TIMEOUT`，
再加上 systemd 的 `MemoryMax` + `Restart=always`，三层。

---

## 八、请求台账（request_trace）

⭐ **它不是中间件。** `StreamingResponse` 的响应体是在中间件 `call_next`
**返回之后**才被消费的——中间件那一层看得到 URL 和状态码，
看不到答案、工具、检索命中。真在中间件里写，写出来的是一张
只有「谁在什么时候打了 /api/chat」的表，而那正是 nginx 日志已经有的东西。

所以分工是：中间件给一个 request id，**流的生产者**在答完之后写一整行。

一行里有：路由、模式、问题、工具、召回块数、rerank 最高分、私有块数、模型、
TTFB、总时长、token、答案长度、是否拒答、**答案来源**、ok / error、
以及 👍👎 和原因。

- **👍👎 写在同一张表上，不另建 feedback 表。** 分表且不关联的话，
  一个 👎 就只是个计数器——你复现不了当时检索到了什么。
- **`answer_source`**（M13 P5）：`kb` / `general_knowledge` / `canned` /
  `tool` / `no_answer`。M12 之后「答了但没有出处」成了一件正常的事，
  而在这一列之前，它和「查库答的」在表里每一列上都长得一模一样。
- **写失败绝不影响回答**（整条包在 try 里），**自己开会话**（流里那个可能
  已经半死），**shield 住取消**（被中断的那一轮恰恰最该留下记录）。

保留策略与清理见 [OPERATIONS.md](OPERATIONS.md)。

---

## 九、勘误与订正

两层，改的东西不一样：

```
Correction      改「哪一篇文档」   —— 语雀原文写错了，用这条盖掉它
VerifiedAnswer  改「哪一个问题」   —— 看到答案不对，当场写一个标准答案
```

⚠️ `VerifiedAnswer` **不是检索之外的另一条路**：保存时会写成一篇
`source_type="verified"` 的公共文档 + 若干块，照常向量化、照常参与检索。
别为它单开一套召回——单开就意味着两条召回路径、两套 `owner_id` 隔离规则。

网页写的勘误只进数据库，**绝不往服务器的 `corrections/` 目录写文件**：
那个目录每次部署都会被仓库版本整个覆盖（`deploy.sh` 是 `rm -rf` 再解包）。
`copilot corrections-export` 把它们导成 md 好进版本管理。
