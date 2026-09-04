"""CSRF：把「改状态的接口长什么样」钉下来。

⚠️⚠️ **这个文件不声称"不存在 CSRF 风险"。** 它钉的是一组**可核查的事实**，
而结论建立在这几条事实加上部署形态之上：

    1. 认证 cookie 是 `HttpOnly` + `SameSite=Lax` + `Secure`（线上）
       → 跨站发起的 POST / PATCH / DELETE **不带 cookie**，到后端就是 401
    2. 改状态的接口**没有一个**收 `application/x-www-form-urlencoded`
       或 `text/plain` → 跨站 `<form>` 连请求都构造不出来
       （HTML 表单只能发这三种 enctype 之一，JSON 不在其中）
    3. 收 `multipart/form-data` 的只有两个上传接口，而且清单钉死在这里
       → 加第三个必须先来改这道题，也就必须重新想一遍
    4. **没有任何 GET 带副作用** → 顶级导航（Lax 唯一会带 cookie 的场景）
       改不了任何东西
    5. CORS 逐个列来源、不用星号，`allow_credentials=True` 与星号互斥

⚠️ 仍然存在、只是今天不可利用的面，写下来别当成"已经解决"：

    - `SameSite` 是**浏览器**在执行。不实现它的老浏览器会照发 cookie
    - Lax 认的是**可注册域**：`*.liushun666.cn` 上任何一个页面对本站都是
      "同站"。今天那台机器上只有这一个应用，但这一条是配置决定的，不是代码
    - `extract_token` 还认 `Authorization: Bearer`。**这不是 CSRF 面**：
      跨站脚本设不了自定义头（会触发预检，被来源白名单挡掉）

所以正确的说法是：**基于当前同站部署、SameSite=Lax、状态修改接口的形态
和 CORS 配置，没有发现可利用的典型 CSRF 路径。**
"""

from __future__ import annotations

import pytest

from copilot.api.app import app
from copilot.config import get_settings

# HTML 表单能发出去的三种 enctype。**JSON 不在里面**——这正是
# 「只收 JSON」本身就构成一层 CSRF 防护的原因
FORM_SUBMITTABLE = {
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
}

STATE_CHANGING = {"POST", "PATCH", "PUT", "DELETE"}

# 允许收 multipart 的接口。**清单钉死**：加第三个必须来改这道题，
# 也就必须重新想一遍"它跨站被提交会怎样"
MULTIPART_ALLOWLIST = {
    ("POST", "/api/documents"),
    ("POST", "/api/answer-corrections/images"),
}


def _state_changing_ops() -> list[tuple[str, str, set[str]]]:
    """(method, path, 收哪些 content-type)。直接读 OpenAPI，不靠人工维护清单。"""
    out = []
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            if method.upper() not in STATE_CHANGING:
                continue
            content = (op.get("requestBody") or {}).get("content") or {}
            out.append((method.upper(), path, set(content)))
    return sorted(out, key=lambda r: (r[1], r[0]))


def test_there_are_state_changing_endpoints_to_check():
    """先钉住"有东西可查"。

    ⚠️ 下面几道都是「对每一个都成立」式的断言，而空列表会让它们恒真——
    正是 ISSUES.md I-4 记的那种「绿着的空断言」。
    """
    ops = _state_changing_ops()
    assert len(ops) >= 20, f"改状态的接口只剩 {len(ops)} 个，清单是不是没读到"


@pytest.mark.parametrize(
    ("method", "path", "content"),
    _state_changing_ops(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_no_state_changing_endpoint_takes_a_form_body(method, path, content):
    """⭐⭐ 改状态的接口不许收 `urlencoded` / `text/plain`。

    跨站 `<form>` 只能发那三种 enctype。全部走 JSON 的话，攻击者连请求都
    构造不出来——而这一层**不依赖浏览器实现 SameSite**，比 cookie 属性更硬。

    ⚠️ 收 multipart 的两个上传接口是例外，在 `MULTIPART_ALLOWLIST` 里各自
    列了名字。它们仍然靠 SameSite 挡（跨站表单提交不带 cookie → 401），
    但清单钉死意味着**加第三个的时候有人必须重新想一遍**。
    """
    bad = content & FORM_SUBMITTABLE
    if bad == {"multipart/form-data"}:
        assert (method, path) in MULTIPART_ALLOWLIST, (
            f"{method} {path} 新收了 multipart。它跨站是**可以**被表单提交的，"
            "确认过再加进 MULTIPART_ALLOWLIST"
        )
        return
    assert not bad, f"{method} {path} 收 {sorted(bad)}——跨站表单能直接提交它"


def test_no_get_endpoint_changes_state():
    """⚠️ `SameSite=Lax` 唯一会带上 cookie 的场景是**顶级导航的安全方法**。

    也就是说：只要没有任何 GET 带副作用，Lax 就是完整的防线。
    一个 `GET /api/.../delete` 会把这条防线整个作废。
    """
    verbs = ("delete", "remove", "publish", "review", "retire", "logout", "approve", "reject")
    offenders = [
        p
        for p, ops in app.openapi()["paths"].items()
        if "get" in ops and any(v in p for v in verbs)
    ]
    assert offenders == [], f"这些 GET 看起来会改状态：{offenders}"


def test_the_auth_cookie_attributes_are_the_ones_the_argument_rests_on():
    """结论建立在这几个属性上，所以把它们钉在这里。

    ⚠️ `secure` 由 `COOKIE_SECURE` 决定（本地开发必须 false，否则 http 下
    浏览器根本不存这个 cookie）。这道题验的是**代码没有硬编码成 false**，
    线上那一份靠 `.env`（OPERATIONS.md「上线前 .env 要改两个值」）。
    """
    import inspect

    from copilot.auth import deps

    src = inspect.getsource(deps.set_auth_cookie)
    assert "httponly=True" in src, "JWT 放进了 JS 读得到的 cookie"
    assert 'samesite="lax"' in src, "SameSite 变了——整个 CSRF 结论要重写"
    assert "secure=s.cookie_secure" in src
    assert "path=\"/\"" in src or "path='/'" in src
    # 没有 domain= ：host-only cookie，兄弟子域拿不到它
    assert "domain=" not in src


def test_cors_never_uses_a_wildcard_origin():
    """`allow_credentials=True` 和 `allow_origins=["*"]` 互斥（浏览器规范），
    但配置写错时 FastAPI 不会拦——它只会在运行时安静地不发那个头。
    """
    s = get_settings()
    assert "*" not in s.cors_origin_list
    assert all(o.startswith(("http://", "https://")) for o in s.cors_origin_list)
