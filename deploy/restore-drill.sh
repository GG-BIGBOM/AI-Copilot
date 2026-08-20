#!/usr/bin/env bash
# 恢复演练：拿一份备份，在**别处**起一个库，然后真跑一次检索。
#
# 两种跑法，**服务器上那条要 sudo -u postgres**（那边是 peer 认证，
# root 直接 psql 会被要求输密码，而这个脚本是非交互的）：
#
#   # 在服务器上（2026-08-20 首次演练就是这么跑的，通过）
#   sudo -u postgres env COPILOT_DRILL_PSQL=postgresql:///postgres \
#       bash /opt/copilot/deploy/restore-drill.sh /var/backups/copilot/kb-YYYYmmdd-HHMMSS.dump
#
#   # 在本机（对着 backup-pull.sh 拉下来的那份）
#   ./deploy/restore-drill.sh /d/backups/copilot/kb-20260820-041000.dump
#
#   加 --keep：演练完不删这个库，留着自己翻
#
# ⭐ **「备份文件存在」和「能恢复」是两件事。** 中间隔着：dump 有没有写完整、
# pgvector 扩展在不在、向量列的维度对不对、恢复出来的数据能不能被检索到。
# 这四样里任何一样不对，你都会在真正需要恢复的那天才发现 —— 那天来不及查。
#
# 所以这个脚本的最后一步不是「pg_restore 退出码为 0」，是
# **在恢复出来的库上跑一次真实的向量检索并拿到结果**。
# 前者只证明文件能解开，后者才证明这份备份真的救得回这个系统。
set -euo pipefail

DUMP=${1:-}
[ -n "$DUMP" ] && [ -f "$DUMP" ] || {
    echo "用法：$0 <备份文件.dump> [--keep]"
    echo
    echo "本机现有的备份："
    ls -1t "${COPILOT_BACKUP_LOCAL:-/d/backups/copilot}" 2>/dev/null | head -10 | sed 's/^/  /'
    exit 1
}
KEEP=${2:-}

# 演练库的名字带时间戳，且**永远不叫 kb**。
# 手滑把演练恢复到生产库上，是这个脚本唯一有可能造成的破坏，
# 所以名字里不给这种手滑留位置。
DRILL_DB="kb_drill_$(date +%s)"
PSQL_URL=${COPILOT_DRILL_PSQL:-postgresql://postgres@localhost:5432/postgres}

echo "==> [1/5] 检查这份 dump 本身"
pg_restore -l "$DUMP" > /tmp/drill-toc.txt
echo "    目录项 $(wc -l < /tmp/drill-toc.txt) 条，$(du -h "$DUMP" | cut -f1)"
grep -q "TABLE DATA public chunks" /tmp/drill-toc.txt \
    || { echo "    ⚠️ 目录里没有 chunks 的数据段 —— 这份备份是空的或者只有结构"; exit 1; }

echo "==> [2/5] 建一个空库 $DRILL_DB"
psql "$PSQL_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DRILL_DB" > /dev/null
DRILL_URL="${PSQL_URL%/*}/$DRILL_DB"
cleanup() {
    if [ "$KEEP" = "--keep" ]; then
        echo "    演练库留着了：$DRILL_DB（自己 DROP DATABASE $DRILL_DB）"
    else
        psql "$PSQL_URL" -c "DROP DATABASE IF EXISTS $DRILL_DB" > /dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# pgvector 必须先装。**不装的话 pg_restore 会报一堆「type vector does not exist」，
# 然后仍然以 0 退出**（它把每张表当成独立的错误跳过去了）——
# 于是你得到一个「恢复成功」的空库。这就是为什么第 5 步必须真查一次数据。
psql "$DRILL_URL" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector" > /dev/null

echo "==> [3/5] 恢复"
# --no-owner：演练库的属主多半和生产不同（本机是自己，服务器上是 copilot），
# 不加这个会为每张表报一次「role does not exist」
pg_restore --no-owner --no-privileges -d "$DRILL_URL" "$DUMP" 2>&1 \
    | grep -v "^pg_restore: 处理" | tail -5 || true

echo "==> [4/5] 数一遍"
psql "$DRILL_URL" -tA -F' ' -c "
  SELECT 'users      ' || count(*) FROM users
  UNION ALL SELECT 'documents  ' || count(*) FROM documents
  UNION ALL SELECT 'chunks     ' || count(*) FROM chunks
  UNION ALL SELECT '  其中私有 ' || count(*) FROM chunks WHERE owner_id IS NOT NULL
  UNION ALL SELECT 'convs      ' || count(*) FROM conversations
  UNION ALL SELECT 'messages   ' || count(*) FROM messages
  UNION ALL SELECT 'invites    ' || count(*) FROM invite_codes
" | sed 's/^/    /'

CHUNKS=$(psql "$DRILL_URL" -tAc "SELECT count(*) FROM chunks")
[ "$CHUNKS" -gt 0 ] || { echo "    ⚠️ chunks 是空的，这份备份救不回任何东西"; exit 1; }

echo "==> [5/5] 在恢复出来的库上真跑一次检索"
# ⭐ 这一步才是演练的意义。拿库里第一条向量当查询向量，看能不能按余弦距离
# 召回一批块 —— 它同时验了三件事：向量列的维度对、pgvector 索引可用、
# 数据真的进来了。上面四步全过、这一步失败的情形是存在的（维度不匹配）。
psql "$DRILL_URL" -tA -v ON_ERROR_STOP=1 -c "
  WITH q AS (SELECT embedding FROM chunks WHERE owner_id IS NULL LIMIT 1)
  SELECT left(c.title, 40) || '  距离=' || round((c.embedding <=> q.embedding)::numeric, 4)
  FROM chunks c, q
  ORDER BY c.embedding <=> q.embedding
  LIMIT 3
" | sed 's/^/    /'

echo
echo "✅ 演练通过：这份备份能恢复成一个可检索的库。"
echo "   $(basename "$DUMP")"
