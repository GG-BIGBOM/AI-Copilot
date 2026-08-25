#!/usr/bin/env bash
# 服务器加固。在**服务器上**以 root 执行，幂等，重复跑不会破坏已有状态。
#
#   scp deploy/harden.sh root@<服务器>:/tmp/ && ssh root@<服务器> bash /tmp/harden.sh
#
# 2026-08-25 加。审出来的实际状态是：ufw 已经在拦、只开 22/80/443，
# 自动安全更新也开着——但 **SSH 的密码登录一直是开的，root 也能用密码登**，
# 而 root 有密码（`passwd -S root` = P）。公网 22 + root + 密码，
# 这三样凑齐就只剩「密码够不够长」这一道防线了。fail2ban 也没装，
# 意味着爆破可以不限次数地试。
#
# ⭐ **改的是 drop-in，不是 sshd_config 本身。** 回滚 = 删一个文件再 reload，
#    不用去原文件里找哪几行是自己加的。
#
# ⚠️ **不锁 root 密码。** 密码登录关掉之后它对公网已经没用了，但它是
#    阿里云控制台 VNC 唯一的登录方式——万一 SSH 真的被自己关死，
#    那是唯一一条回得去的路。锁掉它等于把备用钥匙一起扔了。
set -euo pipefail

DROPIN=/etc/ssh/sshd_config.d/99-hardening.conf

echo "==> [1/4] 先确认有公钥可用（没有就不能关密码登录）"
# 关掉密码登录之前必须证明「还有别的门进得来」。这里只认**真实存在且非空**
# 的 authorized_keys —— 文件在但里面是空的，等同于没有钥匙。
keys=0
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
    [ -s "$f" ] || continue
    n=$(grep -cvE '^\s*(#|$)' "$f" || true)
    [ "$n" -gt 0 ] && echo "     $f: $n 把" && keys=$((keys + n))
done
[ "$keys" -gt 0 ] || {
    echo "!! 一把公钥都没有。现在关掉密码登录 = 把自己锁在外面。中止。"
    exit 1
}

echo "==> [2/4] 写 SSH drop-in"
cat > "$DROPIN" <<'CONF'
# 由 deploy/harden.sh 生成。改这里，不要改 sshd_config 本体。
# 回滚：rm 掉本文件，再 systemctl reload ssh（或 restart ssh.socket）。

# 密码登录整个关掉。爆破再狠也没有意义了——它连一个可以猜的东西都没有
PasswordAuthentication no
KbdInteractiveAuthentication no

# root 只能用公钥。**不是 no**：部署脚本走的就是 root@，
# 改成 no 会把 deploy.sh / backup-pull.sh 一起弄坏
PermitRootLogin prohibit-password

# 单条连接内的尝试次数。反正只认公钥了，6 次没有存在的必要
MaxAuthTries 3

# 这台机器上没有任何图形程序，转发口子白开着
X11Forwarding no
CONF

echo "==> [3/4] 校验配置（不过就地回滚）"
# ⭐ `sshd -t` 不过就**立刻删掉**再退出。让一个语法错误的 drop-in 留在盘上，
#    下一次任何人 restart ssh 都会起不来 —— 而那时候多半已经没人记得它。
sshd -t || {
    rm -f "$DROPIN"
    echo "!! sshd -t 没过，drop-in 已删除，配置没有变化"
    exit 1
}

# Ubuntu 24.04 默认是 socket 激活（systemd 持有 22 端口，每条连接现起一个
# sshd@）。这种形态下配置对**新连接**立即生效，reload 反而可能没有这个动作。
# 两种都试一遍，且**都不会踢掉当前这条连接**——它是独立的 sshd 进程。
systemctl reload ssh 2>/dev/null || systemctl restart ssh.socket 2>/dev/null || true

echo "==> [4/4] fail2ban"
if ! systemctl is-active --quiet fail2ban; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban
fi

cat > /etc/fail2ban/jail.local <<'CONF'
# 由 deploy/harden.sh 生成。

[DEFAULT]
# ⚠️ **显式写死 systemd**，不要用 backend = auto。auto 是「有 /var/log/auth.log
# 就读文件」，而那个文件在 Ubuntu 24.04 上取决于 rsyslog 装没装。
# 哪天它不在了，auto 会安静地退化——fail2ban 照常 active、一个都不封，
# 而 `systemctl status` 看起来完全正常。
backend = systemd

# 封 1 小时；10 分钟内错 5 次就封
bantime  = 1h
findtime = 10m
maxretry = 5
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
CONF

systemctl enable --now fail2ban >/dev/null 2>&1
systemctl restart fail2ban

echo
echo "==> 结果"
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|maxauthtries|x11forwarding) '
echo "--- fail2ban ---"
systemctl is-active fail2ban
fail2ban-client status sshd 2>/dev/null | sed 's/^/     /'
