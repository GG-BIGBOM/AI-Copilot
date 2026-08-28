#!/bin/sh
# compose 的一次性初始化（W1.3）：迁移 → 灌样例语料 → 回填词法索引 → 建演示账号。
#
# ⚠️ `set -e`：任何一步失败就整个退非零，compose 的
# `condition: service_completed_successfully` 会因此挡住 api 和 worker 起来。
# 这是刻意的——一个语料没灌进去的站点，打开只会看到「知识库暂无此内容」，
# 而看的人会以为是这个项目不行，不会想到是初始化半路挂了。
set -e

cd /app/backend

echo "── 1/4 数据库迁移 ──"
alembic upgrade head

echo "── 2/4 灌样例语料（samples/，20 篇）──"
# ⚠️ **这一步要花 embedding 额度**（约 20 篇 × 几块，很少，但不是零）。
# `ingest` 自己按 content_hash 判重，重跑一次不会重复花钱。
#
# ⚠️ 没配 SILICONFLOW_API_KEY 的话它会在这里失败——**这是好事**：
# 失败信息里写着缺什么，比"起来了但一问三不知"好排查得多
copilot ingest /app/samples

echo "── 3/4 回填词法索引（W1.2 的 content_tsv）──"
# 幂等：只补还是 NULL 的块。上一步 ingest 其实已经顺手写过了，
# 这一行是为了让"先有库、后加列"的老库也能对齐
copilot backfill-tsv

echo "── 4/4 建演示账号 ──"
# ⚠️ 这条命令要 COPILOT_ALLOW_SEED_USER=1 才肯跑，compose 里设了，
# 服务器上根本没有这个变量（见 cli.py 的 seed_user）
copilot seed-user --email "${DEMO_EMAIL:-demo@example.com}" \
                  --password "${DEMO_PASSWORD:-demo12345}" --admin

echo ""
echo "样例语料入库完成。"
echo "  打开   http://localhost:3000"
echo "  登录   ${DEMO_EMAIL:-demo@example.com} / ${DEMO_PASSWORD:-demo12345}"
echo "  试问   SAMPLE-POSTB 对应哪家快递？"
