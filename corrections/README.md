# 勘误层

语雀原文写错了，在这里覆盖它。

```
corrections/<slug>.md      ← 这一层，进 Git，可 diff、可回滚
      ↓ ingest 时覆盖
data/raw/yuque/**/*.md     ← sync 的产物，永远和语雀保持一致，不手工碰
```

## 怎么用

```bash
cd backend

uv run copilot correct 京东面单          # 搜文档 → 编辑器改 → 存成勘误文件
uv run copilot correct 某文档 --retire   # 整篇作废（语雀删了 / 彻底过时）
uv run copilot corrections               # 看所有勘误，过期的会标黄

uv run copilot ingest                    # 让勘误生效（本机）
git add corrections/ && git commit       # 改动进版本库
./deploy/deploy.sh                       # 推到服务器，线上生效
```

## 一个文件长什么样

```markdown
---
target_url: https://www.yuque.com/wdterpqjb/shezhi/tzmxafmgyxghn0n8
title: "设置 · 预估成本"
based_on: 2024-04-01T03:26:41.000Z
reason: "操作上限原文写 300 单，实际是 500 单"
---

（修正后的完整正文）
```

- **`target_url`** 是和语雀原文对齐的唯一键。抄错了勘误就一个字都不会生效——
  所以 `ingest` 会把「没对上号」的勘误单独列出来警告。
- **`reason` 必填。** 半年后你会需要它，而那时语雀原文早变了，无从倒推。
- **`based_on`** 是写勘误时语雀那篇的版本。语雀后来又更新了，
  `copilot corrections` 就把这条标成**过期**——勘误仍然生效，
  但需要人去核对一下语雀那边是不是已经自己改对了。
- **`retired: true`** 表示整篇从索引里删掉，此时正文可以为空。

## 几条规矩

1. **不要直接改 `data/raw/yuque/`。** 那是 sync 的产物，`data/` 又在
   `.gitignore` 里：改动没记录、换台机器就没、而且语雀那篇一更新就被静默冲掉。
2. **一篇文档只能有一个勘误文件。** 撞车会直接报错——静默取其一的话，
   生效的是哪份取决于文件名排序，改个文件名就换一份内容。
3. **改完必须 `ingest` 才生效**，推到线上必须走 `deploy.sh`。
   只 commit 不部署 = 本机是对的、线上还是错的。
