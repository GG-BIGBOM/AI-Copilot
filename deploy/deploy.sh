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

echo "==> [1/7] 本机自检（不过就别推上去）"
( cd backend && uv run ruff check . && uv run pytest -q )
( cd frontend && npm run lint && npx tsc --noEmit )

# 勘误体检。**只警告不拦部署**：过期的勘误仍然比错的原文更接近事实，
# 拦下来只会逼人加 --skip 绕过去，那这条检查就永远没人看了。
( cd backend && uv run copilot corrections --check ) || \
    echo "  ⚠️ 上面有过期的勘误，语雀原文已经变了，抽空核对一下"

echo "==> [2/7] 构建前端"
( cd frontend && rm -rf out && npm run build )
[ -f frontend/out/index.html ] || { echo "构建没产出 out/，中止"; exit 1; }

echo "==> [3/7] 推送后端代码"
# 本机可能没有 rsync（Git Bash 默认不带），tar over ssh 到哪都能用。
# 排除 .venv：服务器自己 uv sync，两边平台不同，venv 不能直接搬
tar -czf - --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    -C backend src alembic alembic.ini pyproject.toml uv.lock .env.example \
  | $SSH "tar -xzf - -C $APP_DIR && chown -R copilot:copilot $APP_DIR"

echo "==> [4/7] 推送勘误层"
# corrections/ 在**仓库根**、不在 backend 下，所以单独推一次。
#
# ⭐ 用 `--delete` 的语义：先清空再解包。本机删掉一条勘误（= 撤销修正）时，
# 不清空的话服务器上那份会永远留着——本机看不到、线上还在生效，
# 这是最难查的一类不一致。
tar -czf - --exclude='.gitkeep' corrections \
  | $SSH "rm -rf $APP_DIR/corrections && tar -xzf - -C $APP_DIR \
          && chown -R copilot:copilot $APP_DIR/corrections"

echo "==> [5/7] 同步 systemd 单元 + 运维脚本"
# M5 时这两个文件是手动 scp 上去的，结果 M6 加 worker 才发现「服务器上的单元
# 文件和仓库里的可能不一致」没人守着。放进部署流程，仓库即事实。
#
# ⭐ 只 enable timer，**不 enable copilot-sync.service / copilot-backup.service**：
# 它们是 oneshot，由各自的 timer 触发；单元里故意没写 [Install]（或写了空的），
# enable 它会直接报错。
tar -czf - -C deploy copilot-api.service copilot-worker.service \
                     copilot-sync.service copilot-sync.timer \
                     copilot-backup.service copilot-backup.timer \
                     copilot-prune.service copilot-prune.timer \
  | $SSH "tar -xzf - -C /etc/systemd/system \
          && systemctl daemon-reload \
          && systemctl enable copilot-api copilot-worker >/dev/null \
          && systemctl enable --now copilot-sync.timer >/dev/null \
          && systemctl enable --now copilot-backup.timer >/dev/null \
          && systemctl enable --now copilot-prune.timer >/dev/null"
# timer 要 `--now`：光 enable 只建了开机自启的符号链接，**这次不会跑起来**，
# `systemctl is-active` 会一直是 inactive，而那看起来像装失败了

# ⭐ backup.sh 要推到服务器上，因为 copilot-backup.service 是去执行
# `/opt/copilot/deploy/backup.sh` 的。**单元文件和它要跑的脚本必须一起推**——
# 只推单元不推脚本的话，timer 到点了就报 203/EXEC，而那条错误只在 journal 里，
# 没有任何外部症状：站好好的，备份一份都没有。
tar -czf - -C . deploy/backup.sh \
  | $SSH "mkdir -p $APP_DIR/deploy && tar -xzf - -C $APP_DIR \
          && chmod +x $APP_DIR/deploy/backup.sh \
          && mkdir -p /var/backups/copilot"

echo "==> [6/7] 推送前端产物"
# 先解到 .new 再整目录替换：避免用户正好在传输中途刷到半个站
tar -czf - -C frontend/out . \
  | $SSH "rm -rf $WEB_DIR.new && mkdir -p $WEB_DIR.new && tar -xzf - -C $WEB_DIR.new \
          && rm -rf $WEB_DIR.old && mv $WEB_DIR $WEB_DIR.old 2>/dev/null || true \
          && mv $WEB_DIR.new $WEB_DIR && rm -rf $WEB_DIR.old \
          && chown -R www-data:www-data $WEB_DIR"

echo "==> [7/7] 装依赖、跑迁移、重启"
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
# ⚠️⚠️ 同理，**别在服务器上裸跑 `uv run`**。它会先把环境同步成 pyproject
#    的默认样子（带 dev 组、不带 extra），等于悄悄改了生产的 venv。
#    临时验证请用 `.venv/bin/python -c ...` 直接调解释器，绕开 uv 的同步。
#    真动了的话，把上面这条 `uv sync --no-dev --extra parse --extra agent`
#    原样重跑一遍就能拉回声明状态。
#
# ⭐ `--extra agent` 是 M7 的（pydantic-ai + openpyxl）。它拖进来四十来个包，
#    但**只在有人真的要方案时才 import**（`routes/chat.py` 里那几个 import
#    写在函数内部，不在模块顶层）。所以普通问答的常驻内存不受影响——
#    这在 1.6GB 的机器上不是洁癖，是能不能装的问题。
$SSH "set -e
      export PATH=/root/.local/bin:\$PATH COPILOT_ROOT=$APP_DIR
      cd $APP_DIR
      uv sync --no-dev --extra parse --extra agent 2>&1 | tail -2
      # ⚠️ **迁移用 .venv/bin/alembic，不能用 uv run alembic。**
      # uv run 会先把环境同步成 pyproject 的默认样子——带 dev 组、**不带 extra**，
      # 正好把上一行刚装好的 parse/agent 卸掉。表现极其难查：部署脚本全绿、
      # 网站也正常，只有「上传文档」和「出方案」两条路悄悄坏掉，
      # 而且下次部署又会被上一行修好、再被这一行弄坏。
      # alembic 是运行时依赖（不在 dev 组里），所以直接调它就够了。
      #
      # ⚠️ 这整段是 **双引号包着的 ssh 命令串**，里面不能出现反引号——
      # 包括注释里。反引号在双引号内是**命令替换**，会在**本机**跑一遍，
      # 然后把输出拼进远程命令里。踩过一次：注释里写了一句
      # “uv run alembic”加反引号，结果本机真去 spawn 了一个不存在的 alembic。
      .venv/bin/alembic upgrade head 2>&1 | tail -2
      chown -R copilot:copilot $APP_DIR
      systemctl restart copilot-api copilot-worker
      systemctl is-active copilot-worker copilot-sync.timer

      # ⚠️ \`systemctl is-active\` **不等于应用可用**。Type=exec 的单元在
      # 二进制被 exec 那一刻就算 active，而 uvicorn 还要几秒才 import 完、
      # 开始监听。原来写的是 \`sleep 3\`，M7 把依赖树撑大之后启动要 5 秒，
      # 于是出现过「服务 active、页面 502」——而且当时脚本照样打印了「完成」。
      # 改成轮询真实的健康检查，等到就绪为止。
      for i in \$(seq 1 30); do
        if curl -sf -o /dev/null http://127.0.0.1:8000/api/health; then
          echo \"  API 就绪（等了 \${i}s）\"
          break
        fi
        [ \$i -eq 30 ] && { echo '  API 30 秒还没起来，看 journalctl -u copilot-api'; exit 1; }
        sleep 1
      done"

echo "==> 验收（公网，走 nginx）"
# ⚠️ 原来这里写的是 `curl -sf ... && echo`——`&&` 里的失败**不会**被 set -e 拦住，
# 于是健康检查挂了也照样往下打印「完成」。踩过一次：页面 502，脚本说部署成功。
# 现在把每条都单独 check，任何一条不过就非零退出。
#
# ⭐ 必须用 `--resolve`：本机 VPN 做 fake-IP DNS 劫持，直接 curl 域名会全挂，
#    看起来像上线失败（M5 的坑 #5）。
for path in /api/health / /chat/ /documents/; do
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        --resolve "liushun666.cn:443:${HOST#*@}" "https://liushun666.cn${path}")
    printf "  %-14s %s\n" "$path" "$code"
    [ "$code" = "200" ] || { echo "  ↑ 这条没过，部署未通过验收"; exit 1; }
done

echo "==> 备份体检"
# ⭐ **这是这个项目唯一会被人真的读到的备份告警渠道。**
# 服务器上没有邮件、没有 Sentry，备份任务悄悄失败三周也不会有任何外部症状——
# 站好好的、答案照常出、你每天还在往本机拉「备份」。所以把检查挂在
# **每次上线都会看的那段输出**里：LAST_OK 超过 48 小时就把这次部署判为不通过。
#
# 为什么是 48 小时不是 24：timer 有最多 5 分钟随机延迟，而上线常常发生在
# 刚过整点的时候。卡 24 小时会周期性地误报，而**误报几次之后这条检查就没人看了**。
AGE=$($SSH "if [ -f /var/backups/copilot/LAST_OK ]; then
                echo \$(( (\$(date -u +%s) - \$(date -u -d \"\$(cat /var/backups/copilot/LAST_OK)\" +%s)) / 3600 ))
            else echo -1; fi")
COUNT=$($SSH "ls -1 /var/backups/copilot/kb-*.dump 2>/dev/null | wc -l")
if [ "$AGE" = "-1" ]; then
    echo "  ⚠️ 还没有成功过一次备份。timer 刚装上的话，今晚 04:10（北京）会跑第一次；"
    echo "     不想等就手工来一次：ssh 上去 systemctl start copilot-backup.service"
elif [ "$AGE" -gt 48 ]; then
    echo "  ⚠️ 最近一次成功备份是 ${AGE} 小时前 —— 备份多半已经坏了"
    $SSH "systemctl status copilot-backup.timer --no-pager | head -5" || true
    echo "  ↑ 部署未通过验收（站是好的，但 R8 又回来了）"
    exit 1
else
    echo "  上次备份 ${AGE} 小时前，现存 ${COUNT} 份"
fi

echo "完成：https://liushun666.cn/"
