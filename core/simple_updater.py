"""
更新页面 UI（kachina 模式）

页面结构（自上而下）:
  1. 顶部 toolbar    — 版本号 + 大小 + 发布时间 + 关闭按钮
  2. 中间 scrollable — release note (Markdown 渲染) + 通道选择（GitHub / Mirror 酱）
  3. 底部按钮区     — 取消 + 立即更新

kachina 模式下,主程序不下载任何东西:
  - 用户点"立即更新" → spawn DDRec.update.exe
  - update.exe 自己读 kachina.config.json 下载/HDiffPatch/替换文件
"""

import os
import sys
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton,
    QInputDialog, QLineEdit,
    QScrollArea, QWidget, QFrame,
    QTextBrowser,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.updater import UpdateInfo, get_local_version

logger = logging.getLogger(__name__)


# ==================== Markdown 渲染 CSS（暗色主题）====================
_MARKDOWN_CSS = """
h1, h2, h3, h4 { color: #F8FAFC; margin-top: 14px; margin-bottom: 8px; font-weight: 600; }
h1 { font-size: 20px; }
h2 { font-size: 17px; border-bottom: 1px solid #2D2E3A; padding-bottom: 6px; }
h3 { font-size: 15px; }
h4 { font-size: 14px; color: #CBD5E1; }
p { margin: 8px 0; line-height: 1.7; }
ul, ol { margin: 8px 0; padding-left: 24px; }
li { margin: 4px 0; line-height: 1.6; }
code {
    background-color: #0F1014;
    color: #F59E0B;
    padding: 1px 6px;
    border-radius: 3px;
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 12px;
}
pre {
    background-color: #0F1014;
    color: #CBD5E1;
    padding: 12px 14px;
    border-radius: 6px;
    border: 1px solid #2D2E3A;
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 12px;
    line-height: 1.5;
}
pre code { background: transparent; padding: 0; color: inherit; }
a { color: #3B82F6; text-decoration: none; }
a:hover { text-decoration: underline; }
blockquote {
    color: #94A3B8;
    border-left: 3px solid #3B82F6;
    padding-left: 14px;
    margin: 10px 0;
}
hr { color: #2D2E3A; background-color: #2D2E3A; border: none; max-height: 1px; margin: 16px 0; }
table {
    border-collapse: collapse;
    margin: 10px 0;
}
th, td {
    border: 1px solid #2D2E3A;
    padding: 6px 12px;
    text-align: left;
}
th { background-color: #252631; color: #F8FAFC; }
img { max-width: 100%; border-radius: 6px; }
strong { color: #F8FAFC; font-weight: 600; }
em { color: #CBD5E1; }
"""


def _render_markdown(text: str) -> str:
    """渲染 markdown → HTML（带暗色 CSS）。失败时返回纯文本。"""
    if not text or not text.strip():
        return ""
    try:
        import markdown as md_lib
        html = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        # 用 <style> 包裹的 div 确保 CSS 生效
        return f'<style>{_MARKDOWN_CSS}</style>{html}'
    except Exception as e:
        logger.warning(f"Markdown 渲染失败,降级纯文本: {e}")
        # 转义 HTML 特殊字符后回填
        escaped = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
        )
        return f'<div style="color: #CBD5E1; line-height: 1.6;">{escaped}</div>'


def _format_published_at(iso_str: str) -> str:
    """GitHub API 返回 ISO 8601 UTC,如 2024-01-15T10:30:00Z。
    简单切成 YYYY-MM-DD HH:MM,不做时区换算(原始就是 UTC,前端展示由用户感受)。
    """
    if not iso_str:
        return ""
    # 兼容 'Z' 后缀和带时区的格式
    s = iso_str.replace("Z", "+00:00")
    # 取前 16 字符:YYYY-MM-DDTHH:MM
    if len(s) >= 16 and s[4] == "-" and s[7] == "-" and (s[10] == "T" or s[10] == " "):
        return f"{s[:4]}-{s[5:7]}-{s[8:10]} {s[11:13]}:{s[14:16]} UTC"
    return iso_str


def _make_channel_row(icon: str, title: str, desc: str) -> dict:
    """构造一个通道行:左边 icon + 中间 title/desc + 右边 checkable 按钮(模拟截图里的"立即更新"位)。
    返回 {'frame': QFrame, 'btn': QPushButton}
    """
    frame = QFrame()
    frame.setObjectName("channelRow")
    frame.setStyleSheet("""
        QFrame#channelRow {
            background-color: #252631;
            border: 1px solid #2D2E3A;
            border-radius: 8px;
        }
        QFrame#channelRow:hover { background-color: #2A2B36; }
    """)

    row = QHBoxLayout(frame)
    row.setContentsMargins(16, 12, 16, 12)
    row.setSpacing(14)

    icon_label = QLabel(icon)
    icon_label.setFont(QFont("Microsoft YaHei UI", 20))
    icon_label.setStyleSheet("background: transparent; border: none;")
    icon_label.setFixedWidth(32)
    icon_label.setAlignment(Qt.AlignCenter)

    text_block = QVBoxLayout()
    text_block.setSpacing(2)
    title_label = QLabel(title)
    title_label.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
    title_label.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
    desc_label = QLabel(desc)
    desc_label.setStyleSheet("color: #94A3B8; font-size: 12px; background: transparent; border: none;")
    desc_label.setWordWrap(True)
    text_block.addWidget(title_label)
    text_block.addWidget(desc_label)

    # 用 checkable QPushButton 模拟 radio(配合 setAutoExclusive)
    radio_btn = QPushButton("选择")
    radio_btn.setCheckable(True)
    radio_btn.setAutoExclusive(True)  # 同组互斥
    radio_btn.setCursor(Qt.PointingHandCursor)
    radio_btn.setFixedSize(80, 36)
    radio_btn.setStyleSheet("""
        QPushButton {
            background-color: #3D3E4A;
            color: #CBD5E1;
            border: 1px solid #2D2E3A;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
        }
        QPushButton:hover { background-color: #4D4E5A; color: #F8FAFC; }
        QPushButton:checked {
            background-color: #3B82F6;
            color: white;
            border: 1px solid #3B82F6;
        }
        QPushButton:checked:hover { background-color: #2563EB; }
    """)

    row.addWidget(icon_label)
    row.addLayout(text_block, 1)
    row.addWidget(radio_btn)

    return {"frame": frame, "btn": radio_btn}


def _prompt_for_cdk(parent) -> Optional[str]:
    """弹子对话框让用户输入 CDK。返回 None=取消,str=CDK(可能为空)"""
    cdk, ok = QInputDialog.getText(
        parent,
        "输入 Mirror 酱 CDK",
        "请输入 CDK（明文存储在本地 config.json）:",
        QLineEdit.EchoMode.Password,
    )
    if not ok:
        return None
    return cdk.strip() if cdk else ""


def show_update_dialog(info: UpdateInfo, parent=None) -> bool:
    """显示更新页面。

    Returns: True 表示用户点了立即更新;False 表示取消/关闭
    """
    page = QDialog(parent)
    page.setWindowTitle(f"发现新版本 v{info.version}")
    page.resize(720, 600)
    page.setMinimumSize(560, 460)
    page.setStyleSheet("QDialog { background-color: #1A1B21; }")

    root_layout = QVBoxLayout(page)
    root_layout.setSpacing(0)
    root_layout.setContentsMargins(0, 0, 0, 0)

    # ============= 顶部 toolbar =============
    toolbar = QFrame()
    toolbar.setFixedHeight(78)
    toolbar.setStyleSheet("""
        QFrame {
            background-color: #181920;
            border-bottom: 1px solid #2D2E3A;
        }
    """)
    tb_layout = QHBoxLayout(toolbar)
    tb_layout.setContentsMargins(24, 14, 18, 14)
    tb_layout.setSpacing(12)

    title_block = QVBoxLayout()
    title_block.setSpacing(3)
    title_label = QLabel(f"发现新版本 v{info.version}")
    title_label.setFont(QFont("Microsoft YaHei UI", 17, QFont.Bold))
    title_label.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")

    local_ver = get_local_version() or "未知"
    size_mb = info.size / 1024 / 1024 if info.size else 0
    published = _format_published_at(getattr(info, "published_at", "") or "")
    sub_parts = [f"当前 v{local_ver}", f"新版 v{info.version}", f"{size_mb:.1f} MB"]
    if published:
        sub_parts.append(f"发布于 {published}")
    subtitle = QLabel("  ·  ".join(sub_parts))
    subtitle.setStyleSheet(
        "color: #94A3B8; font-size: 12px; background: transparent; border: none;"
    )
    title_block.addWidget(title_label)
    title_block.addWidget(subtitle)

    close_btn = QPushButton("✕")
    close_btn.setFixedSize(32, 32)
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: #94A3B8;
            border: none;
            border-radius: 6px;
            font-size: 15px;
        }
        QPushButton:hover { background-color: #2D2E3A; color: #F8FAFC; }
    """)
    close_btn.clicked.connect(page.reject)

    tb_layout.addLayout(title_block)
    tb_layout.addStretch()
    tb_layout.addWidget(close_btn)
    root_layout.addWidget(toolbar)

    # ============= 中间 scrollable 内容 =============
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("""
        QScrollArea { background-color: #1A1B21; border: none; }
        QScrollBar:vertical {
            background-color: #1A1B21;
            width: 10px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background-color: #3D3E4A;
            border-radius: 5px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover { background-color: #4D4E5A; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    """)

    content = QWidget()
    content.setStyleSheet("background-color: #1A1B21;")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(24, 20, 24, 20)
    content_layout.setSpacing(18)

    # ----- Release note 卡片 -----
    note_card = QFrame()
    note_card.setStyleSheet("""
        QFrame {
            background-color: #181920;
            border: 1px solid #2D2E3A;
            border-radius: 12px;
        }
    """)
    note_layout = QVBoxLayout(note_card)
    note_layout.setContentsMargins(22, 18, 22, 18)
    note_layout.setSpacing(12)

    note_header = QLabel("📋  更新内容")
    note_header.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    note_header.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
    note_layout.addWidget(note_header)

    note_browser = QTextBrowser()
    note_browser.setOpenExternalLinks(True)
    note_browser.setStyleSheet("""
        QTextBrowser {
            background-color: transparent;
            color: #CBD5E1;
            border: none;
            font-size: 13px;
            padding: 0;
        }
    """)
    note_browser.setMinimumHeight(160)

    raw_body = (info.body or "").strip()
    if not raw_body:
        note_browser.setHtml(
            '<div style="color: #64748B; padding: 20px 0; text-align: center;">'
            "(此版本没有提供更新说明)"
            "</div>"
        )
    else:
        note_browser.setHtml(_render_markdown(raw_body))

    note_layout.addWidget(note_browser)
    content_layout.addWidget(note_card)

    # ----- 下载通道说明卡片(kachina 自己处理选择,主程序只显示提示) -----
    channel_card = QFrame()
    channel_card.setStyleSheet("""
        QFrame {
            background-color: #181920;
            border: 1px solid #2D2E3A;
            border-radius: 12px;
        }
    """)
    ch_layout = QVBoxLayout(channel_card)
    ch_layout.setContentsMargins(22, 18, 22, 18)
    ch_layout.setSpacing(10)

    ch_title = QLabel("🚀  下载通道")
    ch_title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    ch_title.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
    ch_layout.addWidget(ch_title)

    # 列出当前可用的下载源(kachina 启动后会让用户在它自己 UI 里选)
    try:
        from core.config import get_global_setting
        mirror_enabled = bool(get_global_setting("mirror_chyan_enabled"))
        mirror_has_cdk = bool((get_global_setting("mirror_chyan_cdk") or "").strip())
    except Exception:
        mirror_enabled = False
        mirror_has_cdk = False

    try:
        from core.cloudflare_r2 import is_enabled as r2_enabled
    except Exception:
        r2_enabled = False

    available_sources = [
        "🐙  GitHub — 官方源,直连 release",
    ]
    if mirror_enabled and mirror_has_cdk:
        available_sources.append("🪞  Mirror 酱 — 国内加速(需 CDK)")
    if r2_enabled:
        available_sources.append("☁️  Cloudflare R2 — 国内加速(免费)")

    for src in available_sources:
        lbl = QLabel(src)
        lbl.setStyleSheet("color: #CBD5E1; font-size: 13px; background: transparent; border: none;")
        ch_layout.addWidget(lbl)

    channel_hint = QLabel(
        "💡  点击「立即更新」后,kachina 安装器会启动,\n"
        "    你可以在它自己的窗口里选择下载通道。"
    )
    channel_hint.setStyleSheet(
        "color: #64748B; font-size: 11px; background: transparent; border: none; line-height: 1.5;"
    )
    channel_hint.setWordWrap(True)
    ch_layout.addWidget(channel_hint)

    content_layout.addWidget(channel_card)
    content_layout.addStretch()
    scroll.setWidget(content)
    root_layout.addWidget(scroll, 1)

    # ============= 底部按钮区 =============
    bottom = QFrame()
    bottom.setFixedHeight(72)
    bottom.setStyleSheet("""
        QFrame {
            background-color: #181920;
            border-top: 1px solid #2D2E3A;
        }
    """)
    bottom_layout = QHBoxLayout(bottom)
    bottom_layout.setContentsMargins(24, 16, 24, 16)
    bottom_layout.setSpacing(12)

    cancel_btn = QPushButton("稍后再说")
    cancel_btn.setFixedHeight(40)
    cancel_btn.setMinimumWidth(110)
    cancel_btn.setCursor(Qt.PointingHandCursor)
    cancel_btn.setStyleSheet("""
        QPushButton {
            background-color: #252631;
            color: #94A3B8;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
        }
        QPushButton:hover { background-color: #2D2E3A; color: #E2E8F0; }
    """)
    cancel_btn.clicked.connect(page.reject)

    update_btn = QPushButton("立即更新")
    update_btn.setFixedHeight(40)
    update_btn.setMinimumWidth(140)
    update_btn.setCursor(Qt.PointingHandCursor)
    update_btn.setDefault(True)  # Enter 触发
    update_btn.setStyleSheet("""
        QPushButton {
            background-color: #3B82F6;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #2563EB; }
        QPushButton:disabled { background-color: #1F2937; color: #64748B; }
    """)

    bottom_layout.addWidget(cancel_btn)
    bottom_layout.addStretch()
    bottom_layout.addWidget(update_btn)

    root_layout.addWidget(bottom)

    # ============= 立即更新逻辑 =============
    def _do_update():
        # kachina 自己处理多 source 选择 —— 主程序不参与,直接 spawn update.exe
        # 记住用户最后选了哪个(用于将来 mirror 酱 / R2 选 CDK 时的默认行为等)
        try:
            from core.config import set_global_setting
            # 没有 UI 选项,记个默认值
            set_global_setting("update_channel", "github")
        except Exception as e:
            logger.warning(f"保存 update_channel 失败: {e}")

        # 启动 kachina update.exe
        update_btn.setEnabled(False)
        cancel_btn.setEnabled(False)
        update_btn.setText("正在启动安装器...")
        try:
            from core.portable_updater import launch_kachina_update, get_app_dir
            app_dir = get_app_dir()
            # launch_kachina_update 内部会 os._exit(0),执行不到这里
            launch_kachina_update(app_dir)
        except Exception as e:
            logger.error(f"启动 kachina update 失败: {e}")
            update_btn.setEnabled(True)
            cancel_btn.setEnabled(True)
            update_btn.setText("重试")
            cancel_btn.setText(f"启动安装器失败:{str(e)[:60]}")

    update_btn.clicked.connect(_do_update)

    return page.exec() == QDialog.Accepted


# ==================== 兼容层 ====================
def check_and_update(progress_callback=None) -> Optional[UpdateInfo]:
    """兼容旧名"""
    from core.updater import check_update
    return check_update()


def get_local_version() -> Optional[str]:
    """兼容旧名 - 代理到 core.updater.get_local_version"""
    from core.updater import get_local_version as _impl
    return _impl()


def set_local_version(version: str):
    """兼容旧名"""
    logger.info(f"set_local_version({version}) 已弃用")


def parse_version_from_filename(filename: str) -> Optional[str]:
    """兼容旧名"""
    import re
    m = re.search(r'[_\-]v?(\d+\.\d+\.\d+)', filename, re.IGNORECASE)
    return m.group(1) if m else None