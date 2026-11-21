"""构建期 edition 标记：free / lite / complete / enterprise。

本文件由 tools/build_editions.py 在每次打包前重写，全部构建结束后恢复为 free。
当前发布只构建 free；169/260 是免费版导入知识库后的测试状态。
查当前档位：python tools/print_edition.py
"""

EDITION = "free"
IS_OFFLINE = False

LIMITS = {
    "free": {"rules": None, "cases": None, "terms": None, "strict_daily": None},
    "lite": {"rules": None, "cases": None, "terms": None, "strict_daily": None},
    "buyout": {"rules": None, "cases": None, "terms": None, "strict_daily": None},
    "complete": {"rules": None, "cases": None, "terms": None, "strict_daily": None},
    "enterprise": {"rules": None, "cases": None, "terms": None, "strict_daily": None},
}
