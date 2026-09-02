"""自检清单只许有一份——这是「本机绿、CI 红」那一类故障的机制性堵法。

⭐ **为什么需要这么一份测试。**

2026-08-25：`app/layout.tsx` 开始用 `LayoutProps<"/">`。那是 Next 16
**生成**的全局类型，住在 `.next/types/`，而那个目录是 gitignore 的。

    本机     `.next/` 里还留着上一次 `npm run build` 的产物 → tsc 绿
    CI       干净检出，没有那个目录            → TS2304: Cannot find name 'LayoutProps'

修的时候往 CI 里补了一行 `npx next typegen`。**但同一份清单当时抄在三个地方**
——`.github/workflows/ci.yml`、`deploy/deploy.sh` 第 1 步、`plan.md` 的
「本机自检」——补了一处，另外两处没动，靠一句「两边任何一处改了另一处也要改」
的注释维持一致。那句注释就是上一次失败的原因本身。

所以这一轮把清单收成一份（`frontend/package.json` 的 `verify`），
三个入口都去调它。**而这份测试就是拦住"再抄一份"的那道闸门**：
谁把某个入口改回手写命令，后端测试当场红。

⚠️ 它验的是**入口都指向同一份清单**，不是"清单里有哪几项"。
清单该有几项是会变的（以后加 e2e、加 a11y 都正常），而"有几个地方各自维护
一份清单"永远是个 bug——差异出现时没有任何症状，只有某一个入口悄悄少跑一项。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY = ROOT / "deploy" / "deploy.sh"
PKG = ROOT / "frontend" / "package.json"
PLAN = ROOT / "plan.md"


def _scripts() -> dict[str, str]:
    return json.loads(PKG.read_text(encoding="utf-8"))["scripts"]


# ═══════════════ 一、清单本体 ═══════════════


def test_verify_is_the_single_frontend_checklist():
    """`verify` 必须真的把四项都串起来，否则"只有一份清单"是句空话。"""
    scripts = _scripts()
    assert "verify" in scripts, "frontend/package.json 里没有 verify —— 清单没了"
    verify = scripts["verify"]
    for piece in ("npm test", "run lint", "run typecheck", "run build"):
        assert piece in verify, f"verify 少了 {piece!r}：{verify!r}"


def test_typecheck_wipes_the_generated_types_first():
    """⚠️⚠️ **`.next/types` 的残留正是「本机绿」的隐藏前置条件。**

    只加 `next typegen` 不够：本机那个目录里还躺着上一次构建生成的类型，
    typegen 也只是往里补写，删掉的路由留下的旧声明仍然在。
    本机因此永远比干净检出宽松一档，而宽松的那一档没有任何症状。
    """
    typecheck = _scripts().get("typecheck", "")
    assert ".next/types" in typecheck, f"typecheck 没删生成目录：{typecheck!r}"
    assert "rmSync" in typecheck or "rm -rf" in typecheck, (
        f"typecheck 提到了 .next/types 却没删它：{typecheck!r}"
    )
    assert "next typegen" in typecheck, f"typecheck 少了 typegen：{typecheck!r}"
    assert "tsc --noEmit" in typecheck, f"typecheck 少了 tsc：{typecheck!r}"

    # 顺序：先删、再生成、最后检查。写反了等于没删
    order = [
        typecheck.index(".next/types"),
        typecheck.index("next typegen"),
        typecheck.index("tsc --noEmit"),
    ]
    assert order == sorted(order), f"typecheck 三步的顺序反了：{typecheck!r}"


# ═══════════════ 二、每个入口都调那一份 ═══════════════


@pytest.mark.parametrize(
    ("path", "who"),
    [(CI, "CI"), (DEPLOY, "deploy.sh")],
)
def test_every_entry_point_calls_verify(path: Path, who: str):
    text = path.read_text(encoding="utf-8")
    assert "npm run verify" in text, f"{who} 没有调 npm run verify"


@pytest.mark.parametrize(
    ("path", "who"),
    [(CI, "CI"), (DEPLOY, "deploy.sh")],
)
@pytest.mark.parametrize("hand_rolled", ["next typegen", "tsc --noEmit"])
def test_no_entry_point_hand_rolls_the_checklist(path: Path, who: str, hand_rolled: str):
    """⚠️ 手写的命令只许出现在注释里。

    判据是「这一行是不是注释」而不是「文件里有没有这个词」——
    两份文件都需要在注释里解释这段历史，而解释本身不能让这道题变红。
    """
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert hand_rolled not in stripped, (
            f"{who}:{lineno} 又手写了一遍 {hand_rolled!r}——"
            f"清单只许有一份，改 frontend/package.json 的 verify\n  {line}"
        )


def test_the_documented_self_check_is_the_same_command():
    """plan.md 里写给人看的那段「本机自检」也要指向同一份清单。

    ⭐ 它是三个入口里**唯一一个人会照着敲的**。文档和实际跑的命令不一样时，
    人照着敲出来的结果是绿的、CI 是红的，而人会先相信自己敲的那个。
    """
    text = PLAN.read_text(encoding="utf-8")
    assert "npm run verify" in text, "plan.md 的本机自检没跟着改成 npm run verify"


# ═══════════════ 三、后端那一半 ═══════════════


@pytest.mark.parametrize(
    ("path", "who"),
    [(CI, "CI"), (DEPLOY, "deploy.sh")],
)
def test_backend_lint_still_covers_tests_and_eval(path: Path, who: str):
    """ADR-18：`tests/` 和 `eval/` 在仓库根，`ruff check .` 一行扫不到。

    漏掉它们的表现是**这一步照样绿**——它们从 M0 到 2026-08-28 一直没被
    lint 过，而纳入的第一次就抓到一条 F841（一道声称在守某个判据、
    实际什么都没断言的测试）。这种"绿着的漏洞"只能靠断言盯住。
    """
    text = path.read_text(encoding="utf-8")
    assert "ruff check . ../tests ../eval" in text, f"{who} 的 ruff 漏了 ../tests ../eval"


# ═══════════════ 四、写文件一律 LF ═══════════════


def test_eval_writers_never_use_bare_write_text():
    r"""⚠️⚠️ **`Path.write_text` 在 Windows 上会把 `\n` 写成 `\r\n`。**

    这不是理论问题，是这个仓库出过的事故：一个补丁脚本用 `write_text`
    改了一行 `deploy/backup.sh`，Linux 上 bash 读到 `pipefail\r`，
    **备份从此每天照常"跑完"、每天什么都没备份**（见 deploy.sh 文件头）。

    `eval/` 这边表现温和得多——`.gitattributes` 的 `eol=lf` 在 `git add`
    时把它们规范回 LF，所以 `git status` 干干净净——但也正因为看不出来，
    2026-08-29 之前 `eval/results/` 里 106 个文件在磁盘上全是 CRLF，
    没有任何人发现写文件那一层是错的。**git 的规范化是安全网，
    不该当成唯一的正确性来源。**

    统一走 `run.save_json()`，它显式传 `newline="\n"`。

    ⚠️ 判据走 `ast`，不是搜字符串。这几个文件的注释和 docstring 里到处都在
    讲这件事本身（包括这段），按文本搜的话它们会把自己判红——
    一条**因为解释了自己而失败**的检查，最后一定被人加白名单绕过去。
    """
    import ast

    # ⚠️ 后端也扫。`copilot correct` 写的勘误文件要 `git add` 进版本库、
    # 再 rsync 到 Linux；`sync-yuque` 写的正文是 `content_hash` 判重的输入。
    # 两处都在 Windows 上写、在 Linux 上读，和 backup.sh 那次一模一样
    sources = sorted((ROOT / "eval").glob("*.py")) + sorted(
        (ROOT / "backend" / "src" / "copilot").rglob("*.py")
    )
    offenders = []
    for py in sources:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "write_text"):
                continue
            if any(kw.arg == "newline" for kw in node.keywords):
                continue  # 显式指定了换行的写法是对的，`save_json` 就是
            offenders.append(f"{py.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert not offenders, (
        "这些地方还在裸用 write_text，Windows 上会写出 CRLF：\n  " + "\n  ".join(offenders)
    )


def test_committed_eval_results_are_lf_on_disk():
    """磁盘上那份也要是 LF，不能只靠 git 在 `add` 的时候救。

    ⚠️ 判据是**工作区的字节**，不是 `git diff`——后者会先过一遍 clean 过滤器，
    于是 CRLF 的文件和 LF 的 blob 比出来"没有差异"。那正是这个问题
    在此之前从来没被看见的原因。
    """
    bad = [
        p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "eval" / "results").glob("*.json"))
        if b"\r\n" in p.read_bytes()
    ]
    assert not bad, f"这些结果文件在磁盘上是 CRLF（共 {len(bad)} 个）：{bad[:5]}"


# ═══════════════ 五、内嵌在 shell 里的 Python 必须能编译 ═══════════════


def test_embedded_python_in_deploy_actually_compiles():
    """⚠️⚠️ **CRLF 闸门自己被写坏过，代价是整条部署路静悄悄地死了 4 天。**

    2026-08-29 的 `26c6e9f`——标题正是「写文件一律 LF」——把闸门里一句注释
    写断成了两行。后半句 `就是 bad interpreter；……` 前面没有 `#`，
    于是那段内嵌 Python 从那一刻起是语法错的：

        File "<stdin>", line 6
        SyntaxError: invalid character '；' (U+FF1B)

    **它没有写坏生产**，因为它跑在 `[1/7] 本机自检`，在推任何东西之前。
    真正的代价是反过来的：从那天起 `deploy.sh` 一次都跑不完，
    main 上的两个迁移（`c8d3f1a704be` / `d9c4f2e81b36`）在生产外面躺了 4 天，
    而**没有人收到过一行告警**——脚本每次都"报个错就退出"，
    在 GBK 控制台上还是一屏乱码，看起来像是本机环境的问题。
    2026-09-02 真要部署时才撞上。

    ⚠️ **这段代码住在 shell 的 heredoc 里，谁都够不着它**：Python 的语法
    检查、ruff、编辑器高亮、CI——一个都不会去解析 `.sh` 文件里的字符串。
    它唯一被验证的时机就是部署那一刻，而那恰好是最不该发现语法错的时刻。

    ⚠️ 判据是 `compile()`，不是"检查每行有没有 `#`"。注释断行只是这一次的
    形态，下次可能是缩进、可能是引号。**能不能编译**才是那段脚本真正
    需要成立的性质，也是唯一不会随写法变化的判据。
    """
    import re

    blocks = re.findall(
        r"<<'PYEOF'\n(.*?)\n^PYEOF$", DEPLOY.read_text(encoding="utf-8"), re.S | re.M
    )
    assert blocks, "deploy.sh 里一个内嵌 Python 块都找不到——它被挪走了，还是分隔符改了？"

    for i, src in enumerate(blocks, 1):
        try:
            compile(src, f"deploy.sh 第 {i} 个 PYEOF 块", "exec")
        except SyntaxError as exc:
            broken = src.split("\n")[(exc.lineno or 1) - 1]
            raise AssertionError(
                f"deploy.sh 第 {i} 个内嵌 Python 块编译不过，部署会卡在 [1/7]：\n"
                f"  第 {exc.lineno} 行: {broken!r}\n"
                f"  {type(exc).__name__}: {exc.msg}"
            ) from exc
