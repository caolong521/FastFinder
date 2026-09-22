from __future__ import annotations

"""FastFinder public application metadata.

Keep public-facing software/developer information in this file so the About dialog,
Windows executable metadata and future release tooling stay consistent.

Before publishing your GitHub repository you may replace DEVELOPER_DISPLAY_NAME,
PROJECT_URL and CONTACT_TEXT with the public information you actually want to expose.
Do not put private email addresses, phone numbers or local filesystem paths here.
"""

APP_NAME = "FastFinder"
APP_VERSION = "2.7.0"
APP_DESCRIPTION = "Windows 本地文件 / 文件夹极速搜索工具"
APP_DESCRIPTION_EN = "Fast, privacy-first local file and folder search for Windows"

ORGANIZATION_NAME = "SolveTrue"
DEVELOPER_DISPLAY_NAME = "SolveTrue / FastFinder Contributors"
COPYRIGHT_YEAR = "2026"
COPYRIGHT_HOLDER = "SolveTrue and FastFinder contributors"

LICENSE_NAME = "MIT License"
APP_USER_MODEL_ID = "SolveTrue.FastFinder"

# Fill these when the public repository/contact channel is ready.
PROJECT_URL = ""
CONTACT_TEXT = ""

PRIVACY_SUMMARY = (
    "FastFinder 的文件索引、搜索历史、收藏与使用记录默认保存在本机 SQLite 数据库中，"
    "程序本身不需要把这些数据上传到服务器。"
)


def version_tuple4() -> tuple[int, int, int, int]:
    parts = []
    for raw in APP_VERSION.split("."):
        try:
            parts.append(int(raw))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def software_summary() -> str:
    rows = [
        f"软件名称：{APP_NAME}",
        f"版本：{APP_VERSION}",
        f"说明：{APP_DESCRIPTION}",
        f"开发者：{DEVELOPER_DISPLAY_NAME}",
        f"组织/品牌：{ORGANIZATION_NAME}",
        f"许可证：{LICENSE_NAME}",
        f"版权：Copyright (c) {COPYRIGHT_YEAR} {COPYRIGHT_HOLDER}",
    ]
    if PROJECT_URL:
        rows.append(f"项目地址：{PROJECT_URL}")
    if CONTACT_TEXT:
        rows.append(f"联系：{CONTACT_TEXT}")
    return "\n".join(rows)
