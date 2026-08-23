#!/usr/bin/env bash
# 每天备份一次。**在服务器上跑**，由 copilot-backup.timer 触发。
#
#   bash /opt/copilot/deploy/backup.sh          # 正常跑（timer 就是这么调的）
#   bash /opt/copilot/deploy/backup.sh --now    # 手工补一次，行为完全一样
#
# ⭐ **备份方案的第一要求是你不会想关掉它。**
# 所以这里分了三档，不是懒，是刻意的：
#
#     Postgres      每天，14 份，**要异地**   —— 3 个账号、私有文档、会话、邀请码，全不可再生
#     data/uploads         每天，14 份，**要异地**  —— 用户上传的原件，丢了就没了
#     data/private-images  每天，跟着 uploads 走    —— 上传文档里解出来的截图（M17）
#     data/images          每周，本地快照 2 份      —— 1.1G，能从语雀重下（一次 11 分钟）
#
# ⚠️ **private-images 跟 uploads 一档，不跟 images 一档。** 它看起来是"图片"，
# 但它和语雀镜像的本质区别是**不可再生**：原件在用户自己的电脑上，
# 丢了就只能让每个人重新上传一遍（而他们多半已经不记得传过什么了）。
# 量也完全不同——它是几十 MB 级，不是 1.1G。
#
# 把 1.1G 的图片塞进每日异地传输的下场是：两周后你自己把这个任务关掉，
# 那时候连 PG 的备份也一起停了。**分档是为了让最重要的那一档活下来。**
#
# 「异地」这一步不在这个脚本里 —— 见 deploy/backup-pull.sh，那半程在本机跑。
# 服务器主动往外推需要在服务器上放一把能写别处的密钥；本机来拉不需要，
# 一台机器被拿下不等于备份跟着被删。
set -euo pipefail

BACKUP_DIR=${COPILOT_BACKUP_DIR:-/var/backups/copilot}
APP_DIR=${COPILOT_ROOT:-/opt/copilot}
KEEP_DAYS=${COPILOT_BACKUP_KEEP:-14}
KEEP_IMAGE_SNAPSHOTS=2

DB_NAME=${COPILOT_DB_NAME:-kb}
DB_USER=${COPILOT_DB_USER:-kb}

STAMP=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

# ⭐ 失败必须留下痕迹，而且是**下次一定会被看见**的地方。
# 这台机器上没有邮件、没有 Sentry，唯一每天会被读的是 journald，
# 唯一每次上线都会被读的是 deploy.sh 的输出。所以：
#   出错 → journal 里一行 ERROR + 写 FAILED 文件
#   成功 → 覆盖 LAST_OK
# deploy.sh 最后会看 LAST_OK 的年龄，超过 48 小时就把部署判为不通过。
# 「以为有备份、其实没有」是这一节唯一一个会让前面所有工作归零的失败模式。
fail() {
    echo "备份失败：$*" >&2
    printf '%s\n%s\n' "$(date -u +%FT%TZ)" "$*" > "$BACKUP_DIR/FAILED"
    exit 1
}
trap 'fail "第 $LINENO 行非零退出"' ERR

echo "==> [1/4] Postgres"
DUMP="$BACKUP_DIR/kb-$STAMP.dump"
# -Fc 自定义格式：比 SQL 文本小一半，且 pg_restore 能选择性恢复单表。
# ⚠️ **不加 --clean / --create**：恢复演练要往一个**另建的空库**里灌
# （见 restore-drill.sh），带 --create 会让它去建同名库，演练就变成了
# 「在生产库上试一试」——那正是备份最不该做的事。
#
# ⚠️⚠️ **走标准输出重定向，不用 `pg_dump -f`。**
# `-f` 是**postgres 这个用户**去开那个文件，而 /var/backups/copilot 是 root 的，
# 它没有写权限——第一次在服务器上真跑就撞了这个（Permission denied）。
# 重定向则是 root 这一侧开的文件，pg_dump 只管往 stdout 吐。
# 顺带也省掉了「给 postgres 用户开一个可写目录」这种为了绕权限而放宽权限的做法。
sudo -u postgres pg_dump -Fc -d "$DB_NAME" > "$DUMP.part"
mv "$DUMP.part" "$DUMP"
# 立刻验一次能不能读。pg_dump 退出码为 0 但文件截断过（磁盘满），
# 而截断的 dump 只有在真要恢复的那天才会暴露出来
pg_restore -l "$DUMP" > /dev/null || fail "刚生成的 dump 读不出目录，八成是写坏了"
echo "    $(basename "$DUMP")  $(du -h "$DUMP" | cut -f1)"

echo "==> [2/4] data/uploads + data/private-images（不可再生的那两个目录）"
UP="$BACKUP_DIR/uploads-$STAMP.tar.gz"
# 两个目录打进**同一个包**：保留策略、异地拉取、恢复演练都只认
# `uploads-*.tar.gz` 这一个名字，多一种文件名就多一处要跟着改的地方，
# 而漏改的表现是「以为在备份，其实没有」
DIRS=""
for d in uploads private-images; do
    [ -d "$APP_DIR/data/$d" ] && DIRS="$DIRS $d"
done
if [ -n "$DIRS" ]; then
    # shellcheck disable=SC2086  # 目录名是上面这个白名单里的，没有空格
    tar -czf "$UP.part" -C "$APP_DIR/data" $DIRS
    mv "$UP.part" "$UP"
    echo "    $(basename "$UP") （$DIRS ） $(du -h "$UP" | cut -f1)"
else
    echo "    这两个目录都还没有，跳过"
fi

echo "==> [3/4] data/images（每周一次，只留本地）"
# 星期一才做。`date +%u` 是 1..7，周一 = 1。
# 判在脚本里而不是再加一个 timer：两个 timer 就有两处日程要对，
# 而这件事的复杂度不值得第二个单元文件。
IMG_DIR="$BACKUP_DIR/images"
if [ "$(date -u +%u)" = "1" ] && [ -d "$APP_DIR/data/images" ]; then
    mkdir -p "$IMG_DIR"
    SNAP="$IMG_DIR/images-$STAMP.tar"
    # 不压缩：里面全是 png/jpg，gzip 一遍省不下 2%，却要多烧几分钟 CPU，
    # 而这台机器只有 1 核
    tar -cf "$SNAP.part" -C "$APP_DIR/data" images
    mv "$SNAP.part" "$SNAP"
    echo "    $(basename "$SNAP")  $(du -h "$SNAP" | cut -f1)"
    ls -1t "$IMG_DIR"/images-*.tar 2>/dev/null | tail -n +$((KEEP_IMAGE_SNAPSHOTS + 1)) \
        | xargs -r rm -f
else
    echo "    今天不是周一（或没有 images 目录），跳过"
fi

echo "==> [4/4] 清理与记账"
# ⚠️ 用 `ls -t | tail -n +N` 按**份数**留，不用 `find -mtime` 按**天数**删。
# 按天数删有一个很坏的失败模式：备份任务停了三周之后你来看，
# 发现连最后一份成功的备份也被清理掉了 —— 清理任务可从来没停过。
# 按份数留的话，不管停多久，最近 14 份永远都在。
for pat in "kb-*.dump" "uploads-*.tar.gz"; do
    # shellcheck disable=SC2012  # 文件名是自己生成的时间戳，没有空格和换行
    ls -1t "$BACKUP_DIR"/$pat 2>/dev/null | tail -n +$((KEEP_DAYS + 1)) | xargs -r rm -f
done
rm -f "$BACKUP_DIR/FAILED"
date -u +%FT%TZ > "$BACKUP_DIR/LAST_OK"

df -h "$BACKUP_DIR" | tail -1 | awk '{print "    备份盘剩余 " $4 " / " $2}'
echo "    现有 $(ls -1 "$BACKUP_DIR"/kb-*.dump 2>/dev/null | wc -l) 份数据库备份"
echo "完成 $STAMP"
