"""Locate and render local docs/wiki Markdown for in-app help."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

try:
    import markdown as _markdown
except Exception:  # pragma: no cover
    _markdown = None


# Display order for the in-app TOC (filename without path).
HELP_TOC: tuple[tuple[str, str, str], ...] = (
    # id, filename, i18n key for sidebar label
    ("home", "Home.md", "help.toc.home"),
    ("quickstart", "快速开始.md", "help.toc.quickstart"),
    ("ui", "界面导览.md", "help.toc.ui"),
    ("record", "录制与切割.md", "help.toc.record"),
    ("paths", "路径与数据目录.md", "help.toc.paths"),
    ("config", "配置参考.md", "help.toc.config"),
    ("webhook", "Webhook与通知.md", "help.toc.webhook"),
    ("troubleshoot", "故障排查.md", "help.toc.troubleshoot"),
    ("dev_run", "从源码运行与构建.md", "help.toc.dev_run"),
    ("release", "发布与自动更新.md", "help.toc.release"),
    ("arch", "架构概览.md", "help.toc.arch"),
)

# Map wiki link targets (stem or filename) -> section id
_LINK_ALIASES = {
    "home": "home",
    "首页": "home",
    "快速开始": "quickstart",
    "界面导览": "ui",
    "录制与切割": "record",
    "路径与数据目录": "paths",
    "配置参考": "config",
    "webhook与通知": "webhook",
    "webhook 与通知": "webhook",
    "故障排查": "troubleshoot",
    "从源码运行与构建": "dev_run",
    "发布与自动更新": "release",
    "架构概览": "arch",
}


@dataclass(frozen=True)
class HelpSection:
    id: str
    filename: str
    i18n_key: str
    path: str


def project_root() -> str:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: prefer _MEIPASS bundle, then exe dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.isdir(os.path.join(meipass, "docs", "wiki")):
            return meipass
        return os.path.dirname(sys.executable)
    # core/help_docs.py -> project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def docs_wiki_dir(root: Optional[str] = None) -> str:
    base = root if root is not None else project_root()
    return os.path.join(base, "docs", "wiki")


def list_sections(root: Optional[str] = None) -> list[HelpSection]:
    wiki = docs_wiki_dir(root)
    out: list[HelpSection] = []
    for sid, filename, i18n_key in HELP_TOC:
        path = os.path.join(wiki, filename)
        if os.path.isfile(path):
            out.append(HelpSection(sid, filename, i18n_key, path))
    return out


def resolve_section(section_id: Optional[str], root: Optional[str] = None) -> Optional[HelpSection]:
    sections = list_sections(root)
    if not sections:
        return None
    if not section_id:
        return sections[0]
    wanted = str(section_id).strip().lower()
    for sec in sections:
        if sec.id == wanted:
            return sec
    # allow filename / stem
    for sec in sections:
        stem = os.path.splitext(sec.filename)[0].lower()
        if wanted in (sec.filename.lower(), stem):
            return sec
    alias = _LINK_ALIASES.get(wanted)
    if alias:
        for sec in sections:
            if sec.id == alias:
                return sec
    return sections[0]


def read_section_markdown(section: HelpSection) -> str:
    with open(section.path, "r", encoding="utf-8") as fp:
        return fp.read()


def section_id_from_href(href: str) -> Optional[str]:
    """Map a wiki-style relative link to a section id, or None if external."""
    if not href:
        return None
    raw = href.strip()
    if raw.startswith(("http://", "https://", "mailto:")):
        return None
    # strip anchors / query
    raw = raw.split("#", 1)[0].split("?", 1)[0]
    raw = raw.replace("\\", "/").strip("/")
    if not raw:
        return None
    stem = os.path.splitext(os.path.basename(raw))[0]
    key = stem.lower()
    if key in {sid for sid, _, _ in HELP_TOC}:
        return key
    return _LINK_ALIASES.get(key) or _LINK_ALIASES.get(stem)


def _rewrite_wiki_links(md_text: str) -> str:
    """Turn [text](快速开始) into [text](#section:quickstart) for QTextBrowser anchors."""

    def repl(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        sid = section_id_from_href(href)
        if sid:
            return f"[{label}](#section:{sid})"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, md_text)


def render_markdown_html(md_text: str, *, title: str = "", dark: bool = True) -> str:
    body_md = _rewrite_wiki_links(md_text or "")
    if _markdown is not None:
        html_body = _markdown.markdown(
            body_md,
            extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        )
    else:
        # Extremely small fallback
        escaped = (
            body_md.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        html_body = "<pre>" + escaped + "</pre>"

    if dark:
        bg, fg, muted, code_bg, link, border = (
            "#1A1B21", "#E2E8F0", "#94A3B8", "#0F1117", "#60A5FA", "#2D2E3A",
        )
    else:
        bg, fg, muted, code_bg, link, border = (
            "#FFFFFF", "#1E293B", "#64748B", "#F1F5F9", "#2563EB", "#CBD5E1",
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{
  margin: 0;
  padding: 16px 20px 28px 20px;
  background: {bg};
  color: {fg};
  font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.65;
}}
h1, h2, h3, h4 {{
  color: {fg};
  line-height: 1.35;
}}
h1 {{ font-size: 22px; margin-top: 0; }}
h2 {{ font-size: 18px; margin-top: 1.4em; border-bottom: 1px solid {border}; padding-bottom: 6px; }}
h3 {{ font-size: 15px; margin-top: 1.2em; }}
a {{ color: {link}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{
  background: {code_bg};
  border: 1px solid {border};
  border-radius: 4px;
  padding: 1px 5px;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12.5px;
}}
pre {{
  background: {code_bg};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 12px 14px;
  overflow-x: auto;
}}
pre code {{
  border: none;
  padding: 0;
  background: transparent;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}}
th, td {{
  border: 1px solid {border};
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}}
th {{ background: {code_bg}; }}
blockquote {{
  margin: 12px 0;
  padding: 8px 14px;
  border-left: 3px solid {link};
  color: {muted};
  background: {code_bg};
}}
hr {{ border: none; border-top: 1px solid {border}; margin: 18px 0; }}
ul, ol {{ padding-left: 1.4em; }}
.help-missing {{ color: {muted}; }}
</style></head><body>
{html_body}
</body></html>"""


def render_section_html(section: HelpSection, *, dark: bool = True) -> str:
    return render_markdown_html(read_section_markdown(section), title=section.filename, dark=dark)


def missing_docs_html(*, dark: bool = True) -> str:
    msg = (
        "<h1>未找到本地文档</h1>"
        "<p class='help-missing'>未能定位 <code>docs/wiki</code>。"
        "源码运行请确认仓库中存在该目录；打包版请确认构建时已包含文档资源。</p>"
    )
    return render_markdown_html(msg, dark=dark)
