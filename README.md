# 旺店通旗舰版 ERP 知识库 Agent

公网上线中：<https://liushun666.cn/>

一个带引用的 ERP 知识库问答助手。用户注册登录后提问，答案逐句标注来源、
带上原文档的操作截图，来源可点击跳回语雀原文；也可以上传自己的文档，
**只有本人能检索到**。

## 它能做什么

1. **带引用回答操作与配置问题**——每句有依据的结论都标 `[n]`，点得开、溯得回。
2. **带上操作截图**——原文档里有配图的步骤，`[图1]` 会渲染成真图。
   ERP 的操作步骤，一张截图顶三句话。
3. **读你自己的文档**——上传 md / txt / docx / pptx / pdf / 图片，
   后台自动解析入库，之后提问就能引用到它。**隔离是这个项目唯一
   错了就不可挽回的规则**（`owner_id`，见 ARCHITECTURE.md）。
4. **生成实施配置方案**——多轮追问清楚需求，最后给一份可下载的 Excel。

**知识库里没有的行业术语、通用概念，它会按通用理解说一说并声明没有出处；
但具体的界面路径、字段名、参数上限绝不会凭记忆编。**
那条线的来龙去脉在 DECISIONS.md，量它的那套评测在 EVALUATION.md。

## 架构概览

```
浏览器
  │  Next.js 15 静态导出（本机构建，服务器上只有 nginx 发静态文件）
  ▼
nginx ──► FastAPI（uvicorn 单 worker）
              │
              ├─ 检索：Postgres + pgvector ─► SiliconFlow embedding / rerank
              ├─ 生成：DeepSeek（简答）/ Kimi（详解）
              ├─ Agent：Pydantic AI，answer_kb 是终结工具
              └─ 队列：Postgres FOR UPDATE SKIP LOCKED ─► 解析 worker（独立进程）
```

细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。**没有 Docker、没有 Redis、
没有 Celery、没有独立向量库**——每一个「没有」都是有理由的，见 [DECISIONS.md](DECISIONS.md)。

## 本地运行

需要 Python 3.12（`uv` 管理）、Node 20+、Postgres 16 + pgvector。

```bash
# 后端
cd backend
cp .env.example .env          # 填数据库、SiliconFlow、DeepSeek 的密钥
uv sync --all-extras          # ⚠️ 别裸跑 uv sync，它会卸掉 parse/agent/eval
uv run alembic upgrade head
uv run copilot serve --reload

# 前端（另开一个终端）
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

灌一点语料：

```bash
cd backend
uv run copilot sync-yuque --limit 20    # 抓语雀公开知识库
uv run copilot ingest ../data/raw       # 或者灌本地 markdown
uv run copilot invite --count 1 --show  # 生成邀请码才能注册
```

## 测试

```bash
cd backend
uv run pytest          # 需要一个能连上的 Postgres
uv run ruff check
```

⚠️ **单跑一个测试文件要带 `-c pyproject.toml`**：
`pytest ../tests/x.py` 时 rootdir 会变成仓库根，读不到 `asyncio_mode=auto`，
async 用例会整片报 "requested an async fixture ... with no plugin"。

前端有改动才跑：

```bash
cd frontend
npm run lint && npx tsc --noEmit && npm run build
```

## 评测

```bash
cd backend
uv run python ../eval/run.py --check                    # 只验检索，不花钱
uv run python ../eval/run.py --tag baseline             # 公共库 75 题
uv run python ../eval/risk_boundary.py --tag risk       # 风险边界 48 题
uv run python ../eval/routing.py                        # 路由 63 题
```

指标口径、A/B 规则、判分器失效怎么处理，全在 [EVALUATION.md](EVALUATION.md)。
**最要紧的一条：`INVALID`（判分器自己挂了）不计入准确率。**

## 部署

```bash
bash deploy/deploy.sh
```

七步：本机自检 → 本机构建前端 → 推后端 → 推勘误层 → 同步 systemd 单元 →
推前端产物 → 装依赖/迁移/重启。**服务器上永不执行 `npm run build`、
永不加载 ML 模型**——这台机器只有 1.6GB 内存，这两条是生死线。

日常运维（备份、恢复、日志、限流、质量报告、事故处置）见
[OPERATIONS.md](OPERATIONS.md)。

## 文档地图

| 文件 | 写什么 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 每一层怎么工作，隔离和流式在哪里收口 |
| [EVALUATION.md](EVALUATION.md) | 四套评测集、指标定义、baseline、A/B 规则 |
| [OPERATIONS.md](OPERATIONS.md) | 部署、备份恢复、systemd、日志、事故检查表 |
| [DECISIONS.md](DECISIONS.md) | 为什么不用 Docker / Redis / Graph RAG…（ADR） |
| [plan.md](plan.md) | 历史台账。**看当前状态请看它最上面的 NOW / NEXT** |
