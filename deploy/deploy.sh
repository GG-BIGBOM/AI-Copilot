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

echo "==> [1/6] 本机自检（不过就别推上去）"
( cd backend && uv run ruff check . && uv run pytest -q )
( cd frontend && npm run lint && npx tsc --noEmit )

echo "==> [2/6] 构建前端"
( cd frontend && rm -rf out && npm run build )
[ -f frontend/out/index.html ] || { echo "构建没产出 out/，中止"; exit 1; }

echo "==> [3/6] 推送后端代码"
# 本机可能没有 rsync（Git Bash 默认不带），tar over ssh 到哪都能用。
# 排除 .venv：服务器自己 uv sync，两边平台不同，venv 不能直接搬
tar -czf - --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    -C backend src alembic alembic.ini pyproject.toml uv.lock .env.example \
  | $SSH "tar -xzf - -C $APP_DIR && chown -R copilot:copilot $APP_DIR"

echo "==> [4/6] 同步 systemd 单元文件"
# M5 时这两个文件是手动 scp 上去的，结果 M6 加 worker 才发现「服务器上的单元
# 文件和仓库里的可能不一致」没人守着。放进部署流程，仓库即事实。
#
# ⭐ 只 enable timer，**不 enable copilot-sync.service**：它是 oneshot，
# 由 timer 触发；单元里故意没写 [Install]，enable 它会直接报错。
tar -czf - -C deploy copilot-api.service copilot-worker.service \
                     copilot-sync.service copilot-sync.timer \
  | $SSH "tar -xzf - -C /etc/systemd/system \
          && systemctl daemon-reload \
          && systemctl enable copilot-api copilot-worker >/dev/null \
          && systemctl enable --now copilot-sync.timer >/dev/null"
# timer 要 `--now`：光 enable 只建了开机自启的符号链接，**这次不会跑起来**，
# `systemctl is-active` 会一直是 inactive，而那看起来像装失败了

echo "==> [5/6] 推送前端产物"
# 先解到 .new 再整目录替换：避免用户正好在传输中途刷到半个站
tar -czf - -C frontend/out . \
  | $SSH "rm -rf $WEB_DIR.new && mkdir -p $WEB_DIR.new && tar -xzf - -C $WEB_DIR.new \
          && rm -rf $WEB_DIR.old && mv $WEB_DIR $WEB_DIR.old 2>/dev/null || true \
          && mv $WEB_DIR.new $WEB_DIR && rm -rf $WEB_DIR.old \
          && chown -R www-data:www-data $WEB_DIR"

echo "==> [6/6] 装依赖、跑迁移、重启"
# ⭐ COPILOT_ROOT 必须显式给。config.py 会向上找 .env.example 来定位项目根，
#    但部署布局和开发布局不同，靠猜迟早出错——而它出错时是**静默**的：
#    读不到 .env 就用字段默认值，最后报「数据库密码错误」，
#    排查方向被带到 pg_hba 上，真正的原因在三层目录之外。
#
# ⭐ `--extra parse` 不能少。上传解析要 python-docx / python-pptx / pypdf，
#    它们在 `parse` 这个 extra 里，`uv sync --no-dev` 默认**不装 extra**。
#    漏了的话表现是：上传成功、状态转到「解析失败」、错误写着「服务端缺少
#    docx 解析组件」——网站一切正常，只有上传的文档全废。
#    （**永远别在服务器上装 `parse-full`**：Docling 会拖进 torch，
#    1.6GB 装都装不下，见 plan.md 一·3。`eval` 那组也不装——评测只在本机跑。）
#
# ⚠️ `uv sync` 是**声明式**的：它把环境同步成「你这次列出的样子」，
#    没列的 extra 会被**卸掉**。本机踩过——`uv sync --extra eval` 之后
#    python-docx/pptx 全被移除，10 个解析测试当场变红。要装两组就一起列。
#
# ⭐ `--extra agent` 是 M7 的（pydantic-ai + openpyxl）。它拖进来四十来个包，
#    但**只在有人真的要方案时才 import**（`routes/chat.py` 里那几个 import
#    写在函数内部，不在模块顶层）。所以普通问答的常驻内存不受影响——
#    这在 1.6GB 的机器上不是洁癖，是能不能装的问题。
$SSH "set -e
      export PATH=/root/.local/bin:\$PATH COPILOT_ROOT=$APP_DIR
      cd $APP_DIR
      uv sync --no-dev --extra parse --extra agent 2>&1 | tail -2
      uv run alembic upgrade head 2>&1 | tail -2
      chown -R copilot:copilot $APP_DIR
      systemctl restart copilot-api copilot-worker
      sleep 3
      systemctl is-active copilot-api copilot-worker
      systemctl is-active copilot-sync.timer"

echo "==> 验收"
curl -sf --resolve liushun666.cn:443:${HOST#*@} https://liushun666.cn/api/health && echo
echo "完成：https://liushun666.cn/"
