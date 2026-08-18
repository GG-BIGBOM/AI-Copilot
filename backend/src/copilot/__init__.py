"""知识库 Agent。

⚠️ 这个文件里有一行**必须在任何 pydantic 模型被构建之前**执行的设置，
所以它放在包的 `__init__` 里，而不是某个具体模块中。
"""

import os

__version__ = "0.1.0"

# ⭐ 关掉 pydantic 的 logfire 插件。
#
# **这不是优化，是修一个会让命令行整个跑不起来的 bug。**
#
# M7 装了 pydantic-ai，它顺带拖进来 logfire。logfire 在
# `pydantic` 这个 entry point 组里注册了一个插件，于是**第一次构建 pydantic
# 模型时**，pydantic 会枚举 entry points 并 `load()` 它——也就是 import logfire。
# 而 logfire 在初始化时会去读**当前工作目录**下的 `pyproject.toml`
# （见 logfire/_internal/config_params.py）。
#
# 后果：以 `copilot` 这个系统账号从 `/root` 执行任何 CLI 命令，
# 都会炸在一句毫不相干的错误上：
#
#     PermissionError: [Errno 13] Permission denied: 'pyproject.toml'
#
# 更坑的是 pydantic 加载插件时**只捕获 ImportError 和 AttributeError**
# （pydantic/plugin/_loader.py），PermissionError 直接穿出来，
# 所以连一句「插件加载失败，已跳过」的警告都没有，堆栈里全是 importlib 的帧，
# 完全看不出和 logfire 有关。第一次排查时我甚至归错了因（怪到 beartype 头上，
# 那只是它注释里提到了 pyproject.toml）。
#
# 本项目从头到尾没用 logfire。用 `setdefault` 是留个后门：
# 真要接可观测性时，外部设了这个变量就能盖掉这里。
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "logfire-plugin")
