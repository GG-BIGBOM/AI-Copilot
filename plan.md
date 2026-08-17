# 知识库 Agent — 2026 主流栈实施方案

## Context

### 为什么重来

前两次尝试死在同一个地方：

1. **Fork Onyx**（2026-08-13 起）——选型逻辑对（MIT 双许可，不堵死商业化），但 20 万行产品级代码，删 `ee/` 后每次 merge upstream 都要人工判冲突，一个人维护不住；且 **Docker 至今未装**，部署从第一天就阻塞。目录已清空。
2. **erp-copilot**——路线图铺到 M7 + MCP 二期，实际只有 2 个 commit 和一个 `/hello` 骨架，语料区空的。终点太远，没走到有反馈的地方。

**这次的核心约束：让"别人打开网址就能用"的时间点尽早出现。** M5 就是那个点，之后所有功能都在一个活着、有人用的系统上迭代。

### 目标

| 维度 | 决定 |
|---|---|
| 形态 | 公网网站，注册登录后提问 |
| 知识源 | **语雀公开知识库（爬公开页，无需 token）** + Markdown；PDF 次要 |
| 数据隔离 | **公共库 + 个人库**：语雀内容人人可搜；用户自传文档仅自己可搜 |
| 上传 | 自助上传 → 后台自动解析 → 立即可用 |
| 注册 | **邀请码制** |
| 能力 | RAG 带引用 + 会追问、会调工具 |
| 部署 | 阿里云 ECS `8.136.116.9` → `https://liushun666.cn/` |

### 已核实的事实

| 项 | 值 | 影响 |
|---|---|---|
| 域名 | `liushun666.cn` **已备案**，现跑 Aura Note 落地页 | agent 占根路径；**Aura Note 挪到 `/aura` 保留，不删** |
| 语雀 token | 拿不到（需超级会员） | 走公开页 `appData` 解析，路径已验证 |
| ECS 配置 | **2 核 / 1.6Gi 可用内存 / 40G 磁盘（已用 4.6G）**，Ubuntu，已占 591Mi | 内存是唯一紧约束；磁盘充裕。**不升配**，按 2GB 极简部署 |
| 本机 | Intel Arc 核显无 N 卡；Python 3.14.6（torch 无 wheel）；uv 0.11.4；Node 24.14.1 | uv pin 3.12；模型推理一律走 API |

---

## 一、1.6GB 内存下的三条硬约束

服务器实测 2 核 / 1.6Gi 可用 / 已占 591Mi，**剩约 1.0Gi**。

这套架构能在这个盒子里活，是因为**最吃内存的部分（embedding / rerank / LLM 推理）全在云端 API，服务器上一个模型都不跑**。剩下的常驻进程：

| 进程 | 预算 |
|---|---|
| Nginx | ~40 MB |
| PostgreSQL（调小 `shared_buffers` 到 128MB） | ~250 MB |
| FastAPI（uvicorn 单 worker） | ~250 MB |
| 解析 worker | ~150 MB |
| **合计** | **~700 MB** |

余量约 300MB，很薄。因此下面三条是**硬性的，不是建议**：

### 1. 前端静态导出，且必须本机构建

`next build` 峰值吃 1GB+，在服务器上跑必 OOM。流程固定为：**本机 `npm run build` → 传 `out/` 产物 → Nginx 直接服务静态文件**。

Next.js 15 + AI SDK 6 完整保留，`useChat` 照常工作——只是 `output: 'export'`，服务器上不跑 Node 进程。这同时省下 100–200MB。

> 副作用：不能用 Next.js 的服务端能力（Server Actions、Route Handlers）。本方案里 Next.js 本来就只做 UI、所有逻辑在 FastAPI，**所以零损失**。

### 2. 加 2GB swap

33G 空闲磁盘，这是免费保险，兜住突发峰值不至于进程被 OOM killer 干掉。

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 3. 服务器端不跑 Docling 的 PDF ML 管线

Docling 解析 PDF 要加载版面检测模型，1–2GB 内存起步，这台机上必 OOM。但 **md / txt / docx / pptx 走非-ML 路径，很轻**，不受影响。

PDF 的处理方式：
- **服务器端**：只做纯文本提取（`pypdf`，BSD 许可），扫描件不支持
- **需要高质量 PDF 解析时**：本机跑 Docling 完整管线 → 结果推服务器数据库

> 你原话就是「PDF 如果比较难就先不做」，所以这个限制不构成实际损失。

### 4. 内存回收（已勘察，约可回收 107–268MB）

`ps aux` 实测结果，591MB 的构成：

| 进程 | 占用 | 处置 |
|---|---|---|
| `python server.py`（容器 `erp-copilot-mcp:0.1.0`） | **71MB** | ✅ **删**——是已停掉的 erp-copilot 项目的 MCP server，绑 `127.0.0.1:8000`（正好是我们要用的端口） |
| `dockerd` + `containerd` | **90MB** | ✅ **删**——服务器上装了 Docker（本机没装，两回事）。容器删掉后 Docker 无用 |
| `AliYunDunMonitor` | 43MB | 保留。公网站点需要入侵告警 |
| `argusagent`（云监控） | 17MB | 保留。控制台监控图表 |
| `fwupd`（固件更新） | 41MB | ✅ **关**——云主机无物理固件 |
| `tuned`（性能调优） | 28MB | ✅ **关**——对本负载无意义 |
| `multipathd`（多路径存储） | 27MB | ✅ **关**——只有一块 `/dev/vda` |
| `udisksd`（可移动磁盘） | 11MB | ✅ **关**——服务器没人插 U 盘 |
| systemd / journald / resolved 等 | 余下 | 系统必需，不动 |

**确定可回收：107MB**（fwupd + tuned + multipathd + udisksd）
**待查明后可能再回收：161MB**（server.py + Docker）

> ### ✅ 已执行完毕（2026-08-15）
>
> | 指标 | 清理前 | 清理后 |
> |---|---|---|
> | 已用内存 | 591Mi | **327Mi** |
> | 可用内存 | 1.0Gi | **1.3Gi** |
> | `8000` 端口 | 被容器占用 | **空闲**（留给 FastAPI） |
> | 待更新 / 重启 | 17 个 / 需重启 | **0 / 不需要** |
> | 磁盘占用 | 11.6% | 9.7%（Docker 回收 561.9MB） |
>
> | Swap | 0 | **2.0Gi** ✅ |
>
> 备份留在 `/root/backup-2026-08-15/`（192MB，含 nginx 配置、erp-copilot、/var/www）。
> Nginx 保持运行，80/443 正常，`/var/www/myprogram` 按计划保留。
> Docker 的 systemd 单元已全部移除（不自启、不占内存）；`/usr/bin/docker*` 二进制残留仅占磁盘，无影响。
>
> **服务器准备完成，可直接进入 M0。**

**勘察结论（已完成）**：

- 容器是 `erp-copilot-mcp:0.1.0` —— 已停掉的 erp-copilot 项目的 MCP server
- **网站由 Nginx 服务**（占 80/443），容器只绑 `127.0.0.1:8000` → **删容器不影响网站**，无需占位页
- `/opt/erp-copilot` + `/opt/erp-copilot.git`（push 部署用的裸仓库）一并删
- Nginx 已装且运行中，M5 直接改配置即可

**执行顺序**（用户决定全删）：

| 段 | 动作 | 回收 |
|---|---|---|
| 1 | **备份** → `/root/backup-<date>/`：nginx 配置、`/opt/erp-copilot*`、`/var/www` | — |
| 2 | `systemctl list-units \| grep -iE 'erp\|copilot\|mcp'` 查关联服务 | — |
| 3 | 停删容器 → `docker system prune -af --volumes` → `apt purge` docker/containerd → `rm -rf /var/lib/docker` | **~161MB** |
| 4 | `rm -rf /opt/erp-copilot /opt/erp-copilot.git` | 磁盘 |
| 5 | `systemctl disable --now fwupd tuned multipathd udisks2` + `apt autoremove`；查 snapd | **~107MB**（+snapd 30–50MB） |
| 6 | `apt upgrade -y && reboot` | — |
| 7 | 验收：`free -h`；`ss -tlnp \| grep -E ':(80\|443\|8000)'` | — |

**预期：已用 591MB → 260–320MB，可用 1.0GB → ~1.3GB。** 塞下 Postgres + FastAPI + worker（~700MB）后从"很挤"变"够用"。附带 `8000` 端口腾出，正是 FastAPI 要用的。

> ✅ **保留** `/var/www/myprogram`（Aura Note）：占磁盘不占内存，现在删和 M5 删对内存零区别，留着域名不空窗。
> ✅ **保留** `AliYunDunMonitor`(43MB) + `argusagent`(17MB)：公网带注册的站点，入侵告警值这 60MB。

---

## 一·五、安装位置约定（用户规定）

**所有软件一律装到 D 盘。** C 盘只留项目源码。

| 项 | 位置 | 怎么设 |
|---|---|---|
| PostgreSQL 16 + pgvector | `D:\PostgreSQL\16`，数据目录 `D:\PostgreSQL\16\data` | 安装向导里改路径 |
| uv 托管的 Python 3.12 | `D:\dev\uv\python` | 环境变量 `UV_PYTHON_INSTALL_DIR` |
| uv 缓存（依赖包） | `D:\dev\uv\cache` | 环境变量 `UV_CACHE_DIR` |
| npm 缓存 | `D:\dev\npm-cache` | `npm config set cache` |
| 项目源码 | `C:\Users\liushun\Desktop\Copilot` | 当前工作目录，不属"安装" |

> ⚠️ **环境变量必须在装任何东西之前设好**。uv 默认把 Python 和缓存塞在 `%LOCALAPPDATA%`（C 盘），装完再搬要重来一遍。这是 M0 的第一个动作。

---

## 二、技术选型（2026 主流栈）

### 2.1 全景

| 层 | 选型 | 为什么是它 |
|---|---|---|
| Agent 框架 | **Pydantic AI 2.0** | 2026 新项目的默认选择。类型安全、工具签名即 Python 类型、自带 OpenTelemetry。比 LangChain 轻，比手写省事 |
| Web 框架 | **FastAPI** | Python AI 服务事实标准，原生 async + SSE |
| 数据库 | **PostgreSQL 16 + pgvector** | **千万级以下向量的最优解**：向量、文档、用户、任务队列全在一个库，SQL join 直接用，零额外基础设施 |
| 文档解析 | **Docling**（MIT，IBM → Linux Foundation），**非-ML 模式** | 一个库通吃 DOCX/PPTX/XLSX/HTML/MD，输出保留语义层级的结构化文档，替代 pdfplumber+python-docx+python-pptx 的拼装。**PDF 的 ML 管线在 1.6GB 上关闭**（见第一节） |
| Embedding | `BAAI/bge-m3`（SiliconFlow，免费） | 中文最强开源 embedding。**DeepSeek / KIMI 都不提供 embeddings 接口，Gemini 提供但国内服务器访问不了 Google API —— SiliconFlow 是唯一可行解** |
| Rerank | `BAAI/bge-reranker-v2-m3`（SiliconFlow，免费） | 精排，对准确率提升最明显 |
| LLM（生成） | **DeepSeek**（已有 Key），KIMI 备用 | OpenAI 兼容，一套代码切换 |
| LLM（评测裁判） | Gemini（已有 Key，**仅本机用**） | M8 做 LLM-as-Judge，换个模型打分更客观 |
| 前端 | **Next.js 15（静态导出）+ Vercel AI SDK 6** | 2026 AI 聊天界面事实标准。`useChat` 直接搬走流式/工具调用/中断/重试。`output: 'export'` 本机构建，服务器不跑 Node |
| UI | Tailwind + shadcn/ui | 2026 主流组件方案 |
| 任务队列 | **Postgres `FOR UPDATE SKIP LOCKED`** | 已经有 Postgres，不再引入 Redis/Celery。这是标准做法，不是妥协 |

### 2.2 前后端怎么接（关键细节）

**Next.js 只做 UI，所有 AI 逻辑在 Python。** 不用 Vercel 官方 chatbot 模板的全 TS 方案——那会把认证和持久化推到 TS 侧，业务逻辑劈成两半，是维护灾难。

衔接靠 **AI SDK Data Stream Protocol**（SSE 格式）：

```
Next.js  useChat({ transport: new DefaultChatTransport({ api: '/api/chat' }) })
   ↓  nginx 反代
FastAPI  POST /api/chat  →  返回 AI SDK Data Stream Protocol 的 SSE
```

这是 **Vercel 官方支持的路径**（官方模板 "AI SDK Python Streaming"），协议有公开规范；也有 `fastapi-ai-sdk` 包直接实现。不是野路子。

### 2.3 中文混合检索

pgvector 管 dense 向量；中文 BM25 需要分词扩展：

- 首选 `pg_jieba` 或 `zhparser`（需编译安装）
- **装不上就降级**：dense-only + bge-reranker 精排。bge-m3 的中文语义召回本来就强，加上 reranker，纯向量方案的效果损失有限

> **不要为了混合检索卡住主线。** M2 先跑 dense + rerank，BM25 作为 M7 的优化项——用评测数字证明它值得加，而不是凭感觉先上。

### 2.4 ⚠️ 许可红线

**不用 PyMuPDF**——AGPL-3.0，商用需向 Artifex 买授权或整个项目开源。这和你当初「弃 MaxKB（GPL）选 Onyx（MIT）以保留商业化可能」**是同一个陷阱**，且这次是**公网对外提供服务**，AGPL 触发条件比内部自用严格得多。

Docling 是 MIT，一个库覆盖所有格式，顺带解决了这个问题。引入任何新依赖前先看许可。

### 2.5 语雀抓取（无 token）

**实测后改用语雀内部 JSON 接口，比原计划「全靠正则解析 HTML」稳得多：**

```
1. GET /{login}                          → 正则解析 appData 拿 group.id   ⚠️唯一依赖 HTML 的一步
2. GET /api/groups/{group_id}/books      → 知识库列表（只认数字 id，传 login 返回 422）
3. GET /api/catalog_nodes?book_id={id}   → 目录树（文档标识字段叫 url，不是 slug）
4. GET /api/docs/{slug}?book_id={id}&merge_dynamic_data=false
                                         → 正文（content 字段，Lake HTML）+ content_updated_at
```

**语雀改版的脆弱面从「全链路」缩小到「一个数字」**——group_id 拿到后可缓存，
其余三步都是结构化 JSON。

**增量判定改用 `content_updated_at`**（语雀自己给的时间戳），比正文 hash 直接。

正文是语雀 Lake HTML，用 `markdownify` 转 Markdown——标题层级得以保留，
切分时的 `heading` 溯源信息全靠它。

**实测目标空间**：`wdterpqjb` = 「旗舰版ERP」，group_id `22819707`，
**14 个公开知识库 / 807 篇**（群组声称 19 个，匿名只能见 14 个）。

工程要点（这类爬虫成败全在细节）：

- **限速 1–2 req/s** + 抖动；正常 User-Agent，别用默认的 `python-requests`
- 失败**指数退避重试** 3 次；单篇失败记日志继续，不中断整库
- **增量靠正文 content hash**（公开页无可靠 `updated_at`）——hash 没变就跳过 embedding，这是省钱关键
- **解析器隔离在单文件** + 固化 HTML 样本回归测试。语雀改版时只有这个文件会碎，测试立刻告诉你碎在哪

**目标知识库**：`https://www.yuque.com/wdterpqjb` —— 「旗舰版ERP」，已确认可公开访问、无需登录。M2 开工时先跑一遍 `fetch_toc` 摸清文档规模，再决定要不要分批同步。

### 2.6 数据隔离（本项目最不能出的 bug）

```sql
owner_id IS NULL          -- 公共库（语雀），所有登录用户可见
owner_id = '<user_id>'    -- 私有库（用户上传），仅本人
```

检索时统一：`WHERE owner_id IS NULL OR owner_id = :current_user`

**红线：这个条件只允许在 `retrieve.py` 一处出现**，绝不允许调用方自己拼 SQL。配测试守住——用户 A 检索绝不能命中用户 B 的文档。

---

## 三、目标结构

```
Copilot/
├── backend/
│   ├── pyproject.toml          # uv，requires-python = ">=3.12,<3.13"
│   ├── .env.example
│   ├── alembic/                # 数据库迁移
│   └── src/copilot/
│       ├── config.py           # pydantic-settings
│       ├── cli.py              # ingest / ask / sync-yuque / invite / serve / worker
│       ├── db/
│       │   ├── models.py       # SQLAlchemy 七张表：User/InviteCode/Document/Chunk/Job/Conversation/Message
│       │   └── session.py      # async engine
│       ├── providers/
│       │   ├── base.py         # Embedder / Reranker 的 Protocol
│       │   ├── siliconflow.py  # bge-m3 + bge-reranker-v2-m3
│       │   └── llm.py          # OpenAI 兼容流式（DeepSeek / 通义 / KIMI）
│       ├── sources/
│       │   ├── yuque.py        # ⭐ 语雀 JSON 接口链路 + 限速 + 重试
│       │   └── sync.py         # 落成带 frontmatter 的 Markdown + 增量台账
│       ├── ingest/
│       │   ├── parsers.py      # 上传文件 → Markdown（md/txt/docx/pptx/pdf，轻量库）
│       │   ├── chunker.py      # 中文切分，带溯源元数据
│       │   └── pipeline.py     # 切分 → 嵌入 → 写 pgvector；⭐ write_chunks 是写 owner_id 的唯一处
│       ├── retrieve.py         # ⭐ 隔离过滤唯一收敛点；检索 → rerank → 带编号引用
│       ├── qa.py               # ⭐ 防幻觉双闸门 + is_no_answer()
│       ├── agent/              # （M7）
│       │   ├── agent.py        # Pydantic AI Agent 定义
│       │   └── tools.py        # search_kb / collect_requirement / gen_checklist / export_excel
│       ├── auth/
│       │   ├── security.py     # bcrypt（SHA-256 预摘要）+ JWT + 邀请码生成
│       │   ├── invites.py      # 发码 + 原子核销
│       │   └── deps.py         # FastAPI 依赖：CurrentUser / SessionDep / cookie 读写
│       ├── jobs/
│       │   ├── queue.py        # ⭐ SKIP LOCKED 取放 + 状态机 + 僵尸任务回收
│       │   └── worker.py       # 独立进程的消费循环（优雅停止）
│       └── api/
│           ├── app.py          # FastAPI 装配 + lifespan + CORS
│           ├── stream.py       # ⭐ AI SDK 协议编码器（字段名错一个前端就空白）
│           ├── schemas.py      # 请求 / 响应模型
│           ├── providers.py    # 进程内共享的 embedder / reranker / llm
│           └── routes/
│               ├── auth.py     # 注册（验邀请码）/ 登录 / 登出 / me
│               ├── chat.py     # ⭐ SSE 流 + 会话历史
│               └── docs.py     # 上传 / 列表 / 删除　（M6）
├── frontend/                   # Next.js 16，output: 'export'
│   ├── next.config.ts          # ⭐ output: 'export' + trailingSlash（本机构建，产出 out/）
│   ├── app/
│   │   ├── page.tsx            # 分流：登录了去 /chat，没登录去 /login
│   │   ├── login/ · register/  # 注册页填邀请码
│   │   ├── chat/page.tsx       # useChat + 侧栏历史
│   │   └── documents/page.tsx  # 上传（拖拽）+ 解析状态轮询 + 删除
│   ├── components/
│   │   ├── chat/               # chat-view / message-list / composer / citations / sidebar
│   │   └── ui/                 # shadcn/ui
│   └── lib/
│       ├── api.ts              # ⭐ 所有请求 credentials:"include"；API_BASE 是构建期常量
│       ├── auth-guard.ts       # 客户端路由守卫（静态导出无 middleware）
│       └── chat-types.ts       # data-citations / data-conversation 的类型
├── deploy/
│   ├── nginx.conf              # / → out/ 静态；/api → FastAPI；/aura → Aura Note 保留
│   ├── copilot-api.service     # systemd: uvicorn（MemoryMax=600M）
│   ├── copilot-worker.service  # systemd: 解析 worker（MemoryMax=400M，优雅停止）
│   ├── setup-server.sh         # swap + postgres + pgvector + certbot 一次性初始化
│   └── deploy.sh               # 本机构建 out/ → rsync 上传 → 重启服务
├── eval/
│   ├── dataset.yaml
│   └── run.py
└── tests/
    ├── test_yuque_parse.py     # 固化样本，防语雀改版静默失效
    ├── test_chunker.py         # 切分策略
    ├── test_isolation.py       # ⭐ 跨用户隔离
    ├── test_auth.py            # 密码哈希 / JWT / 邀请码
    ├── test_stream_protocol.py # ⭐ AI SDK 协议字段固化
    ├── test_api_auth.py        # 注册 / 登录 / 登出 端到端
    ├── test_api_chat.py        # ⭐ SSE 序列 + 「不知道就不挂来源」
    ├── samples.py              # 造真 docx / pptx / pdf 样例（含扫描件 PDF）
    ├── test_parsers.py         # 编码兜底 / 表格不跑位 / 扫描件要报错
    ├── test_jobs.py            # ⭐ SKIP LOCKED + 重试语义 + worker 端到端
    └── test_api_documents.py   # ⭐ 上传安全项逐条 + 跨用户删不掉
```

---

## 四、进度总览

### 4.1 全局排期

单位是**净工作日**（约 6–8 小时专注开发）。你有本职工作，若按每天 2–3 小时投入，日历时间约为 **2.5–3 倍**。

| 阶段 | 里程碑 | 工期 | 累计 | 依赖 | 状态 |
|---|---|---|---|---|---|
| **地基期** | M0 地基 | 1–2 d | 2 d | — | ✅ |
| | M1 检索内核 | 2–3 d | 5 d | M0 | ✅ |
| | M2 语雀入库 | 2–3 d | 8 d | M1 | ✅ |
| **上线期** | M3 认证 + 聊天 API | 2 d | 10 d | M1 | ✅ |
| | M4 Next.js 前端 | 2–3 d | 13 d | M3 | ✅ |
| | **M5 上线** ⭐ | 1–2 d | 15 d | M2, M4 | ✅ |
| **增强期** | M6 上传 + 私有库 | 2–3 d | 18 d | M5 | ✅ |
| | M7 Agent 化 | 2–3 d | 21 d | M5 | ⬜ |
| | M8 评测 + 优化 | 2–3 d | 24 d | M7 | ⬜ |

**合计 16–24 净工作日。** 按业余投入折算，约 6–10 周日历时间。

**进度：M0–M6 全部完成（含计划外的 M4.5 配图），均已上线。剩余 M7 Agent / M8 评测。**
站点在 https://liushun666.cn 运行中，已有 3 个真实注册账号；公共库 746 篇 / 5268 块，
每个人还能传自己的文档进私有库。

> M6 验收时撞见一件事：**材料里有答案、却被答成「知识库暂无此内容」**（见 M6 那节
> 末尾）。M8 的评测集是唯一能量化这件事的手段。按 4.2 的依赖图 M7 在 M8 之前，
> 但这两者其实互不依赖——先做哪个由你定。

### 4.2 关键路径

```
M0 ──→ M1 ──→ M2 ──┐
        │           ├──→ M5 上线 ──→ M6 上传
        └──→ M3 ──→ M4 ┘                  └──→ M7 Agent ──→ M8 评测
```

- **M1 是所有事的前置**，检索链路不通，后面全是空中楼阁
- **M2 和 M3/M4 可并行**——语雀爬虫卡住时不要干等，去写认证和前端
- **M5 之后才允许做 M6/M7**。上线是分水岭：之前是自己闭门造车，之后每个功能都有真实反馈

### 4.3 三个阶段的心态

| 阶段 | 目标 | 失败信号 |
|---|---|---|
| 地基期（M0–M2） | 证明「检索得准、答得有据」 | 超过 8 天还答不准 → 停下来查 chunk 策略，别往下堆功能 |
| 上线期（M3–M5） | 让别人能用上 | 在 UI 细节上打磨超过半天 → 立刻停手，上线优先 |
| 增强期（M6–M8） | 用真实反馈驱动 | 开始做没人要的功能 → 回去看用户实际问了什么 |

> **前两次项目死在地基期和上线期之间。** 这次 M5 被刻意压到第 15 天，就是为了穿过那道坎。

---

## 五、详细任务台账

> 每个里程碑都有**可当场验证的验收命令**。不过验收不往下走。
> 勾选框用于实际执行时逐项打勾。

### M0 — 地基（1–2 天）　✅ **已完成 2026-08-15**

**服务器侧**
- [x] 加 2GB swap 并写入 `/etc/fstab`
- [x] 清理内存：删 Docker + erp-copilot 容器 + 关无用系统服务 → **591MB → 327MB**
- [x] 系统更新 + 重启，Aura Note 站点正常，8000 端口腾出

**本地工程**
- [x] ⭐ D 盘环境变量：`UV_PYTHON_INSTALL_DIR=D:\dev\uv\python`（`UV_CACHE_DIR` 原本已在 D）、`npm config set cache D:\dev\npm-cache`
- [x] Python 3.12.13 装到 `D:\dev\uv\python`
- [x] `uv init --lib`，pin Python 3.12 → `backend/pyproject.toml`
- [x] **`.gitignore`**：`data/`、`.env`、`out/`、`__pycache__`（已用 `git check-ignore` 验证 `.env` 确实被忽略）
- [x] 装 **PostgreSQL 17** + **pgvector 0.8.6** 到 `D:\PostgreSQL\17`
- [x] `config.py`：pydantic-settings 读 `.env`；`.env.example` 同步
- [x] `db/models.py`：User / InviteCode / Document / Chunk / Job / **Conversation + Message** 七张表
- [x] `db/session.py`：async engine（`pool_size=5`，适配服务器 `max_connections=20`）
- [x] alembic 初始化 + 首次迁移（迁移里带 `CREATE EXTENSION vector`）
- [x] `cli.py`：`ingest` / `ask` / `sync-yuque` / `invite` / `serve` / `worker` 六个子命令空壳
- [x] pytest 冒烟测试 6 条 + ruff 配置

**验收结果**
```
[1] kb --help              六个子命令全部列出            ✅
[2] alembic upgrade head   be8621870f54 (head)，七张表   ✅
[3] pytest                 6 passed                      ✅
[4] ruff check             All checks passed             ✅
```

**与原计划的三处偏差（都有理由）**

| 原计划 | 实际 | 为什么 |
|---|---|---|
| PostgreSQL 16 | **17** | winget/EDB 只有 17/18；Ubuntu PGDG 也有官方 `postgresql-17-pgvector`，两端都省事。选 16 本来就无硬理由 |
| 六张表 | **七张** | `Conversation` 拆成 `conversations` + `messages`，消息才好按条存引用 |
| pgvector 官方包 | **社区预编译** | 官方不发 Windows 二进制，自编译需先装 3–6GB VS Build Tools。**仅开发机用，服务器走官方 apt 包** |

**踩到的坑（记下来省得再踩）**
- 每个 async 测试跑在各自的事件循环里，共用连接池会拿到上个循环的死连接 → asyncpg 崩。测试一律用 `NullPool` 独立引擎，见 `tests/conftest.py`
- alembic 生成的迁移需要 `import pgvector.sqlalchemy`，已加进 `script.py.mako`，后续迁移自动带上

### M1 — 检索内核　✅ **已完成 2026-08-15**

> 顺序调整：语雀已在 M2 抓成本地文件，M1 直接用真实语料，跳过手写 md 这一步。

- [x] `providers/base.py`：`Embedder` / `Reranker` 两个 Protocol
- [x] `providers/siliconflow.py`：bge-m3 + bge-reranker-v2-m3，批量 + 限速 + 429 退避重试
- [x] `providers/llm.py`：OpenAI 兼容流式（DeepSeek / 通义 / KIMI 通吃）
- [x] `ingest/chunker.py`：按标题分段 + 短段合并，每块带 `title`/`heading`/`source_url`
- [x] `ingest/pipeline.py`：切分 → 批量嵌入 → 写 pgvector
- [x] `retrieve.py`：向量 top-20 → rerank top-5 → 带 `[1][2]` 编号的 context
- [x] ⭐ **owner 过滤在 `_visibility_filter()` 单点收敛**
- [x] `qa.py`：防幻觉双闸门（检索层兜底 + prompt 铁律）
- [x] `tests/test_isolation.py` 8 条 + `test_chunker.py` 19 条
- [ ] pgvector 索引：数据量到万级再建 HNSW，现在顺序扫够用

**用真实语料标定出来的三个参数**（不是拍脑袋，见下）

| 参数 | 原值 | 实测后 | 依据 |
|---|---|---|---|
| `min_chars` | 30 | **5** | 30 会丢掉 12.7% 的段，而那些「（操作路径：【app】-【店铺销售统计】）」正是用户最想要的答案 |
| 段落合并 | 无 | **相邻短段合并，不跨一级标题** | 语料段落长度中位数仅 101 字，一段一块会切出海量碎片 |
| `rerank_score_threshold` | 0.3 | **0.005** | bge-reranker 分数绝对值极低：正确答案 0.02、无关 0.0001。0.3 会让系统永远回答"不知道" |

**验收结果**
```
全量入库        746 篇 → 5098 块，失败 0，隔离违规 0            ✅
检索抽查        6 个业务域：5 题完整答出 + 1 题正确声明材料不足   ✅
防幻觉对照      3/3 全部「知识库暂无此内容」且零来源              ✅
pytest          53 passed                                       ✅
ruff            All checks passed                               ✅
```

各库入库量：设置 1622 块 · 统计 832 · 常见问题库 782 · 仓储 656 · 销售 393 ·
货品 196 · 分销 155 · 平台事项 115 · 采购 102 · 账款 99 · APP 65 · 其它 43 ·
事务 22 · CRM 16

**786 → 746 的差额已查清，不是数据丢失**：39 篇是语雀自己 `word_count=0`
的目录封面页（「订单处理」「货品管理」这类）。

**那道没答上的题反而证明系统是诚实的**：问「拼多多电子面单模板怎么设置」，
模型答「参考材料中没有直接说明」。因为该文档正文只有两行「XX请点击：」，
真正的步骤在语雀内链里，而内链是 Lake 卡片、解析时被剥掉了。
模型没有硬编步骤，这正是 ERP 场景最需要的品质。
（对比：《京东面单模板设置步骤》7 块、《唯品会MP》14 块，有正文的就能答。）

**踩到的坑**

1. **`rerank_score_threshold=0.3` 是个隐形炸弹。** 实测正确答案分数只有 0.02，
   这个阈值会让系统永远回答"不知道"——而这个故障看起来还很像"防幻觉工作正常"。
2. **「暂无此内容」下面挂着来源。** `ask_stream` 为了让前端提前渲染，
   在生成前就返回引用；模型说不知道时若照常展示，用户会以为答案有依据。
   已在 `qa.is_no_answer()` 收口，并写进接口文档，免得 M4 做前端时重犯。
3. **合并小节时子标题会丢。** 「1、营销步骤」「2、结果统计」合并后成了两段
   没头没尾的文字。改为把各自的子标题以【】写进正文。
4. **单独指定测试文件路径会让 pytest 读不到 pyproject 配置**（rootdir 判定变化），
   async 夹具直接报错。一律用 `uv run pytest` 从 `backend/` 跑。
5. **正文全在标题里的文档会整篇消失。** 13 篇电子面单对接文档
   （拼多多/抖音/快手/美团/京东/唯品会/顺丰/得物）通篇只有 `####` 标题、
   没有正文段落，按段落切得到 0 块——**文档凭空不见且不报错**。
   已加兜底：切不出块时用标题文本成块。
6. **硬切超长文本会留下零头。** 有文档正文含打印模板 JSON，硬切后剩
   `ext1"}` 这种 6 字残片单独成块。已改为并进上一块。
7. **自动化检查会骗人。** 抽查脚本按「引用命中期望知识库」判定通过，
   给一道其实没答上的题打了 ✓。指标要看，但答案本身也得读。

### M2 — 语雀抓取　✅ **抓取部分已完成 2026-08-15**

> 调整了顺序：**先抓到本地文件，再由 M1 入库**。抓取和检索分开验证，
> 出问题时能分清是解析的锅还是切分的锅。

- [x] `sources/yuque.py` — 四步 JSON 接口链路（见 2.5），替代原计划的全链路正则
- [x] 限速 1.5 req/s + 随机抖动；正常 User-Agent
- [x] 指数退避重试 3 次；单篇失败不中断整库
- [x] 增量：`content_updated_at` 时间戳 + `_manifest.json` 台账
- [x] `sources/sync.py` — 落成带 YAML frontmatter 的 Markdown
- [x] `tests/test_yuque_parse.py` 14 条回归 + `scripts/refresh_yuque_fixtures.py`
- [ ] 接入 `pipeline.py`，以 `owner_id=NULL` 写入公共库　→ **归到 M1 一起做**

**实际战果**

```
14 个公开知识库 · 807 篇
  成功 784    私密 19（作者设了权限，401/404，重试无用）
  跳过  2     真失败 0
产物：data/raw/yuque/<book_slug>/<doc>.md  +  _manifest.json
```

**两个发现**

1. **19 篇 404 不是 bug**——网页版返回 401，内容接口伪装成 404 并附 `docTitle`。
   已单独归类为 `YuqueRestricted`，不再混进错误里。否则每次同步跳 19 个红字，
   真出问题时反而看不见。
2. **增量省的是 embedding，不是网络**——目录接口不带时间戳，所以即便一篇没变，
   仍要发 ~800 次请求（约 9 分钟）确认。做定时同步够用；真要优化，
   可以先比对 `books` 接口的 book 级 `updated_at` 跳过整个库。

**验收结果**
```
copilot sync-yuque https://www.yuque.com/wdterpqjb
  → 14 个库 / 新增 784 / 私密 19 / 失败 0                       ✅

再跑一次（增量验证）
  → 新增 0　跳过 786　私密 19　失败 0，14 个库全部 ✓            ✅

pytest   24 passed（含 18 条语雀解析回归）                       ✅
ruff     All checks passed                                       ✅
```

产物：`data/raw/yuque/` 786 篇 Markdown，3.37 MB，平均 4.4 KB/篇。
按 500 字切分预计产生 **7000–9000 个 chunk**。

> 带引用问答的两条验收（含防幻觉那条）挪到 M1，因为要先有检索链路。

### M3 — 认证 + 聊天 API　✅ **已完成 2026-08-16**

- [x] `auth/security.py`：bcrypt 密码哈希 + JWT 签发/校验
- [x] JWT 存 **HttpOnly + Secure + SameSite=Lax** cookie（不放 localStorage）
- [x] `auth/deps.py`：`current_user` 依赖，未登录抛 401
- [x] `cli.py invite --count 5`：生成邀请码（另加 `--list` 查未用完的）
- [x] `routes/auth.py`：注册（**校验邀请码 + 一次性作废**）/ 登录 / 登出 / me
- [x] `routes/chat.py`：输出 **AI SDK UI Message Stream Protocol**（SSE 格式）
- [x] 对话历史落 `conversations` + `messages`，另给 M4 备了两个读接口
- [x] CORS 配置（本地开发 `localhost:3000` → `localhost:8000`，带凭证）
- [x] `api/stream.py` 协议编码器单独隔离 + 15 条格式固化测试

**接口清单**（M4 照这个对接）

```
POST /api/auth/register   {email, password, inviteCode}  → 201 + Set-Cookie
POST /api/auth/login      {email, password}              → 200 + Set-Cookie
POST /api/auth/logout                                    → 204，清 cookie
GET  /api/auth/me                                        → 当前用户 / 401
POST /api/chat            useChat 的 body                → SSE 流
GET  /api/conversations                                  → 会话列表
GET  /api/conversations/{id}/messages                    → 单条会话的消息
GET  /api/health                                         → 给 systemd / nginx 探活
```

**SSE 片段顺序**（`data-citations` 的位置是刻意的，见下面坑 #1）

```
start → start-step → data-conversation → text-start
      → text-delta × N → text-end → [data-citations] → finish-step → finish → [DONE]
```

**验收结果**（本机真起服务、真密钥、真 curl）
```
[1] copilot invite -n 3        3 个码：4UP6-U49C / RH3E-CXYF / D3Z7-QX8J   ✅
[2] 无 cookie POST /api/chat   401 未登录或登录已过期                       ✅
[3] 错邀请码注册               400 邀请码无效或已被使用，且用户没被建出来    ✅
[4] 真邀请码注册               201 + Set-Cookie: HttpOnly; SameSite=lax     ✅
[5] 登录 → 带 cookie 提问      187 个 SSE 片段，5 条引用带语雀真实链接       ✅
[6] 问知识库没有的             「知识库暂无此内容。」+ data-citations 0 帧   ✅
[7] GET /api/conversations     2 条会话，标题取自首个问题                    ✅
[8] CORS 预检                  localhost:3000 放行；evil.example.com 400    ✅
[9] pytest                     113 passed（新增 60 条）                     ✅
[10] ruff check                All checks passed                            ✅
```

**与原计划的两处偏差**

| 原计划 | 实际 | 为什么 |
|---|---|---|
| 只落 `conversations` | 另加 `GET /api/conversations` 和 `/{id}/messages` | 存了读不出来等于没存；M4 刷新页面要靠它恢复对话 |
| — | `retrieve.py` 的 embed / rerank 调用改走线程池 | 它们是同步 httpx + `time.sleep` 限速。在协程里直接调会**卡住整个事件循环**，服务器是单 worker，别人正在进行的 SSE 流会一起停住 |

**踩到的坑**

1. **「引用先发」和「防幻觉」是直接冲突的。** M1 的 `ask_stream` 为了让前端早点
   渲染来源，在生成之前就把引用返回了；可模型完全可能接着回一句「知识库暂无此内容」。
   那时页面上就是一句"不知道"底下挂着五条来源——用户会以为答案有依据，
   **比不做防幻觉更糟**。已把 `data-citations` 挪到正文流完之后，
   先过 `is_no_answer()` 再决定发不发。这条从"靠自觉"变成了"结构上不可能"，
   并配了 `test_no_answer_carries_no_citations` 守着。
2. **`session.add()` 之后对象的主键还是 None。** `Conversation.id` 的
   `default=uuid.uuid4` 是**列默认值**，INSERT 时才求值。不 flush 就拿 `conv.id`
   去建 Message，插进去的 `conversation_id` 是 NULL。写测试之前跑手工 curl
   根本发现不了——因为异常被流里的 `except` 兜住变成了一个 error 片段。
3. **StreamingResponse 的响应体在依赖退出之后才被消费。** 流里再用
   `Depends(get_session)` 那个会话，可能已经关了。聊天流里自己
   `async with SessionLocal()`，不蹭请求作用域的依赖。
4. **bcrypt 只认前 72 字节，超了直接抛 ValueError。** 汉字 UTF-8 占 3 字节，
   25 个汉字就炸。已改成先 SHA-256 摘要 + base64 压成定长 44 字节再交给 bcrypt
   （Django 的 `bcrypt_sha256` 就是这个做法）。顺带堵住"前 72 字节相同就算同一个密码"。
5. **登录失败的两种情形必须给同一句话、花同样的时间。** 区分「邮箱不存在」和
   「密码错误」等于送人一个用户名枚举接口；而"不存在就立刻返回、密码错要跑一次
   bcrypt"这个耗时差异本身也是。已用一个假哈希把两条分支的耗时拉平。
6. **Git Bash 命令行里的中文传不进 curl 的 `-d`。** Windows 本地代码页会把它
   转成非法 UTF-8，FastAPI 回 400「There was an error parsing the body」，
   看着特别像鉴权顺序写反了。中文 payload 一律写文件 + `--data-binary @file`。
7. **`Depends()` 写在参数默认值里会被 ruff 的 B008 拦下。** 改用
   `Annotated[User, Depends(...)]`，这也是 FastAPI 现在推荐的写法，
   顺手收成 `CurrentUser` / `SessionDep` 两个别名，路由签名干净多了。

### M4 — Next.js 前端　✅ **已完成 2026-08-17**

- [x] `create-next-app` + Tailwind 4 + shadcn/ui
- [x] ⭐ **`next.config.ts` 设 `output: 'export'`** + `trailingSlash: true`
- [x] 登录页 / 注册页（填邀请码）
- [x] 聊天页：`useChat` + `DefaultChatTransport({ api, credentials: 'include' })`
- [x] 引用气泡：可点开，跳语雀原文
- [x] 侧栏历史：会话列表 + 新对话 + 点开还原（用上 M3 那两个读接口）
- [x] `lib/auth-guard.ts` 客户端路由守卫——**真正的鉴权在 FastAPI，前端守卫只是体验优化，不是安全边界**
- [x] 移动端适配：侧栏收成抽屉，`100dvh` 兜住地址栏收放
- [x] ⭐ **UI/UX 深度升级（全面模仿 ChatGPT 风格与 100% 全局纯中文）**：
  - 侧边栏：支持桌面端折叠/展开（一键收起释放阅读空间），历史会话按「今天、昨天、前 7 天、更早」智能分组，增加历史快速搜索；
  - 顶部导航栏：ChatGPT 风格极简磨砂玻璃顶栏，集成侧栏展开开关、助手身份徽章（`语雀检索增强`）与快捷新建对话；
  - 空状态欢迎屏：ChatGPT 经典居中 Sparkle 徽标与问候语，配备 2×2 业务场景卡片（退货入库、面单打印、短信策略、对账结算）与快捷提问标签胶囊；
  - 消息与富文本：集成 `react-markdown` + `remark-gfm`，完美渲染 Markdown 标题、粗体、列表、引用与表格；代码块配备语言标签与「一键复制代码」；
  - 引用来源：升级为 ChatGPT Search / 权威知识库风格卡片网格，清晰展示编号徽章、标题、小节路径与语雀原文外部链接；
  - 底部输入框：ChatGPT 标志性 `rounded-3xl` 大圆角悬浮卡片、微光聚焦、流式生成停止按钮动效，支持长会话「回到底部」悬浮气泡；
  - 纯中文本地化：全站所有提示、Aria 标签、表单占位符与异常提示均为自然规范的简体中文，严密防范 IME 中文输入法误触。

**版本与计划不同**（计划写于更早，这是当时的版本号）

| 计划 | 实际 | 说明 |
|---|---|---|
| Next.js 15 | **16.3.1** | 当前稳定版；`output: 'export'` 照常支持 |
| AI SDK 6 | **`ai` 7.0.66 + `@ai-sdk/react` 4.0.69** | M3 的后端就是照**现行**协议文档写的，两边对得上 |
| — | Tailwind 4 + React 19.2 | create-next-app 默认 |

**验收结果**（真浏览器跑，不是看代码觉得能跑）
```
[1] npm run lint / tsc --noEmit    干净                                   ✅
[2] npm run build                  out/ 7 个 html，1.3MB                  ✅
[3] 邀请码注册 → 自动进聊天页       ✅
[4] 提问「京东电子面单模板怎么设置」 逐字流式，5 条引用带真实语雀链接      ✅
[5] 问知识库没有的                  「知识库暂无此内容。」+ 0 个链接、无"来源"✅
[6] 侧栏点历史                      消息和 5 条引用从数据库还原            ✅
[7] 登出                            跳回 /login/                          ✅
[8] 未登录直开 /chat/               自动踢回 /login/                       ✅
[9] 390×844 移动端                  侧栏移出屏幕、汉堡可用、无横向滚动     ✅
[10] 静态站实测                     out/ + /api 反代，登录/流式/引用全通   ✅
[11] out/ 里 localhost:8000 出现 0 次，接口全是同源相对路径               ✅
```

> [10] 用一个模拟 nginx 的小脚本（发 `out/` + 反代 `/api`）跑的，
> 不是 dev server。M5 的 nginx 配置照这个形状写就行。

**踩到的坑**

1. **静态导出会在构建期预渲染页面，所以 `crypto.randomUUID()` 不能放
   `useState` 初始值**——那个 UUID 会被写死进 HTML，浏览器再算一个就是
   hydration mismatch。但放 `useEffect` 里也不行，React 19 的
   `set-state-in-effect` lint 直接报错。最后放在**渲染期派生**
   （`if (authed && id === null) setId(...)`）：预渲染时 auth 还是 loading，
   这段根本不执行，两头都躲开了。
2. **中文输入法选词时的 Enter 会把半截拼音提交上去。** 必须判
   `e.nativeEvent.isComposing`——中文界面的聊天框不做这个就是残的。
3. **`next/font/google` 给构建加了一次外网字体下载。** 断网就 build 失败，
   而 Geist 根本不含中文字形。已改成系统中文字体栈，零请求。
4. **`grep -rl xxx | head` 的退出码取自 `head` 不是 `grep`。** 我用它判
   "产物里有没有泄漏 dev 地址"，得到一个假阳性，差点去修一个不存在的问题。
   管道里做真假判断要单独取 grep 的退出码或数行数。
5. **`browse` 的 daemon 在这台机器上起不来**（Stagehand 相关的 `fill`/`click`
   一并失效）。`browse --ws <port>` 可以绕开 daemon 直连 CDP，配合
   `eval` + 原生 setter 驱动 React 受控输入照样能跑完整流程。
   页面跳转会销毁 JS 上下文，那时 eval 报 `Uncaught` 是正常的，动作其实已生效。

**留在开发库里**：`m3demo@test.local` / `a-good-password`、
`browser-m4@test.local` / `browser-pass-2026`（都只在本机库）。

**验收命令**
```bash
# 本机双开
cd backend && uv run copilot serve          # :8000
cd frontend && npm run dev                  # :3000
npm run build                               # 必须产出 out/
```
> **`npm run build` 这步不过，M5 就上不了线**——服务器跑不了 build。

### M4.5 — 答案带操作截图　✅ **已完成 2026-08-17**

> 计划外插入的一项。ERP 文档「点哪个按钮」全靠截图说清楚，纯文字答案差口气。
> 用户决定：**上线时就带图**、**行内插图**（不是只堆在来源区）、**图片下载到本地**。

**起因：图片一直都在，是被我自己扔掉的。**
语雀的配图不是 `<img>` 而是 `<card name="image" value="data:...">`，
而 `lake_to_markdown()` 里一句 `soup.select("card, ...")` 把所有卡片都
`decompose()` 了——附件和脑图该删，图片是误伤。786 篇 Markdown 里只剩 3 篇有图。
抽样 12 篇实测**平均 3.9 张/篇**，外推全库约 3000 张。

**必须镜像到本地，不能外链**（实测）：

```
curl <语雀图片>                                 → HTTP 200
curl -H 'Referer: https://liushun666.cn/' <同一张> → HTTP 403
```

浏览器加载 `<img>` 默认带 Referer，直接外链就是**满屏裂图**。
`referrerpolicy="no-referrer"` 眼下能绕过，但那是把整站的图押在
「语雀不收紧策略」上，且收紧后是静默故障——图裂了没有任何报错。

- [x] `sources/yuque.py`：删卡片**之前**先把图片卡片换成 `<img>`
- [x] `sources/images.py`：内容寻址下载器（按 URL 的 sha256 命名、两级目录、
      限速 6 req/s、重试、10MB 上限、临时文件改名落盘）
- [x] `sources/sync.py`：同步时顺带镜像，正文里的地址改写成 `/images/...`
- [x] `ingest/chunker.py`：图片抽成 `[图:a3f9]` 标记，随块走
- [x] `db/models.py`：`chunks.images` + `messages.images`（迁移 `108c3b17f470`）
- [x] `retrieve.py`：`build_context()` 把块内标记重编成全局 `[图1][图2]`
- [x] `qa.py`：prompt 教模型在步骤末尾引用图号，且**只准用材料里出现过的编号**
- [x] `api/routes/chat.py`：`data-images` 片段（**在正文之前**发，见下）
- [x] 前端：`[图N]` 渲染成可点开的截图，编号对不上的静默丢掉
- [x] `tests/test_images.py` 25 条
- [x] 全量重新同步（786 篇 / 6048 张图 1.1G）+ 重新入库（5268 块，55% 带图）
- [x] 线上端到端验收

**⭐ 实测：模型到底会不会引用 `[图N]`**

这是整件事唯一无法靠代码保证的环节，实测了两轮：

| | 6 题中带图 | 图引用数 | **无效图号** |
|---|---|---|---|
| 初版 prompt | 3 / 6 | 14 | **0** |
| 调整后 | **6 / 6** | **34** | **0** |

初版把配图规则放在「写法要求」末尾，还加了一句「图不是必须的，不要为了凑数硬塞」——
这个 hedge 把模型压住了。改成**铁律第 5 条**、正面表述在前，覆盖率从 50% 提到 100%。

**两轮都是 0 个无效图号**，这是最危险的失败模式（配一张错的截图比没有图更糟），
没有发生。前端另有一层：编号对不上的静默丢掉，不会显示成裂图或错图。

防幻觉闸门回归验证：3 道知识库没有的题，全部「暂无此内容」+ 零来源 + 零图号。

> **一个差点被我判成 bug 的细节**：第一次测「京东电子面单模板怎么设置」，
> 模型一个图号都没写，看着像功能没生效。查下来是**模型对的**——
> 召回的材料讲的是得物/京邦达，确实没有京东的截图。
> 单题结论不可信，必须成批测。

**两个关键设计**

1. **标记自带 id，不靠"数第几张图"。**
   如果按顺序计数，只要有一段被 `min_chars` 滤掉，后面所有图就整体偏移一张——
   「第 3 步」配上「第 2 步」的截图，而且**不会有任何报错**。
   `[图:a3f9]` 里的 id 由图片地址哈希而来，每块自己就能算出带哪些图，与顺序无关。

2. **配图在正文之前发，引用在正文之后发。**
   看着矛盾，其实是同一条原则的两面：
   - 引用晚发，是因为模型可能答「知识库暂无此内容」，那时挂来源会让用户
     误以为答案有依据（M1 坑 #2）
   - 配图早发，是因为前端要边流边把 `[图1]` 换成真图；而它不构成"有依据"的
     暗示——模型说不知道时正文里根本不会出现 `[图N]`，什么都不会渲染

### M5 — 上线 ⭐ **第一个交付点**　✅ **已完成 2026-08-17**

> **SSH 不用另给**——`~/.ssh/erp_vps`（上个 erp-copilot 项目留下的密钥）
> 对 `root@8.136.116.9` 仍然有效，免密可登。

**已完成**

- [x] 备份 nginx 全量配置 + `/var/www` → `/root/backup-20260817-163500`（186M）
- [x] 80/443 早已放行（Aura Note 一直在跑），无需改安全组
- [x] PostgreSQL 16.14 + pgvector 0.6.0（Ubuntu 官方源就有，不必加 PGDG）
- [x] `shared_buffers=128MB` / `max_connections=20`，七张表迁移到位
- [x] 数据库密码**在服务器上生成**，写进 `/root/.kb-db-password`(600)，全程不过对话
- [x] uv + 依赖（92 个包）
- [x] `copilot-api.service`：非 root 的 `copilot` 账号、`MemoryMax=600M`、
      `ProtectSystem=strict`。实测常驻 **81MB**
- [x] nginx 切换：`/` → 前端，`/api` → 8000，`/images/` → 配图
- [x] **Aura Note 下线**（2026-08-17，用户决定只保留知识库助手）。
      留了两份：`/var/www/myprogram.retired-20260817` 和
      `/root/backup-20260817-163500/var-www/myprogram`（186M / 10 个文件，已核对一致）。
      恢复办法写在 `deploy/nginx.conf` 的注释里
- [x] HTTPS 沿用既有证书（有效期至 2026-10-15），HTTP 强制跳转
- [x] `deploy/`：`setup-server.sh` / `deploy.sh` / `nginx.conf` / `copilot-api.service`
- [x] 灌数据：服务器自己跑 `sync-yuque`，14/14 库 / 786 篇 / 6047 张图
- [x] `copilot ingest`：**746 篇 / 5268 块 / 2881 块带图 / 6193 处图片引用，
      与本机数字完全一致，0 失败**
- [x] 发邀请码 + 端到端验收
- [x] `copilot-api` / `postgresql` / `nginx` 三个服务均已 `enabled`（开机自启）

**最终验收**（公网，用第一次问过、当时因数据没灌完而答不上来的那道题）
```
问「京东电子面单模板怎么设置？」
  → 答出 4 步，5 条引用，行内配图 [图12][图13]，无效图号 0
  → 两张图 HTTPS 均 200
  → 并诚实指出「材料中未提供旺店通系统内的具体设置步骤」
```

**上线验收（公网实测）**
```
/                200   /login/     200   /register/  200   /chat/  200
/api/health      200
/api/chat 无 cookie   401     /api/docs   404 ← 线上已关闭
http://  → 301 https://
free -h 可用 1.1Gi ← 健康线 200MB 以上
```
> ⚠️ 验证公网可达**必须用 `curl --resolve`**：本机 VPN 做 fake-IP DNS 劫持，
> 直接 curl 域名会全部失败，看起来像上线没成功（见坑 #5）。

**一处改动**：图片不从本机上传。语雀配图约 6000 张 1GB，
服务器在阿里云机房、从语雀 CDN 拉比家用宽带上传快得多；
而图片路径是按 URL 哈希算的，两边跑出来完全一致。

**踩到的坑**

1. **⭐ `config.py` 靠数目录层数定位项目根，部署时必然错。**
   开发是 `Copilot/backend/src/copilot/config.py`（往上四层是根），
   部署时目录被拍平成 `/opt/copilot/src/copilot/config.py`，根就算成了 `/opt`。
   可怕的是**它不报错**：pydantic-settings 读不到 `.env` 就静默用字段默认值，
   应用拿着默认的 `kb:kb` 去连库，最后报的是「password authentication failed」——
   排查方向被带到密码和 pg_hba 上，真正的原因在三层目录之外。
   已改成「显式 `COPILOT_ROOT` → 向上找 `.env.example` → 才轮到数层数」。
2. **向上找路标要分两趟。** 第一版把「找 `<x>/backend/.env.example`」和
   「找 `<x>/.env.example`」写在同一个循环里，结果在 `backend/` 就命中了第二个条件，
   根被算成 `backend/`，`data/` 跟着指到 `backend/data`。开发布局必须先判。
3. **`pkill -f "uv sync"` 会把自己杀掉。** `-f` 匹配整条命令行，
   而我那条 ssh 命令的命令行里正好含 "uv sync"。表现是命令毫无输出、
   什么也没发生。
4. **阿里云到 PyPI 慢到装不完**：默认源几分钟才装 2 个包，
   换清华源后 **20 秒装完 92 个**。这不是优化，是能不能装完的问题。
5. **本机 `curl https://liushun666.cn` 全部失败，但站点是好的。**
   本机装了 VPN/代理做 fake-IP DNS 劫持，域名解析到了 `198.18.1.213`
   （保留段）。验证公网可达要用 `curl --resolve` 绕开本机 DNS，
   否则会得出「上线失败」的错误结论。

**裸装，不用 Docker**（1.6GB 上 Docker 本身的开销不划算）。

- [ ] ⭐ **先备份 Aura Note 目录和现有 nginx 配置**——改任何东西之前
- [ ] 阿里云安全组放行 80/443
- [ ] `setup-server.sh`：
  - `apt install postgresql-16 postgresql-16-pgvector`
  - **`shared_buffers=128MB`、`max_connections=20`**（默认值在 1.6GB 上太贪）
  - 建库建用户，跑 alembic 迁移
  - Python 3.12 + uv（**别碰系统 Python**）
- [ ] `deploy/nginx.conf`：
  - `location /` → `out/` 静态文件（配 `try_files $uri $uri/ /index.html;`，
    因为前端是 `trailingSlash: true` 的静态导出）
  - `location /api` → `127.0.0.1:8000`
  - ⭐ `location /images/ { alias <项目>/data/images/; expires 30d; }`
    —— 语雀配图，**必须由 nginx 直接发**。FastAPI 里那个 `app.mount("/images")`
    只是本地开发用的；1.6GB 的机器上让 Python 发几千张图纯属浪费
  - `location /aura` → Aura Note 静态目录（**保留**；确定下线时删这个 block 即可）
  - ⭐ **SSE 必须配**：`proxy_buffering off;` `proxy_read_timeout 300s;`
  - ⭐ `client_max_body_size 20m;`（默认 1m 会让上传直接 413）
- [ ] certbot 签 HTTPS + 自动续期
- [ ] `kb-api.service` + `kb-worker.service` systemd 常驻 + 开机自启
- [ ] `deploy.sh`：本机 `npm run build` → `rsync out/` → `systemctl restart`
  - ⭐ **`data/images/` 也要 rsync 上去**（约 3000 张图）。服务器上重跑
    `sync-yuque` 也能拿到，但那要再向语雀发几千次请求，不如直接传
- [ ] 首次线上 `kb sync-yuque` 灌数据 + `kb invite` 发码

**验收**：**别人在自己手机上打开 `https://liushun666.cn`，用你发的邀请码注册，问出带引用的答案。** 到这一步项目就活了。同时确认 `/aura` 仍能打开，且 `free -h` 剩余内存 > 200MB。

### M6 — 上传 + 私有库（2–3 天）　✅ **已完成 2026-08-17（含上线）**

- [x] `routes/docs.py` 上传接口，安全项**逐条**落实：
  - 白名单扩展名 `.md .txt .docx .pptx .pdf`（PDF 仅纯文本提取）
  - 大小上限 20MB（**边写盘边判**，不是先读进内存）+ 每用户 200 份
  - ⭐ **落盘用 uuid 重命名**，原始文件名只存 DB —— 防路径穿越
  - 存 `data/uploads/{user_id}/`，库里存**相对路径**（绝对路径搬不了机器）
- [x] 上传后 `enqueue` 解析任务（**与 documents 行同一个事务**）
- [x] `ingest/parsers.py`：md / txt / docx / pptx / pdf → Markdown
- [x] `jobs/queue.py` + `jobs/worker.py`：`FOR UPDATE SKIP LOCKED` 消费；
      状态机 `pending→running→done/failed`，失败存 `error`
- [x] 解析结果以 `owner_id={user_id}` 入库（复用 `write_chunks`，红线仍只有一处）
- [x] 文档管理页 `/documents`：上传（拖拽）+ 列表 + 状态 + 删除（**同时删向量块**）
- [x] 前端轮询 `GET /api/documents`，**只在有文档没解析完时轮**，跑完自己停
- [x] ⭐ `tests/test_isolation.py`：加了「真上传 → 真 worker → 真检索」的端到端隔离
- [x] `copilot worker` / `copilot-worker.service`（独立进程，`MemoryMax=400M`）
- [x] `deploy.sh`：同步 systemd 单元文件 + `uv sync --extra parse` + 重启两个服务
- [x] 部署上线 + 线上换账号实测

**本机验收（真 uvicorn + 真 multipart + worker 独立进程 + 真 SiliconFlow）**
```
上传 e2e-manual.docx  → pending → worker 跑一轮 → done / 1 块
  正文含 `# 一、电子面单设置` 与 Markdown 表格 `| 字段 | 含义 |`  ← 标题层级和表格都保住了
重复上传同一份       → duplicate=true，沿用原来那一行，不重复烧 embedding
上传 x.exe           → 415「不支持的文件类型（.exe），只收 …」
上传坏的 .docx       → failed，错误写着「打不开这个 Word 文件（PackageNotFoundError）」
检索（真 embedder + 真 reranker，库里 5268 个真实块）：
  本人   → 命中自己上传的那篇  True
  另一人 → False        未登录 → False        ← 隔离
删除                 → 204，块与落盘文件一起没了，公共库 746 篇不动
```
**测试**：`pytest` 187 passed（M5 时 137，新增 50 条），`ruff` / `eslint` / `tsc` 全过。

**做的时候想清楚的几件事**

1. **worker 必须是独立进程。** 解析一份 20MB 的 pptx 是同步 CPU 活，塞进 API
   进程就会顶住那个唯一的事件循环——别人正在流的答案会一起停住。分开还能
   单独限内存：真跑飞了被 systemd 收走的是 worker，网站还在。
2. **可重试与不可重试要分开。** 文件坏了（`ParseError`）重试一万次也是坏的，
   只会白烧 CPU；embedding 撞限流是过一会儿就好的。分不开的话，要么坏文件
   卡在队列里反复重试，要么一次网络抖动就让用户看到「解析失败」。
3. **`run_once` 返回三态而不是布尔。** 「有没有活干」和「干成了没有」混在一起时，
   主循环会把失败当成「还有活」→ 立刻接着取 → 同一条任务不带间隔地连撞三次，
   把重试次数在一秒内烧光。**重试也就白设了**，而且日志上看不出异常。
4. **落盘路径存相对的。** 绝对路径会把开发机的 `C:\Users\…` 写进库里，
   搬到 `/opt/copilot` 全都指不对——而这个库是要跨机器用的。
5. **删文档要连待办任务一起撤。** 不撤也不会坏（worker 认得出文档没了，
   作废即可），但队列会攒一堆注定作废的行；更要紧的是它让测试变得随机失败——
   下一个用例的 `run_once` 可能先取到这条孤儿任务。

**踩到的坑**

1. **⭐ `updated_at` 在复用旧行时会炸 `MissingGreenlet`。**
   它是 `onupdate=func.now()`，值由数据库算，提交后属性处于**过期**状态，
   序列化时一读就触发隐式 IO，在 async 会话里直接抛
   `greenlet_spawn has not been called`。
   可怕的是**新建行那条路径完全正常**——只有「重传上次失败的那份」会踩到，
   极容易漏过测试。解法是提交后 `await session.refresh(doc)`。
2. **`FakeEmbedder` 的桶下标带字符位置**（`ord(ch)*7 + i`），
   所以「块正文 = 标题 + 空行 + 原文」时，拿原文去搜反而**搜不到自己**——
   前面多几个字就把整个向量错开了。真实 embedder 没这毛病，
   但测试差点得出「隔离过度」的错误结论。测试数据要么不带标题，要么用原文当查询。
3. **单跑一个测试文件会全红**：`pytest ../tests/test_jobs.py` 时 rootdir 变成
   `Copilot/`，找不到 `backend/pyproject.toml` 里的 `asyncio_mode = "auto"`，
   于是所有 async 测试被当成同步的，报的是
   「requested an async fixture, with no plugin that handled it」。
   要么不带路径跑（`uv run pytest`），要么显式 `-c pyproject.toml`。
4. **`nginx client_max_body_size` 要比应用上限宽一点**（21m vs 20MB）：
   multipart 的分隔符和头也算进 body 长度，正好卡 20m 会让 19.9MB 的文件
   被 nginx 拦下，而 nginx 回的是一页 HTML，前端拿不到那句「文件超过 20MB 上限」。
5. **`uv sync --no-dev` 不装 extra。** 服务器上漏了 `--extra parse` 的表现是：
   网站一切正常，上传也成功，只是每份文档都转成「解析失败：服务端缺少 docx
   解析组件」。

**上线验收（公网实测，2026-08-17）**

用两个临时账号做的，验完已连数据一起删干净（`未用邀请码 0`、`文档 746` / `块 5268` 与灌数据时一致）。

```
/  /chat/  /documents/  /api/health   全 200
POST /api/documents 无 cookie          401
copilot-api / copilot-worker           active + enabled（开机自启）
服务器上 import docx/pptx/pypdf         ok      ← --extra parse 真的装上了
free -h 可用 1.0Gi                              ← 健康线 200MB

A 上传 m6-rule.md → worker 在 1 秒内取到 → done / 1 块
A 上传 x.exe      → 415        A 上传坏 docx → failed「打不开这个 Word 文件」
B 的「我的文档」  → []         B 删 A 的文档 → 404，A 那份还在

⭐ 隔离的线上验证（同一个问题，两个账号）
   问「本公司京东面单用哪个模板，打印偏移量设多少」
   A（主人）→「统一使用「JD-三联单-2026版」模板…[1] 偏移量上边距 3 毫米、
              左边距 2 毫米，否则运单号会压到裁切线上。[1]」引用首条 = m6-rule
   B（另一人）→「知识库暂无此内容。」无引用
```

**一个意外发现（留给 M8 量化）**：第一次验收用的测试文档写了「仅限内部查看」，
问题也偏离 ERP 领域（「爱丽丝的客户报价是多少」）。结果**连文档主人都被答成
「知识库暂无此内容」**——而检索层完全正常（那一块重排 0.9997 排第一，
上下文里就是答案）。是 prompt 的第二道闸门（铁律 3 + 「ERP 实施顾问」的身份设定）
把它挡了。对这个产品来说这个偏保守的取向大概是对的，但**保守到什么程度是没数的**，
正好是 M8 评测集要回答的问题：假阴性率（材料里有、却答不知道）现在完全没有度量。

### M7 — Agent 化（2–3 天）

用 Pydantic AI 把 M1 的检索包成工具，加上多轮追问能力。

- [ ] `agent/agent.py`：Pydantic AI Agent + 依赖注入（当前用户、db session）
- [ ] `agent/tools.py`：
  - `search_kb(query)` — 包装 M1 的 `retrieve.py`，**自动带当前用户的 owner 过滤**
  - `collect_requirement(field, value)` — 维护"需求清单"状态
  - `gen_checklist(profile)` — Pydantic 约束结构化输出
  - `export_excel(checklist)` — 落 xlsx 供下载
- [ ] `routes/chat.py` 切到 Agent，工具调用事件按 AI SDK 协议流式推给前端
- [ ] 前端渲染工具调用过程（AI SDK 原生支持）
- [ ] 最大轮数限制 + 工具失败恢复（单工具报错不能整轮崩）

**验收**
```
完整对话：Agent 主动追问平台数 / 仓库结构 / 日单量…
信息齐后 → 生成《实施配置方案.xlsx》→ 前端可下载
```

### M8 — 评测 + 优化（2–3 天）

- [ ] `eval/dataset.yaml`：30–50 题 = 常见 + 易错坑 + **"知识库没有、应答不知道"**
- [ ] `eval/run.py`：LLM-as-Judge 自动打分，可复跑
- [ ] 指标：准确率 / 引用正确率 / 幻觉率 /
      ⭐ **假阴性率**（材料里有、却答「暂无此内容」）——M6 验收时撞见了真实案例，
      现在这个数完全没有度量，而它和幻觉率是一对：prompt 的闸门收紧一分，
      幻觉降一点、假阴性涨一点，不量化就只能凭感觉调
- [ ] 跑一轮基线 → 调参（chunk 大小 / top-k / overlap）→ 出对比表
- [ ] ⭐ **此时才评估要不要加 `pg_jieba` 混合检索**——用数字说话，不是凭感觉
- [ ] 语雀定时增量同步（systemd timer）
- [ ] 每用户每日 token 配额兜底
- [ ] 日志与错误上报

**验收**
```bash
uv run python eval/run.py     # 出指标表
# 改一个参数再跑 → 能看到指标变化对比
```
---

## 六、风险登记

按「会不会拖垮进度」排序，不是按技术难度。

| # | 风险 | 影响 | 概率 | 预案 |
|---|---|---|---|---|
| R1 | **语雀改版**导致 `appData` 解析失效 | M2 停摆，公共库无法更新 | 中 | 解析器隔离在单文件 + 固化 HTML 样本回归测试，碎了立刻知道碎在哪；备用 `catalog_nodes` 接口 |
| R2 | **1.6GB 内存 OOM** | 线上服务被 kill | 中 | swap 兜底；永不在服务器 build；永不加载 ML 模型；上线后盯 `free -h` |
| R3 | **检索效果不达预期** | 整个产品价值不成立 | 中 | M1 就要发现，不要拖到 M8。地基期超 8 天答不准就停下来查 chunk 策略 |
| R4 | **跨用户数据泄漏** | 最严重，信任归零 | 低 | 过滤条件单点收敛 + `test_isolation.py` + M6 线上换账号实测 |
| R5 | SiliconFlow 免费额度取消或限流 | embedding/rerank 中断 | 低 | Provider 是 Protocol，切通义/百炼是改配置；付费版 ¥0.07 也不贵 |
| R6 | **战线拖长导致弃坑** | 第三次失败 | **中高** | M5 压在第 15 天；每个里程碑独立可验收；卡住超 1 天就跳过去做不依赖的部分 |

> **R6 是历史概率最高的那个。** 前两次都死在这里，不是技术问题。

---

## 七、贯穿始终的约定

1. **`data/`、`.env` 必须在 `.gitignore` 里**，M0 第一件事。用户上传的文档进了 Git 历史，清理成本极高。
2. **隔离过滤只有一处实现**，配测试守住。
3. **不用 PyMuPDF**（AGPL）。引入依赖前先看许可。
4. **每个里程碑过验收才往下走。** 前两次失败的根因就是终点太远、中途没反馈。
5. **不为假想需求先付工程费**：混合检索等评测证明它值得再加；Redis 等 Postgres 队列扛不住再上。
6. **密钥只在 `.env`**，服务器 `chmod 600`，永不进仓库。
7. **服务器上永不执行 `npm run build`**，永不加载 ML 模型。这两条是 1.6GB 的生死线。
8. **所有软件装 D 盘**（见「一·五」）。新增任何需要安装的东西前，先确认它的安装路径和缓存目录都指向 D。

---

## 八、端到端验证

```bash
# ── 本地 ─────────────────────────────
cd backend && uv sync
cp .env.example .env          # SILICONFLOW_API_KEY / DEEPSEEK_API_KEY / JWT_SECRET / DATABASE_URL
uv run alembic upgrade head

uv run copilot sync-yuque <URL>   # 期望：N 篇 / M 块
uv run copilot sync-yuque <URL>   # 再跑：期望 0 变更
uv run copilot ingest             # 期望：切分 + 向量化入公共库
uv run copilot ask "<有的问题>"    # 期望：答案 + [1] 来源链接
uv run copilot ask "公司年会在哪开" # 期望："知识库暂无此内容"
uv run copilot invite -n 3        # 发邀请码
uv run copilot serve              # 起 API，另开一个终端跑下面的 curl
uv run copilot worker             # 另起一个进程解析上传（--once 则清空队列就退出）
uv run pytest && uv run ruff check
# ⚠️ 单跑一个测试文件要带 -c：`pytest ../tests/x.py` 时 rootdir 变成 Copilot/，
#    读不到 backend/pyproject.toml 里的 asyncio_mode=auto，async 测试会全红
uv run pytest -c pyproject.toml ../tests/test_jobs.py

cd ../frontend && npm run dev  # 本机全链路
npm run build                  # 必须能产出 out/

# ── 上线前最后一步：.env 改两个值 ───────
# COOKIE_SECURE=true    （HTTPS 下 cookie 才存得住，同时关掉 /api/docs）
# CORS_ORIGINS=         （线上前后端同源，留空即可）

# ── 部署 ─────────────────────────────
./deploy/deploy.sh             # 本机构建 → rsync out/ → 重启 systemd

# ── 线上 https://liushun666.cn ───────
# 1. 邀请码注册 → 登录
# 2. 提问 → 流式输出 + 引用可点击跳语雀原文
# 3. 上传 md/docx → pending → done（`systemctl status copilot-worker` 看进展）
# 4. 提问命中新文档
# 5. 换账号 → 搜不到那份文档          ← 隔离的线上验证
# 6. "帮我做个实施方案" → Agent 追问 → 下载 xlsx
# 7. ssh 上去 free -h，剩余内存 > 200MB   ← 1.6GB 的健康线
#    （M5 起 Aura Note 已下线，别再去验 /aura；恢复办法在 deploy/nginx.conf 注释里）
```

---

## 九、开工前需要你提供

| # | 需要的东西 | 状态 | 最晚什么时候要 |
|---|---|---|---|
| 1 | SiliconFlow API Key | ✅ 已有（bge-m3 / bge-reranker-v2-m3 **免费额度**） | — |
| 2 | DeepSeek API Key | ✅ 已有 | — |
| 3 | 语雀知识库 URL | ✅ `https://www.yuque.com/wdterpqjb`（旗舰版ERP） | — |
| 4 | 服务器 | ✅ 2 核 / 1.6Gi / 40G，Ubuntu 24.04 | — |
| 5 | **容器身份查明** | ✅ 已查明并清理完毕（见第一节） | — |
| 6 | **SSH 访问方式** | ⬜ **M5 的唯一阻塞项**；不给的话我只写脚本、你自己执行 | **M5** |

**M0–M4 全部完成。** 只剩第 6 项待定，它决定 M5 由谁来按下执行键。

### 密钥存放

- 一律放 `.env`，`.env` 进 `.gitignore`，服务器上 `chmod 600`
- Gemini 仅本机可用（国内服务器访问不了 Google API），只用于 M8 的 LLM-as-Judge

**第一步产出**：本计划落成 `Copilot/plan.md` 作为进度台账，此后每完成一项就勾一个框。

---

## 参考

- [Pydantic AI](https://github.com/pydantic/pydantic-ai) · [Pydantic AI + FastAPI + pgvector 参考实现](https://github.com/serkanyasr/agentic_rag_project)
- [AI SDK Stream Protocol（自定义后端规范）](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) · [Vercel 官方 FastAPI 流式模板](https://vercel.com/templates/next.js/ai-sdk-python-streaming) · [fastapi-ai-sdk](https://pypi.org/project/fastapi-ai-sdk/)
- [Docling](https://pypi.org/project/docling/)（MIT）· [PyMuPDF 许可](https://pymupdf.readthedocs.io/en/latest/about.html)
- [SiliconFlow Embeddings](https://docs.siliconflow.cn/cn/api-reference/embeddings/create-embeddings) · [定价（bge-m3 / reranker 免费）](https://siliconflow.cn/pricing)
- [pg_jieba](https://github.com/jaiminpan/pg_jieba)（中文 BM25，M8 可选）
- [语雀 appData 解析思路](https://cloud.tencent.com/developer/article/2239704)
