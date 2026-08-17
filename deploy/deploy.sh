#!/usr/bin/env bash
# 本机构建 → 推送 → 重启。在**本机**（不是服务器）的仓库根目录执行。
#
#   ./deploy/deploy.sh
#
# ⭐ 前端一定在本机构建。`next build` 峰值吃 1GB+，服务器只有 1.6GB，
#    在上面 build 必被 OOM killer 干掉——这是这套部署方案的第一条生死线。
set -euo pipefail

HOST=${COPILOT_HOST:-root@8.136.116.9}
KEY=${COPILOT_SSH_KEY:-$HOME/.ssh/erp_vps}
APP_DIR=/opt/copilot
WEB_DIR=/var/www/copilot

SSH="ssh -i $KEY -o BatchMode=yes $HOST"
cd "$(dirname "$0")/.."

echo "==> [1/5] 本机自检（不过就别推上去）"
( cd backend && uv run ruff check . && uv run pytest -q )
( cd frontend && npm run lint && npx tsc --noEmit )

echo "==> [2/5] 构建前端"
( cd frontend && rm -rf out && npm run build )
[ -f frontend/out/index.html ] || { echo "构建没产出 out/，中止"; exit 1; }

echo "==> [3/5] 推送后端代码"
# 本机可能没有 rsync（Git Bash 默认不带），tar over ssh 到哪都能用。
# 排除 .venv：服务器自己 uv sync，两边平台不同，venv 不能直接搬
tar -czf - --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    -C backend src alembic alembic.ini pyproject.toml uv.lock .env.example \
  | $SSH "tar -xzf - -C $APP_DIR && chown -R copilot:copilot $APP_DIR"

echo "==> [4/5] 推送前端产物"
# 先解到 .new 再整目录替换：避免用户正好在传输中途刷到半个站
tar -czf - -C frontend/out . \
  | $SSH "rm -rf $WEB_DIR.new && mkdir -p $WEB_DIR.new && tar -xzf - -C $WEB_DIR.new \
          && rm -rf $WEB_DIR.old && mv $WEB_DIR $WEB_DIR.old 2>/dev/null || true \
          && mv $WEB_DIR.new $WEB_DIR && rm -rf $WEB_DIR.old \
          && chown -R www-data:www-data $WEB_DIR"

echo "==> [5/5] 装依赖、跑迁移、重启"
# ⭐ COPILOT_ROOT 必须显式给。config.py 会向上找 .env.example 来定位项目根，
#    但部署布局和开发布局不同，靠猜迟早出错——而它出错时是**静默**的：
#    读不到 .env 就用字段默认值，最后报「数据库密码错误」，
#    排查方向被带到 pg_hba 上，真正的原因在三层目录之外。
$SSH "set -e
      export PATH=/root/.local/bin:\$PATH COPILOT_ROOT=$APP_DIR
      cd $APP_DIR
      uv sync --no-dev 2>&1 | tail -2
      uv run alembic upgrade head 2>&1 | tail -2
      chown -R copilot:copilot $APP_DIR
      systemctl restart copilot-api
      sleep 3
      systemctl is-active copilot-api"

echo "==> 验收"
curl -sf --resolve liushun666.cn:443:${HOST#*@} https://liushun666.cn/api/health && echo
echo "完成：https://liushun666.cn/"
