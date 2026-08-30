# AI Copilot — 实施计划

> **这份文件只留还没做的事。** M0–M13 的详细任务台账、M14–M20 的执行记录、
> 每一次排查和 A/B 的证据，都在 [ARCHIVE.md](ARCHIVE.md)。
> 需要写作品集材料时回那里取——最值得讲的三段都在里面。

## NOW

**Week 1、Week 2 已完成。Week 3 的 W3.1（MCP）、W3.2（校验 Agent）和
W3.3（M18 企业版语料）的代码均已就位，M18 只欠真实语料抓取与入库。
Week 4 的文字材料和生产真实读数已完成，只欠三张图和 MCP 录屏。**

✅ **判分器已恢复，正式评测门禁已于 2026-08-30 重跑为 PASS。** 公共库直路、
公共库 Agent、私有库直路、私有库 Agent、风险边界、路由、跨空间七项全部通过；
没有更换判分模型、没有降低阈值、没有跳过最新结果。

四周主线的目标是**把这个项目改造成能拿去面试的作品**，
岗位：AI Agent 开发 / AI FDE / AI 产品经理。顺序按依赖排：

| 周 | 主题 | 状态 |
|---|---|---|
| ~~**Week 1**~~ | ~~看得见~~ | ✅ 2026-08-28 |
| ~~**Week 2**~~ | ~~记得住~~ | ✅ 2026-08-29。W2.1 默认关（A/B 已出），W2.2 默认关，W2.3 **默认开** |
| **Week 3** | 接得上 | 🟡 W3.1 MCP ✅／W3.2 校验 Agent ✅（默认关，指标没动）／W3.3 **代码就位、语料没导** |
| **Week 4** | 讲得出 | 🟡 README / 案例文 / demo 脚本 / 三份一页纸 / ADR / **生产真实数字** ✅　三张图 / MCP 录屏 ⬜ |

---

### 四阶段收口进度（2026-08-30，工作区尚未提交）

#### 已完成并实际验证

| 阶段 | 完成内容 | 证据 |
|---|---|---|
| 1. 锁文件 | `uv.lock` 已同步 MCP extra；没有无关依赖升级 | `uv lock --check` 退出码 0；差异仅 5 行新增、1 行修改 |
| 1. 后端静态检查 | Ruff 覆盖 `backend / tests / eval` | `ruff check . ../tests ../eval` PASS |
| 1. 前端检查 | 单测、ESLint、TypeScript、Next.js 生产构建 | `npm.cmd run verify` PASS；Next.js 16.3.1 |
| 1. 隐藏依赖问题 | pytest 9.1.1 下异步 autouse fixture 显式交给 `pytest_asyncio` | 门禁 / CI 契约 / compose 定向测试 43 passed |
| 1. 文档漂移 | README / ARCHITECTURE 的 Next.js 16、AI SDK 7，README roadmap，生产读数状态 | 已修改，三张图与 MCP 录屏仍明确保留为未完成 |
| 2. 最小连通性 | 原 Moonshot 判分器，未换模型 | `moonshot-v1-128k`；无 429；3 题判分失败率 0% |
| 2. 公共库直路 | 正式标签 `gate-public-direct` | 准确率 96.0%，幻觉率 0%，判分失效 0 |
| 2. 公共库 Agent | 正式标签 `gate-public-agent` | 准确率 96.0%，幻觉率 0%，判分失效 0 |
| 2. 风险边界 | 正式标签 `gate-risk` | 准确率 96.4%，四条硬指标全 0，判分失效 0 |
| 2. 总门禁 | 七项统一汇总 | `eval/gate.py` 退出码 0，七项全部 PASS |
| 3. 部署门禁 | 新增 PASS / FAIL / UNRELIABLE 摘要、证据时间、语料指纹、部署类型和审计行 | AI 改动非 PASS 需按 commit 人工确认；安全补丁有明确出口；策略测试 PASS |
| 4. Docker 静态收口 | 缺 key 提前报清晰错误；API / Web healthcheck；依赖健康条件；持久卷契约测试 | compose 契约测试 PASS |

#### 留给下一次继续

1. **隔离数据库全量 pytest**：本次没有对开发库盲跑；应提交并推送后让 GitHub Actions
   使用临时 PostgreSQL 跑全套，或另建专用测试库。
2. **Docker 真机验收**：当前 Windows 机器没有 Docker，尚未执行 `build --no-cache`、
   从零启动、真实问答、错误 key / 服务不可用体验、重启持久化。
3. **文档最后对账**：`ISSUES.md` 的 I-0 / I-1 / I-2 和 `EVALUATION.md` 的“当前门禁红”
   仍是旧状态；提交前应改为已解决或历史记录。README 的门禁当前状态也需改绿。
4. **最终验证与交付**：重新跑完整 Ruff、前端 verify、`uv lock --check`，再提交
   `chore: sync lockfile and reconcile project status`、推送远程并确认 CI 全绿。
5. **仍未完成的产品/作品集项**：M18 真实语料抓取与入库、三张图、MCP 录屏，
   以及 M19-B 趋势页 / 定时回归和 M20 的扩大生产样本、路由收敛。

⚠️ 当前工作区包含上述代码、正式评测结果和文档修改，**尚未 commit / push**。

---

### 这一轮做完了什么（2026-08-29）

**本机全绿**：`ruff check . ../tests ../eval` 通过、`pytest` **880+ passed**
（Week 2 结束时 788）。

| # | 东西 | 落点 | 默认 |
|---|---|---|---|
| W2.1 | 上下文**预算装配器 + 滚动摘要** | `qa.split_history` / `qa.history_digest`，[ADR-21](DECISIONS.md) | **关** |
| W2.3 | 注入防线第三层 + **默认开** | `injection.strip_links`，[ADR-20](DECISIONS.md) | **开** |
| W3.1 | MCP server（stdio，三个工具） | `copilot/mcp_server.py`、`copilot mcp`，[ADR-23](DECISIONS.md) | — |
| W3.2 | 校验 Agent（出稿后核对） | `copilot/verifier.py`，[ADR-22](DECISIONS.md) | **关** |
| E1–E3 | 三个技术债（见下） | `tests/test_ci_contract.py` 等 | — |

#### ⭐ W2.1：那三个「停着等你拍板」的问题，答案是**不用模型**

plan.md 里 W2.1 停了很久，理由是滚动摘要「引入一笔按轮数计的经常性成本」，
于是停在三个决定上：用哪个模型、存哪里、阈值多少。

**那三个问题只有在「摘要必须由模型来写」这个前提下才存在。**
跨窗口那四道题问的是「我一开始说的是哪个版本 / 几个仓 / 哪家客户 / 哪些平台」——
**答案全是用户自己打过的原字**。让模型重写一遍换不来更准，
换来的是一次调用、一份延迟，外加一条会写错的路。

所以摘要是**抽取式**的：把挤出预算的那些轮次里**用户说过的话**按时间顺序列出来。

| 问题 | 答案 |
|---|---|
| 用哪个模型 | **不用模型**，`qa.history_digest` 是纯函数 |
| 存哪里 | **不存**，每轮现算。没有迁移、没有新列、没有缓存一致性问题 |
| 阈值 | `HISTORY_CHAR_BUDGET=1200` 字（全中文，1 字 ≈ 1 token） |

**付费 A/B**（`eval/longchat.py`，11 题 × 4 臂，规则判定不受判分器欠费影响）：

```
                         两个都关   只开事实表   开预算装配器   两个都开
上下文命中率                28.6%  →   57.1%   →    100.0%  →  100.0%
跨窗口解析成功率             54.5%  →   63.6%   →     90.9%  →   81.8%
  cross_window_fact 答对      1/4  →     2/4   →       4/4  →     4/4
  in_window_control 答对      3/3  →     3/3   →       3/3  →     2/3  ⚠️
  must_refuse 答对            2/2  →     2/2   →       2/2  →     2/2
```

⚠️⚠️ **最后一列是这轮最值钱的一个数**：两个开关一起开，**对照组掉了一道**。
那道题（长会话里的一个普通产品题）本来答得好好的，两个都开之后答案退化成
半条 + 一句「知识库暂无此内容」。事实表那一段讲用户情况的文字，
叠上摘要那一段列用户原话的文字，把模型从"读材料"推向了"读会话状态"。

⭐ 所以结论不是"两个都开"，是**开 W2.1、W2.2 继续关着**。
没有对照组的话，这个报告会写成「跨窗口 +3 道」，而不会有人发现窗口内掉了一道。

#### W3.2：加了第二个 Agent，**指标一个点没动**

`high_risk_hallucination_rate` 等四条硬指标**本来就已经是 0.0%**——
一个只能减少幻觉的东西，在幻觉率已经是 0 的地方只可能把对的答案降级成拒答。
56 题只差一个开关：四条硬指标全部 0.0% → 0.0%，22/56 的答案花了一次校验调用，
2 条被标注，**0 条误报**。详见 [ADR-22](DECISIONS.md)。

⭐ 敢报负结果、并写下"什么条件下我会打开它"，比"我做了多 Agent 编排"强。

#### ⚠️ 做这两轮 A/B 时量出来的四个 bug，比结论本身值钱

1. **`inj-fake-authority` 连着三轮被判成「注入成功」，而三轮都拒绝对了。**
   模型点名那条假路径是**为了拒绝它**，而拒绝措辞的清单一条都没匹配上
   （三轮三种写法）。⚠️ 一条 `==0` 的门禁红线为正确行为变红——
   而红多了之后，真出事那天没人看。
2. 校验器把「复制/补发规则」当成界面路径（`/` 在中文里是"或者"）。
3. 反过来，`【设置】-【打印设置】` 这种**最常见**的路径它一条都抽不出来。
   ⚠️ 症状是"校验器很安静"，看起来像它认为一切都有据。
4. 校验器的标注会把注入题的禁词**再打印一遍**——
   **一个防线把另一条防线的指标打红**，之前是靠相邻位置侥幸躲过。

四条都补了回归测试。全部记在 [ISSUES.md](ISSUES.md) 的索引表里。

#### 三个技术债

| 债 | 根因 | 修法 |
|---|---|---|
| `test_first_person_question_keeps_public_material` 偶发红 | 夹具跑在 4568 块真实语料上，而 `FakeEmbedder` 把每轮随机的 tag 也哈希进查询向量——**实测 30 轮里有 2 轮抽不到自己的材料** | 夹具自建知识空间，语料一条都进不来。顺带整份文件从 40s 降到 7.6s |
| 前端「本机绿 CI 红」没有机制防下一次 | 同一份自检清单抄在三个地方，靠一句注释维持一致 | 收成 `npm run verify` 一份；`typecheck` 先删 `.next/types` 再 typegen；`tests/test_ci_contract.py` 盯着谁再抄一份 |
| `eval/results/` 106 个文件全是 CRLF | 8 处裸用 `Path.write_text`（Windows 默认写 CRLF），而 git 的 `eol=lf` 在 `add` 时救了回来——**所以谁都没发现写文件那层是错的** | 统一走 `run.save_json`；后端两处也补上；`ast` 判据钉死 |

---

### ✅ 付费判分器与门禁已恢复（2026-08-30）

继续沿用原来的 Moonshot `moonshot-v1-128k`，先做最小连通性，再按顺序正式重跑：

```bash
cd backend
# ① 公共库两套
uv run python ../eval/run.py --tag gate-public-direct
uv run python ../eval/run.py --tag gate-public-agent --agent
# ② 风险边界
uv run python ../eval/risk_boundary.py --verify off --tag gate-risk
# ③ 门禁
uv run python ../eval/gate.py            # 实际退出码 0
```

结果：公共库直路 96.0%、公共库 Agent 96.0%、风险边界 96.4%；判分失效率均为 0，
四条风险硬指标均为 0。门禁沿用“最新一轮说了算”，没有改阈值或挑旧结果。

---

### 还开着的事，按「谁能做」排

#### ① W3.3 / M18：企业版语料首次导入（⚠️ 要抓取 + 付费 embedding）

**代码已经全部就位**（2026-08-29），剩下的是抓取和入库那几条命令：

| 原计划缺的 | 现在 |
|---|---|
| `sync-yuque` 写死输出目录 | ✅ `--space`，映射表在 `spaces.SPACE_ROOTS`，拼错当场退出 |
| `copilot ingest` 没有 `--space` | ✅ 有了，同时决定"从哪读"和"写进哪个版本" |
| 没有 `copilot spaces` 命令 | ✅ `list` / `activate` / `deactivate` |
| 聊天页没有版本选择器 | ✅ `ChatRequest.space` + composer 里的选择器（只剩一个版本时不显示；会话开始后变只读标签） |
| **`content_hash` 判重是全局还是按空间** | ✅ **plan 猜对了一半，另一半更糟**，见下 |

⭐⭐ **判重那一条值得单独说**。plan.md 写着「这是本步最值得先写测试的地方」，
写下来之后实测：判重用的是 `source_url + owner_id`，**没有空间这一维**。

    content_hash 一样  →  企业版那一篇被判成「已入库、跳过」，**静静少掉**
    content_hash 不同  →  ⚠️ **改的是旗舰版那一行**：正文换成企业版的，
                          而 `knowledge_space_id` 保持旗舰版不变

第二种是**跨版本污染发生在入库层**。`_space_filter` 过滤得完全正确——
它只能保证"旗舰版空间里的块才会被旗舰版的提问召回"，
保证不了"旗舰版空间里的块讲的是旗舰版的事"。
⚠️ 而且没有任何症状：文档数不变、块数不变、门禁的跨空间污染率照样是 0。
回归在 `tests/test_ingest_spaces.py`，**导入语料之前必须绿**。

⚠️ 还欠的两件：
1. **抓取 + 入库本身**（要网络 + 几千次付费 embedding）——**客户端企业版**
   （`huice-wiki`）2026-08-30 已确认可抓，全量抓取进行中；
2. **第 ③ 步：跨空间题集会在导入当天失效**——完整说明在下面「M18 执行细节」。

✅ 开发库里 `enterprise_desktop` 的孤儿文档已清（2026-08-30 实测 189 篇、
全部 `chunk_count=0`，一次性脚本删完，见 [ISSUES.md](ISSUES.md) I-10）。

⛔⛔ **网页版企业版这一轮不导入，范围缩小成只导客户端企业版。**
`enterprise_web`（`zsxj.yuque.com/da3ftb/vgswhb`）走的是**自定义域名的独立
语雀团队**，和旗舰版、客户端企业版都不是同一个可匿名访问的公开空间：

    书目 / TOC / 标题   →  公开，SSR 页面里直接带（appData.book.toc）
    正文内容（/api/docs/…）→  401 force_redirect_login，即使直连该域名也一样

即**换个 host 也解决不了**——这不是抓取器的 bug，是这个语雀团队本身没有对
匿名请求开放正文接口。要拿到内容只有三条路，成本依次升高：
用户自己导出成 Markdown/PDF 直接给文件、用用户的登录态做认证抓取（要碰
session、给抓取器新增一整条认证路径）、或者干脆不导。**这一轮选最后一条**——
记进 ISSUES.md 当已知缺口，等有导出文件或愿意接认证抓取再重新评估。

#### ③ Week 4：讲得出　—— 只剩三张图和 MCP 录屏

| # | 东西 | 状态 |
|---|---|---|
| W4.1 | README 重写成面试入口 | ✅ 第一屏答四件事，架构图 ✅ `docs/img/architecture.svg`，门禁证据 ✅（代码块，真机截图本环境导不出），span 树截图还欠 |
| W4.2 | 案例文《把笔从模型手里拿走》 | ✅ `docs/case-把笔从模型手里拿走.md` |
| W4.3 | 5 分钟 demo 脚本 | ✅ `docs/demo-5分钟脚本.md`，每步写了"会出什么岔子" |
| W4.4 | 三份岗位定制一页纸 | ✅ `docs/一页纸-三个岗位版本.md` |
| W4.5 | ADR 补齐 | ✅ 23 份。四条破的各一份，**三条不破的也各一份**（ADR-1/2/3） |
| — | **生产真实数字** | ✅ 已写入 README（2026-08-29，336 次提问） |

#### ④ 真机跑一次 `docker compose up`（⚠️ 本机没 Docker）

#### ⑤ Langfuse 接上（需要你注册账号）

`TRACING_ENABLED=false`，线上没开追踪。本机看树不需要账号：`TRACING_CONSOLE=true`。

#### ⑥ 阿里云安全组只留 22/80/443（⚠️ 只有你能做）

#### ⑦ 生产真实数字进 README（✅ 2026-08-29 完成）

`copilot quality-report --days 30` 的真实读数已写入 README：336 次提问、
TTFB p50 2.7s / p95 9.8s、越过工具直答 0；差评率同时保留了只有 5 次评价的
小样本警告，没有把不可靠的百分比包装成结论。

#### ⑧ MCP 录一段 30 秒的屏（简历里能放的东西）

`copilot mcp` 已经真的连通过一次（见 [ADR-23](DECISIONS.md) 的验收），
但录屏要在真的 Claude Desktop 里录。

---

## 第 0 步 — 开工前（半天）

### 0.1 确认线上跑的是哪一版

动任何东西之前先量一次。本机和线上不是同一版代码的话，后面所有验收都是假的：

```bash
# 服务器上
sudo -u postgres psql copilot -c "select version_num from alembic_version;"
# ⚠️ 单元名是 `copilot-api`，不是 `copilot`。写错的表现是
# 「Unit copilot.service could not be found」——看起来像服务没装，
# 实际上它跑得好好的（2026-08-29 差点据此得出"线上挂了"的结论）
systemctl status copilot-api copilot-worker
```

- 停在 `a2c8f47b91d6` → 三笔改动**没上线**，走 0.2。
- 停在 `b7e91c4d2a08` → 已上线，跳过 0.2。

### 0.2 部署攒下的三笔改动（如果还没）

三笔：M19-A 评测契约与门禁、来源清单只列正文引用过的那几条、纠错传图 P0/P1/P2。

```bash
bash deploy/deploy.sh      # 它自己会先备份
```

⚠️ 带 migration `b7e91c4d2a08`。**它的 downgrade 会删掉所有纠错图**，
回滚优先用备份，不要跑 downgrade。

部署完手工走一遍：登录 → 提问 → 看来源清单条数是否收窄 → 提交一条带图纠错 →
`/admin/corrections` 发布 → 再问一遍拿到订正原文。

### 0.3 通知用的人两件行为变化

已经上线但没说过：

1. 「答错了，我来改」现在是**提交审核**，管理员在 `/admin/corrections` 发布之后才生效；
2. 上传多了 xlsx；Word / PPT / PDF 里的截图会跟着正文一起进知识库（私有，只有本人看得到）。

**不花时间，但它带来真实使用数据**——那正是 M20 唯一缺的东西，而且靠时间买不到，
越早通知越好。

---

## ~~Week 1 — 看得见~~　✅ 2026-08-28 完成

> **这一节按本文件的规矩（只留还没做的事）删掉了任务清单。**
> 交付了什么、A/B 的数字、以及留下的三件事，都在最上面的 NOW；
> 三条设计取舍各有一份 ADR（[ADR-15](DECISIONS.md) OTel、
> [ADR-16](DECISIONS.md) 混合检索、[ADR-17](DECISIONS.md) compose）；
> 检索那一路怎么工作见 [ARCHITECTURE.md 第三节](ARCHITECTURE.md)，
> 关键词题集的口径见 [EVALUATION.md 第四·五节](EVALUATION.md)。

---

## Week 2 — 记得住（上下文工程）

> **W2.2 和 W2.3 已落代码，这一节按本文件的规矩删掉了它们的任务清单。**
> 交付了什么、免费 A/B 的数字、还欠哪几轮付费评测，都在最上面的 NOW；
> 设计取舍各有一份 ADR（[ADR-19](DECISIONS.md) 结构化事实表、
> [ADR-20](DECISIONS.md) 注入防线两层）；题集口径见
> [EVALUATION.md 第四·六节](EVALUATION.md)（长会话）和
> [第五节](EVALUATION.md)（注入）。

### W2.1　从固定窗口到预算装配器　⬅️ 只做完了基线

**做什么**　把 `qa.assemble_messages` 的内部从 `history[-HISTORY_TURNS:]`
换成按 token 预算装配的**分区上下文**：

```
[系统指令]
[任务状态：已确认事实]        ← ✅ W2.2 做完了，就是 session_facts 那一段
[更早对话的滚动摘要]          ← ⬅️ 还没有。超预算时才生成
[近 N 轮原文]                 ← 现在是固定 6 条消息
[本轮检索材料]                ← ✅ W2.3 给它加了围栏
```

超预算时先把最早的轮次压成摘要，而不是整段丢掉。

**已经就位的两样**：

1. **装配的缝**　`qa.assemble_messages()`——这几行原来长在 `ask_stream` 中段，
   拆出来是为了让它**能被量**（拆分本身不改任何行为）。W2.1 要换的就是它的内部。
2. **基线**　`eval/longchat.py --check`，零成本。数字在 NOW 里。
   ⚠️ **没有改前数字，改完的数字什么都不说明**——这条规矩之所以能落地，
   靠的正是那一档是免费的：一条要花钱才能遵守的规矩，迟早没人遵守。

**停在这里等你拍三件事**（都是产品决定，不是技术决定）：摘要用哪个模型、
摘要存哪里（`conversations` 加一列还是每轮重算）、超预算阈值定多少 token。
理由是它引入一笔**按轮数计的经常性成本**。

**怎么验**　`eval/longchat.py`，`cross_window_fact` 要涨、
**`in_window_control` 一分都不许掉**（上下文装配翻车的典型方式不是"跨窗口没修好"，
是"窗口内的反而变差了"）。

⚠️ 改完之后 `lc-earliest-question-out-of-window` 那道题的期望**要跟着改**：
现在它期望「我无法确认你最开始问的是什么」，有了滚动摘要之后就不该再是这个答案。
题集里留了记号。同理 `agent/deps.py` 的 `history_truncated` 和
`runner._beyond_window` 要再改一次语义。

**面试怎么讲**　上下文工程是 2026 面试的核心考点，而「从固定窗口到预算装配」
是一张能画在白板上的图——分几个区、谁先被牺牲、为什么。
⭐ 更好讲的是那张图**已经有两个区落地了**，而且每个区都有一个免费的量法。

---

## Week 3 — 接得上

### W3.1　MCP server　🔴 破线

**做什么**　把 `search_kb` / `my_documents` / `answer_kb` 暴露成 MCP server，
让 Claude Desktop、Cursor 能直接连上知识库。约 200 行。

**为什么**　JD 高频词，成本却很低。对 FDE 叙事尤其强：
**客户已经在用的 AI 工具，不用改造就能接进我们的知识库**——这正是 FDE 每天在解释的事。

**怎么验**　在 Claude Desktop 里连上，问一个 ERP 操作题，拿到带引用的答案。
**录一段 30 秒的屏**，这是简历里能放的东西。

**⚠️ 鉴权是重点**　MCP server 不能做成一个无鉴权的私有文档读取口。
`user_id` 只能从登录态来，绝不能变成工具入参——理由见 `agent/deps.py` 那段注释。

**面试怎么讲**　讲鉴权：「我做 MCP 时第一件事是想清楚 user_id 从哪来。」

---

### W3.2　校验 Agent：唯一值得加的第二个 Agent　🔴 破线

**做什么**　高风险答案（操作步骤类）出稿后过一个 verifier Agent：
「这段答案里的每条界面路径、每个参数值，在材料里找得到吗？」
找不到的整段降级为拒答或显式标注未核实。

**为什么**　**不为多而多。** 它直接服务于项目的核心指标（高风险幻觉率），
而且能用现有的 47 题风险边界题集**量出效果**。这才是多 Agent 编排该有的样子：
因为有个指标非它不可。

**怎么验**　风险边界题集上做开 / 关 verifier 的 A/B——同题集、同语料、只差这一条，
和 08-23 查铁律 9 时用的是同一套方法（见 ARCHIVE.md）。

**成本**　只对高风险问题开，每轮多一次调用。几十元。

**面试怎么讲**　⭐ **如果指标没动，就写进 ADR 说「这个场景不需要第二个 Agent」——
那是一个更强的面试答案。** 敢报负结果的候选人极少。

---

### W3.3　M18：企业版语料首次导入（压缩到 3 天）

完整执行细节见下面「M18 执行细节」一节。压缩版只做四件事：
抓取 → 入库 → 改写跨空间题集 → 激活 + 门禁。**聊天页版本选择器要做**，
它是演示时看得见的那一半。

**为什么值得占掉三天**　换来一个强叙事：**同一个 Agent 服务三个产品版本，跨版本污染率 0**。
多租户 / 数据隔离是企业级 AI 应用的第一考题，而这个项目的隔离是量出来的，不是声称的。

**面试怎么讲**　「隔离是这个项目唯一错了就不可挽回的规则」——然后展示那套
**故意把空间过滤短路掉、四条硬指标当场破线三条、漏出 49 张截图**的验证。
这证明测试真的咬得住。

---

## Week 4 — 讲得出

> **这一周的价值可能高于前三周之和。** 所有好东西现在都埋在 ARCHIVE.md 里。
> 面试官看仓库**平均不到 3 分钟**——他不会翻到第 2707 行去发现你把幻觉率打到了 0。

### W4.1　README 重写成面试入口

一屏说清：这是什么、解决什么真实问题、三张图（架构 / 一次请求的 span 树截图 /
门禁全绿截图），然后是「三个可以深挖的决策」的链接。

**验收**：找一个不懂 ERP 的朋友读 3 分钟，让他复述这个项目在解决什么问题。
复述错了就是 README 的问题。

### W4.2　案例文《把笔从模型手里拿走》

把 M7 → M10 那次单独成文：症状（模型自己写答案，41 题 87.8% / 幻觉 12.5%）→
四条成因的排查 → 架构决策（终结工具，模型再也看不到原始材料）→ 结果（100% / 0%）→
代价（模型看不到答案，调试变难）。素材在 ARCHIVE.md 的 M10 一节。

**验收**：能在 10 分钟内讲完，且中途不需要打开代码。
被问「讲一个你做过的最难的技术决策」时，这就是答案。

### W4.3　5 分钟 demo 脚本

写死一条路径，面试共享屏幕时照着走，**不即兴**：

1. 问一道操作题 → 答案带截图、带可点击引用
2. 点引用 → 跳到语雀原文对应段落
3. 传一份私有文档 → 提问命中它
4. 换账号 → 同样的问题搜不到那份文档
5. 换知识版本 → 同一个问题得到企业版的答案
6. 问「帮我做个实施方案」→ 多轮收集 → 下载 xlsx
7. 打开 span 树 → 指着讲这一轮的耗时构成
8. 打开门禁结果 → 七条 PASS，退出码 0

第 4、5 步是压轴：**隔离和多租户是当场就能看见的东西**，比任何架构图都有说服力。

### W4.4　三份岗位定制的一页纸

同一个项目三种讲法：Agent 开发版（架构与编排打头）、FDE 版（交付与运维打头）、
PM 版（指标与取舍打头）。投递时附对应那一份。

### W4.5　DECISIONS.md 补齐 ADR

四条破线各一份（OTel / MCP / verifier / compose），**三条不破的也各写一份**
（为什么不用 ES、不换向量库、不上 Redis）。每份都写：上下文 / 决策 / 代价 /
**什么情况下我会改回去**。

不破的那三份作用比破的还大——它们证明你评估过并且知道边界在哪。
「为什么不用向量数据库？」几乎必考。

---

## 每周验收

每周末问自己这一句。答不上来说明这周的东西还不能带进面试，那和没做完是一回事。

| 周 | 能带进面试的东西 | 一句话验收 |
|---|---|---|
| ~~W1~~ | ✅ hybrid 的改前改后数字（35/45 → 44/45）+ 一套能 `docker compose up` 的仓库 | ⚠️ **半条没答上**：span 树本机能看，但线上 `TRACING_ENABLED=false`，所以「线上这一轮 9 秒花在哪」目前**仍然说不出来**。要么线上开采样跑一周，要么面试时用本机那棵树讲结构 |
| W2 | 上下文分区图 + 长会话指代解析的改前改后 + 注入题集进门禁 | 第 15 轮问「我一开始说的是哪个版本」，答对吗？ |
| W3 | MCP 连 Claude Desktop 的录屏 + verifier 的 A/B 数字 + 三空间隔离演示 | verifier 让哪个指标动了几个点？没动的话写下来了吗？ |
| W4 | 3 分钟能读懂的 README + 案例文 + demo 脚本 + 三份一页纸 + 七份 ADR | 不懂 ERP 的人读完 README，能复述这个项目在解决什么问题吗？ |

**如果只剩两周**：保留 Week 1 全部（W1.1 / W1.2 / W1.3）和 W4.1 / W4.2 / W4.5。
MCP 和 verifier 是锦上添花；一个跑不起来、读不懂的仓库是釜底抽薪。

---

## 红线取舍

项目一直有条红线（「七、约定 5」和「已核对的实现边界」）：不引入 Redis / Celery /
ES / 向量库 / MCP / 多 Agent / Docker。**该破就破，但每破一条写一份 ADR。**

| 红线 | 决定 | 结果 |
|---|---|---|
| OTel / Langfuse | 🔴 **破** | ✅ W1.1 已破，[ADR-15](DECISIONS.md)。默认关 + 可选依赖 + 生产采样 |
| Docker | 🔴 **有限度破** | ✅ W1.3 已破，[ADR-17](DECISIONS.md)。只加本地 compose，**生产仍不用，ADR-1 一字未改** |
| Elasticsearch | ⚪ **不破** | ✅ 兑现了。W1.2 的 BM25 走 Postgres `tsvector` + GIN，一个新服务都没加（[ADR-16](DECISIONS.md)） |
| MCP | 🔴 **破** | Week 3。JD 高频，约 200 行；FDE 叙事强 |
| 多 Agent | 🔴 **有限度破** | Week 3。只加一个有指标理由的 verifier。为多而多，一问指标就穿帮 |
| Qdrant / Milvus | ⚪ **不破** | pgvector 够用。**能说清「什么规模才需要换」比换掉它值钱** |
| Redis / Celery | ⚪ **不破** | Postgres 队列已在生产跑着。「用最少的组件解决同样的问题」是资深信号 |

⭐ **「不引入混合检索」这条 2026-08-28 撤销了一半**，而且值得说清楚撤销的是哪一半：
当初 ADR-3 把"混合检索"和"为混合检索多养一个有状态服务"写成了同一件事。
W1.2 只做了前者。ES 那一条**因此更站得住了**，不是被推翻了。

---

## 明确不做的事

写下来是为了不反复捡起来。这些 JD 上都有，但对**这个项目**是堆砌——
面试官一问「它解决了你的什么问题」就穿帮。

- **微调（fine-tuning）**——问题是知识时效和隔离，不是模型能力。微调解决不了「语料更新了怎么办」。
- **语音 Agent / 浏览器 Agent**——和 ERP 知识库没关系，加了就是关键词填空。
- **GraphRAG / 知识图谱**——4568 块用不上，而且语料是操作手册，实体关系稀疏。
- **换向量库**——见上面红线表。
- **嵌图视觉转写**（原路线图 39）——先用 M19-A 的配图指标量一遍「转写对检索有没有用」
  再决定。一份 PPT 就是几十次付费视觉调用。
- **把 trace 拆成 router / retrieval / rerank / generation 四段做缓存或并行**——
  W1.1 的 span 树已经把段分好了，但**线上还没开采样，归因数据一条都没有**。
  拿到线上 p95 的分段构成之前不动。**先看见，再优化。**

---

## M18 执行细节

> Week 3 用。语料来源已定：语雀另外两个团队空间，和旗舰版同一条抓取路径，只是 login 不同。

### 现状盘点（已核实）

已经有的，不用再造：

| 东西 | 在哪 | 状态 |
|---|---|---|
| 四个空间的定义与种子 | `spaces.py` | 企业版两个已建表，`status='inactive'` |
| SQL 层空间过滤 | `retrieve.py` 的 `_space_filter` | 全项目唯一一处，没 space_id 时 fail closed |
| 入库带空间 | `ingest/pipeline.py` | `ingest_documents(..., space_id=)` 已就绪 |
| 图片跟着文档带空间 | `assets.sync_document_assets()` | 从 Document 冗余 |
| 可选空间列表 API | `api/routes/spaces.py` | 已就绪，只列 active 且 selectable 的 |
| 跨空间门禁 | `eval/cross_space.py` + `gate.yaml` | 4 条硬指标，不依赖判分器 |

缺的：**1–4 已经全部做完（2026-08-29），只剩第 5 条和抓取/入库本身。**

1. ~~`sync-yuque` 写死输出目录~~ → ✅ `--space`，映射表 `spaces.SPACE_ROOTS`，
   拼错**当场退出**（默默落回默认目录是这一步最危险的错法）。
2. ~~`copilot ingest` 没有 `--space`~~ → ✅ 有了，同时决定"从哪读"和"写进哪个版本"。
3. ~~没有 `copilot spaces` 命令~~ → ✅ `list` / `activate` / `deactivate`。
   ⚠️ `activate` 的闸门**数块不数文档**：开发库里 `enterprise_desktop`
   有 161 篇文档、0 块，按文档数判的话闸门当场放行，而检索一条都召不回。
4. ~~聊天页没有版本选择器~~ → ✅ `ChatRequest.space` + composer 里的选择器。
   只剩一个版本时不显示；会话开始后变只读标签；传错/未启用 **400，在开流之前**。
5. **跨空间题集会在导入当天失效**（见下面，本次最容易漏的一点）　← **还没做**

⭐⭐ **另外补了一条盘点里没有的**：`content_hash` 判重**没有空间这一维**。
plan 说「这是本步最值得先写测试的地方」——写下来之后发现它猜对了一半：

    content_hash 一样  →  企业版那一篇被判成「已入库、跳过」，**静静少掉**
    content_hash 不同  →  ⚠️ **改的是旗舰版那一行**：正文换成企业版的，
                          而 `knowledge_space_id` 保持旗舰版不变

第二种是**跨版本污染发生在入库层**，而门禁那套题量的是召回、不是内容——
没有任何症状。修在 `_ingest_one`，回归在 `tests/test_ingest_spaces.py`。

### 步骤

**① 抓取与入库**

`sync_yuque()` 加 `root` 参数；不给还是 `data/raw/yuque`（旗舰版原地不动，不用重抓）：

```
--space flagship            → data/raw/yuque/            （默认，兼容今天）
--space enterprise_desktop  → data/raw/spaces/enterprise_desktop/
--space enterprise_web      → data/raw/spaces/enterprise_web/
```

用一个 `SPACE_ROOTS` 映射表把「flagship 是历史遗留路径」写在一处。
不搬旗舰版：搬了要全量重新向量化（几千次付费 embedding），换来的只是目录好看。

`--space` 拼错**当场退出**，不能默默落回默认目录——那正是没有任何症状的错误。

⚠️ **勘误层只对旗舰版生效**：今天用 `if owner: corrections = {}` 跳过私有库
（`cli.py`）。企业版是**公共库但不是旗舰版**，判据要改成「非旗舰版空间也跳过」——
现有勘误的 `target_url` 全指向旗舰版语雀文档，拿去盖企业版一条都对不上。

⚠️ **`content_hash` 判重是全局的还是按空间的，入库前必须确认。**
两个版本有同名同内容文档时，跨空间被判成「已入库、跳过」的话，企业版会静静少掉几篇。
**这是本步最值得先写测试的地方。**

**② 新增 `copilot spaces` 命令组**

```
copilot spaces list                # 四个空间 + 状态 + 文档/块/图片数
copilot spaces activate <code>     # inactive → active
copilot spaces deactivate <code>   # 回滚用：导错了先让用户看不见
```

`activate` 要有闸门：文档数为 0 时拒绝激活，除非显式 `--force`。
激活一个空空间，用户问什么都得到「知识库暂无此内容」，**他会以为是系统坏了**。

```bash
cd backend
.venv/bin/copilot sync-yuque <login> --space enterprise_desktop --limit 5   # 先试跑
.venv/bin/copilot sync-yuque <login> --space enterprise_desktop            # 再全量
.venv/bin/copilot ingest ../data/raw/spaces/enterprise_desktop --space enterprise_desktop --limit 5
.venv/bin/copilot spaces list                                              # 看数字
.venv/bin/copilot ingest ../data/raw/spaces/enterprise_desktop --space enterprise_desktop
```

⚠️ 服务器上只能 `.venv/bin/copilot`，**不要 `uv run`**（它会把 extra 卸掉）。
⚠️ 语雀私密文档匿名抓不到，`stats.restricted` 会如实报数——**它不是失败**，
但要记下有几篇，因为「企业版答不出某个问题」很可能就是那几篇没抓到。

**③ 跨空间题集要重写一版　← 最容易漏的一步**

今天 `eval/cross_space.yaml` 的 probe 组，前提是**企业版空间是空的**：
在 `enterprise_desktop` 里问「极兔的平台物流编码」，期望「一块都不召回、必须拒答」。

**导入之后这个前提当场消失。** 企业版有自己的极兔编码了，拒答反而是错的。
一整组 probe 会从「隔离的证据」变成「一堆必然失败的题」——更危险的是，
如果为了让门禁变绿去放宽判据，就等于亲手拆掉唯一咬得住隔离的那套题。

| 组 | 问什么 | 期望 |
|---|---|---|
| probe（第二代） | 一个三个版本答案**都不一样**的问题，在企业版里问 | 答得出来，`banned` = 旗舰版那个答案/路径 |
| control | 同一个问题在旗舰版问 | 答得出来，且是旗舰版那个答案 |
| probe（保留一部分） | 一个**只有旗舰版才有**的功能，在企业版里问 | 仍然必须拒答 |

出题规矩不变：`banned` 只写**别的空间才有的具体事实**——一个数字、一条界面路径、
一个编码。形容词判不了，别写。
⚠️ **配图那两条负例要留着**：正文拦住了、配图从另一条路带出来，
用户看到的是另一个产品的界面截图——这是隔离最容易漏的一环。

**④ 顺序是死的**

```bash
cd backend
uv run python ../eval/cross_space.py --tag pre-m18-import   # ① 导入前存档
#                                                            ② 导入
#                                                            ③ 改写题集
uv run python ../eval/cross_space.py --tag post-m18-import  # ④ 再跑
uv run python ../eval/gate.py                               # ⑤ 门禁必须 0
```

导入前那轮不为证明什么，只为**留个对照**：post 真出了污染，才分得清是
「导入带进来的」还是「本来就有」。

**⑤ 聊天页版本选择器**

后端：`ChatRequest` 加 `space: str | None = None`。不传 → `spaces.default_id()`，
老前端不能因此 422（和 `mode` 同一个处理法）。传了不存在/inactive 的 code **报错**，
绝不退回默认值。**已有会话的 space 不许改**——传上来的和会话已有的不一致时按会话原有的走，
否则之前那几轮的答案就来自另一个产品了。

前端：选择器放 `composer.tsx`，和「简答 / 详解」档位同一处。
只剩一个空间时**不显示**（前端规范里的「假按钮」）。会话已有消息后变成只读标签，
说明「换版本请新建会话」。

**⑥ 激活、部署、线上验收**

顺序不能反：本机门禁全绿 → 服务器备份 + 本机异地拉一份 → 部署代码 →
**在服务器上**抓取入库（不是把本机的库推上去）→ `spaces list` 确认数字不为 0 →
`spaces activate` → 立刻手工过一遍：

- 新建会话 → 选客户端企业版 → 问企业版特有问题 → 答案对、来源是企业版文档；
- 同一问题换旗舰版会话问 → 答案不同；
- 企业版会话里问只有旗舰版才有的功能 → **拒答**，不是编一段；
- 操作题看配图 → 截图是企业版界面；
- 换个账号 → 私有文档仍搜不到（隔离的另一根轴不能被这次带塌）。

**回滚**：`copilot spaces deactivate <code>`，用户立刻看不到，数据留库里慢慢查。
比删语料快，也不会连带删掉图片。

**完成定义**：导入前后跨空间污染率均为 0、`control_answer_rate` 100%、
`eval/gate.py` 退出码 0、三个空间各被真人问过一遍、`deactivate` 回滚演练过一次。

---

## 推迟：M19-B / M20

不作废，但四周内不做。**要在 README 的 roadmap 里写清楚它们是什么、为什么还没做**——
「有清晰的下一步、且知道每一步的前置条件」比「做完了一切」更像一个真实在维护的项目。

### M19-B — 评测中心与持续回归

进入条件：M18 通过。要做的事：

1. **先补 `request_trace` 的列**（是其余部分的前置）。现在**没有 `knowledge_space_id`**，
   `quality-report` 就分不出「这 300 轮里企业版占多少、错在哪个版本」。一并补：
   `verified_answer_id`、`general_knowledge_used`、`image_count`、`correction_id`。
   都可空、老数据不回填。
   ⚠️ `answer_source` 口径不变：KB + 常识兜底时仍是 `kb` + `general_knowledge_used=true`，
   **不新增 source 值**，否则历史统计的分母会变。
2. 给两个企业版各建一套 baseline，各 30 题左右够用（别一开始凑 75 题）。
   **不要照抄旗舰版题面**——抄了的话，答案不同的题会全错，答案相同的题又证明不了隔离。
3. **给评测本身写测试**（四个人工构造的用例）：Temu 问题 + 小红书证据 → 跨平台污染；
   网页版问题 + 客户端证据 → 跨空间污染；引用编号存在但内容不支持 → 无支撑引用；
   图片编号存在但属于另一平台 → 配图串台。
   这条有先例：08-24 发现「配图串台率的第一版判据是错的」，靠人眼看出来的，
   不该指望第二次还有人看得出来。
4. `copilot eval-publish` + 只读的 `/admin/evaluations`。
   **评测不在生产网页里实时跑**——一次全量是两百多次付费调用，放进网页等于给了一个
   「点一下花几十块」的按钮。
5. 闭环最后一段：标准答案发布后**生成一条回归用例进题集**。第一版手工就够
   （发布页多一个「加入回归题集」按钮，导出 yaml 片段人工贴）。

⚠️ 原路线图 §46 要求把 `eval/` 按空间拆目录，**建议不拆**，只新增两个目录。
拆了要动 `gate.yaml` 每一条路径和所有历史结果的读取，门禁读不到旧证据会全变 FAIL——
而门禁一旦红成一片，就没人看了。

### M20 — 生产验证与 Agent 路由收敛

删掉 `_chat_stream`、`AGENT_TRIGGERS`、`profile is not None` 旧粘性路由。
前置条件（一条不满足就不删）：

- 真实用户用满一周，`quality-report --route agent --days 7` 里没有用户可见的失败。
  ⚠️ 08-24 那次 16 轮带 error 里有 11 轮是 `UsageLimitExceeded`——**那不是用户看到的失败**，
  runner 早把它降级成「这一轮到此为止」，答案照发。报告要能把降级和真失败分开。
- 路由评测 `route_accuracy >= 95`、`bypass_rate == 0` 稳定（不是单轮）。
- **回滚演练做过并留证**（现在没做过）：在生产真走一遍备份 → 部署 → 回滚 → 验证数据完好，
  记录回滚到哪个 dump、花了多久、期间用户看到什么。
  ⚠️ 已写入用户新数据的生产库**不做会丢数据的 downgrade**。
- 全量门禁退出码 0。

删完立刻重跑全量评测 + 门禁——删代码最容易出的问题是「顺手删掉了还有人用的分支」。

**为什么现在删不了**：灰度还是白名单模式，线上活跃用户只有 1 个（就是自己），
按 `agent_rollout` 的注释，3 个注册账号谈百分比灰度等于观察零样本。
而 `UsageLimitExceeded` 那个 bug 正说明 Agent 路还在长新形态的故障，
它当时的表现是**完全没有症状**。

⭐ 面试时这本身是个好答案：「我知道现在还不能删，因为活跃用户只有 1 个。」

### Release Blockers（上线前逐条留证）

多数已经有测试，这一步是**把证据对齐到条目上**，对不上的就是真缺口：

```
跨用户私有文档泄漏 / 跨用户私有图片泄漏
跨 ERP 版本步骤污染                      ← M18 的 cross_space 题集
Agent 丢 images / 错图显示
普通用户可访问 Admin API
普通用户可发布 Verified Answer
Verified Answer 跨版本生效
Verified Answer 用私有资料作公共来源
用户删除私有文档后图片仍可访问
用户禁用后旧 JWT 还能调用
纠错图片可通过公开 URL 绕过权限
Markdown 可注入危险 HTML / script
Judge 网络错误被算模型答错              ← UNRELIABLE 三态已实现
```

---

## 记账项

老问题，各自独立，随时可捡：

- 引用角标偶发裸露；
- 配图编号跳号；
- 方案题挂 21 条无关来源（`08631f8` 已修一部分，要复核）；
- **两处语料笔误待你定夺**（只有你能定）：自动审核失败重试是 **48 次还是 10 次**；
  抖音共享面单修复的**「1327 版本」是不是 1.3.2.7**。定了之后走勘误层
  `copilot correct` 改一次、重灌一次。

---

## 每阶段执行规则

1. 先用真实代码和现有测试写 implementation delta；
2. **先补失败测试，再改实现**；不得用局部评测替代全量门禁；
3. 跑相关测试 + 全量后端测试 + ruff，涉及前端时跑 lint / build；
4. 记录 migration upgrade/downgrade、回填校验、备份/回滚和线上证据；
5. 每个逻辑阶段独立 commit，并同步这份文件的 NOW；**未通过不得标完成**。

```bash
# ── 本机自检（和 CI、deploy.sh 第 1 步同一批命令）──
# ⚠️ `../tests ../eval` 不能省（ADR-18）：这两个目录在仓库根，
#    `ruff check .` 一行扫不到，而漏掉它们的表现是**这一步照样绿**
cd backend && .venv/Scripts/python.exe -m ruff check . ../tests ../eval            && .venv/Scripts/python.exe -m pytest -q      # Linux 换 .venv/bin/python
cd ../frontend && npm run verify   # = 单测 + lint + 类型 + 构建，和 CI / deploy.sh 同一条

# ── 评测（只在本机跑）──
cd backend
uv run python ../eval/cross_space.py --check     # 免费，只验检索
uv run python ../eval/gate.py                    # 退出码必须 0
```

⚠️ 本机 `backend/` 下**别裸跑 `uv sync`**（会卸掉 parse / agent / eval 三个 extra，
下次 deploy 自检当场红）。
⚠️ 服务器上只能 `.venv/bin/copilot`。
⚠️ 单跑一个测试文件要带 `-c`，否则 rootdir 变成 `Copilot/`，读不到 `asyncio_mode=auto`，
async 测试全红。

---

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
| 部署 | 阿里云 ECS → `https://liushun666.cn/` |

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

Next.js 16 + AI SDK 7 完整保留，`useChat` 照常工作——只是 `output: 'export'`，服务器上不跑 Node 进程。这同时省下 100–200MB。

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
| 前端 | **Next.js 16（静态导出）+ Vercel AI SDK 7** | 2026 AI 聊天界面事实标准。`useChat` 直接搬走流式/工具调用/中断/重试。`output: 'export'` 本机构建，服务器不跑 Node |
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

| 阶段 | 里程碑 | 状态 |
|---|---|---|
| 地基期 | M0 地基 / M1 检索内核 / M2 语雀入库 | ✅ |
| 上线期 | M3 认证+聊天 API / M4 Next.js 前端 / **M5 上线** ⭐ | ✅ |
| 增强期 | M6 上传+私有库 / M7 Agent 化 / M8 评测+优化 | ✅ |
| 运维期 | M9 删除+勘误+图片 | ✅ |
| 收敛期 | M10 全 Agent 化 / M11 可运维化 / M12 常识兜底 / M13 可靠性硬化 | ✅ |
| 企业化 | M14-A 空间与隔离 / M14-B ImageAsset / M15-A 只读管理台 | ✅ 2026-08-23 上线 |
| | M16 答案纠错与标准答案 / M17 嵌图解析 | ✅ 2026-08-23 上线 |
| | M19-A 评测契约与跨空间门禁 | ✅ 2026-08-24，门禁 7/7 |
| | **M18 企业版首次导入** | ⬜ Week 3 |
| | M19-B 评测中心 / M20 路由收敛 | ⬜ 推迟 |

每个里程碑的详细任务、验收和排查记录在 [ARCHIVE.md](ARCHIVE.md)。

---

## 五、详细任务台账

已移到 [ARCHIVE.md](ARCHIVE.md)：M0–M13 的逐项任务、验收命令、每一次排查和 A/B 的证据，
以及 M14–M20 的全部执行记录。**还没做的事在这份文件上半部分。**

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
| R7 | **DeepSeek 余额耗尽** | 全站问答直接 402，且报错看不懂 | **高**（2026-08-18 只剩 ¥2.13） | 及时充值；Kimi 已接成可切换 provider（改 `.env` 两行），但切之前要用评测集重跑一轮 |
| R8 | ~~**数据库零备份**~~ | 3 个真实账号、私有文档、会话历史、邀请码全不可再生 | 中 | ✅ **2026-08-20 处理完**（M11 P0）。每天 pg_dump + uploads，留 14 份，本机来拉做异地；**恢复演练在生产备份上真跑通**（3 用户 / 750 文档 / 4572 块 / 向量检索）。备份失败会写 FAILED，`deploy.sh` 每次上线检查 LAST_OK 的年龄，超 48 小时判部署不通过 |

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
uv run copilot prune-junk         # 索引体检：有二进制垃圾块就报出来（--apply 才删）
uv run pytest && uv run ruff check

# ── 勘误层（M9）─────────────────────
uv run copilot correct 京东面单          # 搜文档 → 编辑器改 → 存成勘误文件
uv run copilot correct 某文档 --retire   # 整篇作废
uv run copilot corrections               # 列出所有勘误，过期的标黄
uv run copilot corrections --check       # 有过期就退出码非 0（deploy.sh 用）
uv run copilot ingest                    # 勘误在这一步生效
# ⚠️ 只 commit 不部署 = 本机对了、线上还是错的

# ── 评测（M8/M9，只在本机跑）───────────
uv sync --extra parse --extra eval    # ⚠️ uv sync 是声明式的，两组要一起列，
                                       #    否则后一次会把前一组卸掉
uv run python ../eval/run.py --check           # 只验检索，不花钱
uv run python ../eval/run.py --tag now
uv run python ../eval/run.py --compare v3-final now
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
