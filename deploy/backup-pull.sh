#!/usr/bin/env bash
# 把服务器上最新的备份拉到**本机**。这就是「异地」那一半。
#
#   ./deploy/backup-pull.sh              # 拉最新一份数据库 + uploads
#   ./deploy/backup-pull.sh --all        # 把服务器上现有的每日备份全拉一遍
#
# ⭐ **方向是「本机来拉」，不是「服务器往外推」。** 两个理由：
#   1. 服务器往外推要在服务器上放一把能写别处的密钥。那台机器公网可注册，
#      一旦被拿下，攻击者连备份一起删 —— 备份和生产同归于尽是最糟的形态。
#   2. 拉取这件事本来就该由「还活着的那一端」发起。
#
# 目标目录默认 D 盘（见 plan.md 一·五：所有软件和数据装 D）。
set -euo pipefail

# ⚠️ **服务器地址不写在仓库里**（这个仓库是公开的）。两种给法，环境变量优先：
#     export COPILOT_HOST=root@1.2.3.4
#     cp deploy/.env.example deploy/.env && 填进去      ← deploy/.env 已在 .gitignore
# 只有 HOST 是必填。密钥文件名不是秘密，保留默认值，换机器时再覆盖。
if [ -z "${COPILOT_HOST:-}" ] && [ -f "$(dirname "$0")/.env" ]; then
    . "$(dirname "$0")/.env"
fi
HOST=${COPILOT_HOST:?没有服务器地址。cp deploy/.env.example deploy/.env 后填 COPILOT_HOST}
KEY=${COPILOT_SSH_KEY:-$HOME/.ssh/erp_vps}
REMOTE_DIR=${COPILOT_BACKUP_DIR:-/var/backups/copilot}
LOCAL_DIR=${COPILOT_BACKUP_LOCAL:-/d/backups/copilot}

SSH="ssh -i $KEY -o BatchMode=yes $HOST"
mkdir -p "$LOCAL_DIR"

echo "==> 服务器上的备份"
$SSH "ls -1t $REMOTE_DIR/kb-*.dump 2>/dev/null | head -20" > /tmp/copilot-dumps.txt || true
[ -s /tmp/copilot-dumps.txt ] || {
    echo "服务器上一份数据库备份都没有。先去看 systemctl status copilot-backup.timer"
    exit 1
}

# ⚠️ 先看健康状态再拉。**「拉到了文件」不等于「备份是好的」**：
# 备份任务三周前就挂了，而 14 份陈旧的 dump 还老老实实躺在那里，
# 你每天拉一份下来，心里很踏实 —— 这正是 R8 最难查的那种形态。
LAST_OK=$($SSH "cat $REMOTE_DIR/LAST_OK 2>/dev/null" || echo "")
if [ -z "$LAST_OK" ]; then
    echo "  ⚠️ 服务器上没有 LAST_OK，说明备份**从来没成功过**"
else
    echo "  上次成功：$LAST_OK"
fi
if $SSH "test -f $REMOTE_DIR/FAILED"; then
    echo "  ⚠️ 服务器上有 FAILED 标记，最近一次备份是失败的："
    $SSH "cat $REMOTE_DIR/FAILED" | sed 's/^/     /'
fi

if [ "${1:-}" = "--all" ]; then
    FILES=$(cat /tmp/copilot-dumps.txt)
else
    FILES=$(head -1 /tmp/copilot-dumps.txt)
fi

echo "==> 拉数据库备份"
for remote in $FILES; do
    name=$(basename "$remote")
    if [ -f "$LOCAL_DIR/$name" ]; then
        echo "  $name  已经有了，跳过"
        continue
    fi
    # cat over ssh：Git Bash 默认没有 rsync，也没有 scp 的通配符展开。
    # 落 .part 再改名，中断了不会留下一个看起来完整的半截文件
    $SSH "cat $remote" > "$LOCAL_DIR/$name.part"
    mv "$LOCAL_DIR/$name.part" "$LOCAL_DIR/$name"
    echo "  $name  $(du -h "$LOCAL_DIR/$name" | cut -f1)"
done

echo "==> 拉 uploads（用户上传的原件，丢了就没了）"
UP=$($SSH "ls -1t $REMOTE_DIR/uploads-*.tar.gz 2>/dev/null | head -1" || echo "")
if [ -n "$UP" ]; then
    name=$(basename "$UP")
    if [ -f "$LOCAL_DIR/$name" ]; then
        echo "  $name  已经有了，跳过"
    else
        $SSH "cat $UP" > "$LOCAL_DIR/$name.part"
        mv "$LOCAL_DIR/$name.part" "$LOCAL_DIR/$name"
        echo "  $name  $(du -h "$LOCAL_DIR/$name" | cut -f1)"
    fi
else
    echo "  服务器上还没有 uploads 备份"
fi

echo
echo "本机现有："
ls -1t "$LOCAL_DIR" | head -10 | sed 's/^/  /'
echo
echo "⭐ 每月手工验一次最新那份能不能恢复（不验的备份不算备份）："
echo "   ./deploy/restore-drill.sh $LOCAL_DIR/$(basename "$(head -1 /tmp/copilot-dumps.txt)")"
