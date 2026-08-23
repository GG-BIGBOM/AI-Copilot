# 运维

服务器：阿里云 ECS `8.136.116.9`，Ubuntu，**2 核 / 1.6GB / 40G**。
站点：<https://liushun666.cn/>

两条红线，破了会直接把站弄挂：

> **服务器上永不执行 `npm run build`**（峰值 1GB+，必 OOM）
> **服务器上永不加载 ML 模型**（embedding / rerank / 生成全在云端 API）

---

## 一、部署

```bash
bash deploy/deploy.sh
```

七步：

```
[1/7] 本机自检          pytest + ruff，不过就别推上去
[2/7] 本机构建前端      npm run build → out/
[3/7] 推送后端代码
[4/7] 推送勘误层        ⚠️ rm -rf 再解包，所以网页写的勘误绝不能落在这个目录
[5/7] 同步 systemd 单元 + 运维脚本
[6/7] 推送前端产物
[7/7] 装依赖、跑迁移、重启
```

之后自动跑：**公网验收**（走 nginx，不是 localhost）+ **备份体检**。

### 上线前 `.env` 要改两个值

```
COOKIE_SECURE=true     HTTPS 下 cookie 才存得住，同时关掉 /api/docs
CORS_ORIGINS=          线上前后端同源，留空即可
```

### 装依赖的坑

⚠️ **`backend/` 下别裸跑 `uv sync`**——它会卸掉 `parse` / `agent` / `eval`
这几个 extra，部署自检当场变红。要用 `uv sync --all-extras`。

⚠️ **服务器上跑 CLI 只能用 `.venv/bin/copilot`**，`uv run` 会卸掉 extra。

---

## 二、systemd 单元

| 单元 | 干什么 | 内存 |
|---|---|---|
| `copilot-api.service` | FastAPI（uvicorn 单 worker） | `MemoryMax=600M` |
| `copilot-worker.service` | 解析上传的文档 | `MemoryMax=400M` |
| `copilot-sync.timer` | 每天同步语雀 | UTC 04:10 |
| `copilot-backup.timer` | 每天备份 | UTC 20:10（北京次日 04:10） |
| `copilot-prune.timer` | 每天清过期台账 | UTC 21:10 |

**timer 之间的顺序是刻意的**：备份（20:10）→ 清理（21:10）。
**先备份，再删。** 反过来的话，某天的清理出了错，当天的备份里已经没有那些行了——
而台账不可再生。

`enable` 时只 enable timer，**不要 enable 那几个 oneshot service**：
它们由各自的 timer 触发，单元里故意没写 `[Install]`，enable 会直接报错。

worker 收到 SIGTERM 会**跑完手上那条任务**再退出。硬杀的话，
那条 running 会留在库里，得等 30 分钟的 stale 回收才恢复——
对用户就是「解析中」卡了半小时。

```bash
systemctl status copilot-api copilot-worker
systemctl list-timers 'copilot-*'
systemctl restart copilot-api          # 走优雅停止
```

---

## 三、备份与恢复

```bash
# 手工来一次
systemctl start copilot-backup.service

# 本机异地拉一份
bash deploy/backup-pull.sh
```

每天 `pg_dump` + `uploads` + `private-images` 打包，留 14 份，
落在 `/var/backups/copilot/`。

⚠️ `data/private-images/`（M17：上传文档里解出来的截图）**跟 uploads 一档**，
不跟语雀镜像那一档：它看起来是图片，但**不可再生**——原件在用户自己的
电脑上，丢了只能让每个人重新传一遍。语雀那 1.1G 镜像相反，
一条 `copilot sync-yuque` 就能重下。

### ⭐ 告警渠道只有一个：`deploy.sh` 的「备份体检」

服务器上**没有邮件、没有 Sentry**。备份任务悄悄失败三周也不会有任何外部症状——
站好好的、答案照常出、你每天还在往本机拉「备份」。

所以检查挂在**每次上线都会看的那段输出**里：`LAST_OK` 超过 **48 小时**
就把这次部署判为**不通过**（exit 1）。

> 为什么是 48 小时不是 24：timer 有最多 5 分钟随机延迟，而上线常常发生在
> 刚过整点的时候。卡 24 小时会周期性地误报，而**误报几次之后这条检查就没人看了**。

### 恢复演练：别等真出事那天才第一次跑

```bash
# 服务器上（peer 认证，必须 sudo -u postgres）
sudo -u postgres env COPILOT_DRILL_PSQL=postgresql:///postgres \
    bash /opt/copilot/deploy/restore-drill.sh /var/backups/copilot/kb-YYYYmmdd-HHMMSS.dump

# 本机（对着 backup-pull.sh 拉下来的那份）
./deploy/restore-drill.sh /d/backups/copilot/kb-20260820-041000.dump
```

⭐ **「备份文件存在」和「能恢复」是两件事。** 中间隔着四样：dump 有没有写完整、
pgvector 扩展在不在、向量列维度对不对、恢复出来的数据能不能被检索到。
任何一样不对，你都会在真正需要恢复的那天才发现——**那天来不及查**。

所以脚本最后一步不是「pg_restore 退出码为 0」，是**在恢复出来的库上跑一次
真实的向量检索并拿到结果**。

2026-08-20 在**生产备份**上真跑通过一次（3 用户 / 750 文档 / 4572 块 / 向量检索）。

---

## 四、日志与追踪

```bash
journalctl -u copilot-api -f
journalctl -u copilot-worker -n 200
journalctl -u copilot-api | grep <request-id>     # 用户截图报错时凭这个捞堆栈
```

每个请求有一个 `X-Request-Id`，同时写进 `request_trace.request_id`——
**两边各存一半信息，靠这个字段缝合**。

### 从一条差评复现当时的链路

```bash
curl -s localhost:8000/api/feedback/recent | jq
```

一条差评能直接看到：走的哪条路、调了什么工具、召回几块、rerank 最高分、
有没有私有块、TTFB、总时长、`answer_source`、以及 `requestId`。

⭐ 这就是那个闭环的入口：**差评 → 看是检索没召回还是模型没答好 → 补一道评测题**。
没有这一步，👎 就真的只是个计数器。

---

## 五、限流

进程内令牌桶（`api/ratelimit.py`），不引 Redis——只有一个 uvicorn worker，
**进程内就是全局**。

另有每人每日 token 配额（`users.daily_token_quota`，0 = 不限）。
它不是计费系统，是**保险丝**：挡的是循环发问的脚本、重放的前端 bug、
和把整本手册贴进来的善意用户。

---

## 六、质量报告

```bash
.venv/bin/copilot quality-report                  # 最近 7 天
.venv/bin/copilot quality-report --days 30
.venv/bin/copilot quality-report --route agent    # ⭐ 灰度观察
.venv/bin/copilot quality-report --user a@b.c     # 某个人说慢
```

输出：提问数 / 活跃用户 / **答案来源五分类** / 👍👎 与差评率 /
Agent 轮次与 tools 为空 / 越过工具直答 / 出错 / TTFB p50·p95 /
总时长 p50·p95 / token 合计与均值。

几处口径**是刻意的**（都有测试钉着，改之前先看 `tests/test_quality_report.py`）：

- 差评率的分母是**被评价过的轮次**，不是全部请求
- **延迟不含寒暄**（它首字是毫秒级的，混进来 p50 就看不出问题了）
- `answer_source` 为 NULL 的老行**单独列**，不并进任何一类
- 百分位**最近邻、不插值**（两位数样本上插值出的数是从未发生过的事件）
- **没有可靠价格配置就不印成本**

---

## 七、数据保留

```
普通 request_trace     30 天
带 👎 的               90 天    ← 它是评测集的原料，闭环有时跨好几周
出错的（ok=false）     90 天    ← 事故复盘常常发生在事发很久以后
聚合统计               长期
聊天记录               用户删会话时**当场**删干净（另一条链路）
```

```bash
.venv/bin/copilot prune-traces              # 预演（默认）
.venv/bin/copilot prune-traces --apply      # 真删
```

`copilot-prune.timer` 每天 UTC 21:10 自动跑（带 `--apply`）。

⚠️ **`--apply` 和 `--dry-run` 同时给会直接报错**，不替你猜。
猜「听保守的那个」的代价是：timer 里一条写错的命令会安安静静地每天什么都不做，
而你以为它在清理——半年后磁盘满了才发现。

### 删除链路（哪些跟着删、哪些刻意留下）

```
删一段会话
  messages         ON DELETE CASCADE      跟着删
  exports/*.xlsx   路由里手动 unlink      跟着删
  request_trace    **没有外键，不删**      刻意留下 ← 见下
  feedback         在 trace 那一行上       跟着留下

删一个用户
  documents / chunks / conversations       CASCADE，跟着删
  request_trace.user_id                    SET NULL，行留着
  invite_codes.used_by                     SET NULL，但 **used_at 不清**，
                                           所以那个码**不会复活**
```

**为什么 trace 不跟着删**：它记的是「系统那天表现如何」，不是「他说过什么」。
而用户删掉那段会话的动机，很多时候恰恰是「这轮答得不好」——
跟着删的话，最该留下的样本会被最想让你看到它的那个动作抹掉。
被留下的那一行里**没有答案正文**，问题原文截到 2000 字，30/90 天后清掉。

---

## 八、事故检查表

### 站打不开

```bash
systemctl status copilot-api nginx
free -h                              # 剩余内存 > 200MB 是健康线
journalctl -u copilot-api -n 100
```

内存不够被 OOM killer 干掉是最常见的一种。swap 有 2G 兜着，
但如果 `free -h` 显示 swap 也在用，说明有东西在漏。

### 所有上传都卡在「解析中」

```bash
systemctl status copilot-worker
journalctl -u copilot-worker -n 100
```

worker 挂了或者被一份文件拖住。`zipguard` + `PARSER_TIMEOUT` 之后
后者应该很少见了；真发生就 `systemctl restart copilot-worker`
（stale 回收会把那条任务放回队列）。

### 答案突然全是「知识库暂无此内容」

按可能性排序：

1. **LLM 余额耗尽**（DeepSeek 402）。`journalctl` 里会有
2. **SiliconFlow 限流**，embedding / rerank 打不通 → 一条都召回不了
3. 语雀同步把库洗坏了 → `copilot ask "..." --show-chunks` 看召回

### 答案开始编界面路径

**这是最严重的一类。** 立刻：

```bash
cd backend && uv run python ../eval/risk_boundary.py --tag incident
```

三条硬指标破了就回滚。`ALLOW_GENERAL_KNOWLEDGE=false` 一行退回 M11 的严格版
（改 `.env` 重启，不用发版）。

### 怀疑跨用户泄漏

**最严重，信任归零。** `tests/test_isolation.py` 是第一道；
线上验证是换账号搜同一份文档。过滤条件只有一处实现（`retrieve.search`），
先看那里有没有被改过。

### 判分器/评测出奇怪的数

先看报告顶上的 `判分失效` 和 `判分失效率`。
超过 5% 会打 `【UNRELIABLE】`，**那一轮的数字不作数**，重跑。
（见 [EVALUATION.md](EVALUATION.md)。）

---

## 九、端到端验收（上线后手工过一遍）

```
1. 邀请码注册 → 登录
2. 提问 → 流式输出 + 引用可点击跳语雀原文
3. 上传 md/docx → pending → done（systemctl status copilot-worker 看进展）
4. 提问命中新文档
5. 换账号 → 搜不到那份文档              ← 隔离的线上验证
6. 「帮我做个实施方案」→ Agent 追问 → 下载 xlsx
7. ssh 上去 free -h，剩余内存 > 200MB     ← 1.6GB 的健康线
8. .venv/bin/copilot quality-report      ← 看真实的 TTFB p95
```

多轮行为另有一份手工清单：[eval/manual_conversations.md](eval/manual_conversations.md)。
