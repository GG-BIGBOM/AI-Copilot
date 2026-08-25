#!/usr/bin/env bash
# 服务器一次性初始化。在服务器上以 root 执行。
#
# 这份脚本是 2026-08-17 首次上线时**实际跑通的步骤**，不是照着想象写的。
# 幂等：重复执行不会破坏已有状态。
#
#   scp deploy/setup-server.sh root@<服务器>:/tmp/ && ssh root@<服务器> bash /tmp/setup-server.sh
set -euo pipefail

APP_DIR=/opt/copilot
WEB_DIR=/var/www/copilot
PG_VERSION=16

echo "==> [1/7] 备份现有配置（动任何东西之前）"
BACKUP=/root/backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
cp -a /etc/nginx "$BACKUP/nginx" 2>/dev/null || true
[ -d /var/www ] && cp -a /var/www "$BACKUP/var-www" || true
echo "    备份在 $BACKUP"

echo "==> [2/7] PostgreSQL $PG_VERSION + pgvector"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq "postgresql-$PG_VERSION" "postgresql-$PG_VERSION-pgvector"

# 1.6GB 内存下的收敛配置。默认值是按独占服务器给的，
# 在这台机器上会把 FastAPI 和 nginx 一起挤死。
cat > "/etc/postgresql/$PG_VERSION/main/conf.d/zz-lowmem.conf" <<'EOF'
shared_buffers = 128MB
max_connections = 20            # 应用侧 pool_size=5 + max_overflow=5，够用
work_mem = 8MB
maintenance_work_mem = 64MB
effective_cache_size = 512MB
log_min_duration_statement = 2000
EOF
systemctl restart postgresql

echo "==> [3/7] 建库建用户"
if [ ! -f /root/.kb-db-password ]; then
    # 只用字母数字：密码要塞进 DATABASE_URL，带 /+= 还得处理 URL 转义
    umask 077
    openssl rand -base64 24 | tr -d '/+=' | head -c 28 > /root/.kb-db-password
fi
PW=$(cat /root/.kb-db-password)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<EOF
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='kb') THEN
    CREATE ROLE kb LOGIN PASSWORD '$PW';
  ELSE
    ALTER ROLE kb PASSWORD '$PW';
  END IF;
END \$\$;
EOF
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='kb'" | grep -q 1 \
    || sudo -u postgres createdb -O kb kb
sudo -u postgres psql -d kb -c "CREATE EXTENSION IF NOT EXISTS vector"

echo "==> [4/7] uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# ⭐ 阿里云到 PyPI 慢得离谱：实测默认源几分钟才装 2 个包，
# 换清华源后 20 秒装完 92 个。这不是优化，是能不能装完的问题。
mkdir -p /root/.config/uv
cat > /root/.config/uv/uv.toml <<'EOF'
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
EOF

echo "==> [5/7] 服务账号与目录"
# 公网可注册的服务不要用 root 跑
id copilot >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin copilot
mkdir -p "$APP_DIR/data" "$WEB_DIR"
chown -R copilot:copilot "$APP_DIR"
chown -R www-data:www-data "$WEB_DIR"

echo "==> [6/7] swap（1.6GB 内存的免费保险）"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> [7/7] 检查"
systemctl is-active postgresql
sudo -u postgres psql -d kb -tAc "SELECT 'pgvector '||extversion FROM pg_extension WHERE extname='vector'"
free -h | head -2

cat <<'NEXT'

初始化完成。接下来：
  0. ⭐ 先加固，再放数据（在服务器上）：
     bash /tmp/harden.sh             # 见 deploy/harden.sh
     ⚠️ 它会关掉 SSH 密码登录。跑之前确认 authorized_keys 里有你的公钥，
        脚本自己也会检查，一把都没有就拒绝执行。

  1. ./deploy/deploy.sh              # 传代码 + 构建前端 + 起服务（本机执行）
  2. 服务器上灌数据：
     sudo -u copilot env COPILOT_ROOT=/opt/copilot HOME=/tmp \
       /opt/copilot/.venv/bin/copilot sync-yuque https://www.yuque.com/wdterpqjb
     sudo -u copilot env COPILOT_ROOT=/opt/copilot HOME=/tmp \
       /opt/copilot/.venv/bin/copilot ingest
  3. 发邀请码：
     sudo -u copilot env COPILOT_ROOT=/opt/copilot HOME=/tmp \
       /opt/copilot/.venv/bin/copilot invite -n 5
NEXT
