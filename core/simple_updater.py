"""
更新页面 UI（kachina 模式）

页面结构（自上而下）:
  1. 顶部 toolbar    — 版本号 + 大小 + 发布时间 + 关闭按钮
  2. 中间 scrollable — release note (Markdown 渲染) + 通道选择（GitHub / CNB）
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
    QScrollArea, QWidget, QFrame,
    QTextBrowser,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.updater import UpdateInfo, get_local_version

logger = logging.getLogger(__name__)


# ==================== Markdown 渲染 CSS ====================
def _markdown_css(tokens) -> str:
    return f"""
    h1, h2, h3, h4 {{ color: {tokens['text_primary']}; margin-top: 14px; margin-bottom: 8px; font-weight: 600; }}
    h1 {{ font-size: 20px; }}
    h2 {{ font-size: 17px; border-bottom: 1px solid {tokens['border']}; padding-bottom: 6px; }}
    h3 {{ font-size: 15px; }}
    h4 {{ font-size: 14px; color: {tokens['text']}; }}
    p {{ color: {tokens['text']}; margin: 8px 0; line-height: 1.7; }}
    ul, ol {{ margin: 8px 0; padding-left: 24px; }}
    li {{ color: {tokens['text']}; margin: 4px 0; line-height: 1.6; }}
    code {{
        background-color: {tokens['surface_code']};
        color: {tokens['warning']};
        padding: 1px 6px;
        border-radius: 3px;
        font-family: 'Consolas', 'Cascadia Code', monospace;
        font-size: 12px;
    }}
    pre {{
        background-color: {tokens['surface_code']};
        color: {tokens['text']};
        padding: 12px 14px;
        border-radius: 6px;
        border: 1px solid {tokens['border']};
        font-family: 'Consolas', 'Cascadia Code', monospace;
        font-size: 12px;
        line-height: 1.5;
    }}
    pre code {{ background: transparent; padding: 0; color: inherit; }}
    a {{ color: {tokens['primary']}; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    blockquote {{
        color: {tokens['text_secondary']};
        border-left: 3px solid {tokens['primary']};
        padding-left: 14px;
        margin: 10px 0;
    }}
    hr {{ color: {tokens['border']}; background-color: {tokens['border']}; border: none; max-height: 1px; margin: 16px 0; }}
    table {{ border-collapse: collapse; margin: 10px 0; }}
    th, td {{ border: 1px solid {tokens['border']}; padding: 6px 12px; text-align: left; color: {tokens['text']}; }}
    th {{ background-color: {tokens['surface_hover']}; color: {tokens['text_primary']}; }}
    img {{ max-width: 100%; border-radius: 6px; }}
    strong {{ color: {tokens['text_primary']}; font-weight: 600; }}
    em {{ color: {tokens['text']}; }}
    """


def _render_markdown(text: str) -> str:
    """渲染 markdown → HTML（跟随当前应用主题）。失败时返回纯文本。"""
    if not text or not text.strip():
        return ""
    from ui.theme import theme_manager
    tokens = theme_manager().tokens
    try:
        import markdown as md_lib
        html = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        return f'<style>{_markdown_css(tokens)}</style>{html}'
    except Exception as e:
        logger.warning(f"Markdown 渲染失败,降级纯文本: {e}")
        escaped = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
        )
        return f'<div style="color: {tokens["text"]}; line-height: 1.6;">{escaped}</div>'


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


def _make_channel_row(icon: str, title: str, desc: str, source_id: str) -> dict:
    """构造一个通道行:左边 icon + 中间 title/desc + 右边 checkable 按钮(单选)。
    多个 row 用 QButtonGroup 互斥,实现"二选一"。

    返回 {'frame': QFrame, 'btn': QPushButton, 'source_id': str}
    """
    frame = QFrame()
    frame.setObjectName("updateChannelRow")

    row = QHBoxLayout(frame)
    row.setContentsMargins(16, 12, 16, 12)
    row.setSpacing(14)

    icon_label = QLabel(icon)
    icon_label.setObjectName("updateChannelIcon")
    icon_label.setFont(QFont("Microsoft YaHei UI", 20))
    icon_label.setStyleSheet("QLabel#updateChannelIcon { background: transparent; border: none; }")
    icon_label.setFixedWidth(32)
    icon_label.setAlignment(Qt.AlignCenter)

    text_block = QVBoxLayout()
    text_block.setSpacing(2)
    title_label = QLabel(title)
    title_label.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
    title_label.setProperty("role", "title")
    desc_label = QLabel(desc)
    desc_label.setProperty("role", "secondary")
    desc_label.setStyleSheet("font-size: 12px;")
    desc_label.setWordWrap(True)
    text_block.addWidget(title_label)
    text_block.addWidget(desc_label)

    # 单选按钮(配合 QButtonGroup.setExclusive 互斥,这样跨 row 互斥)
    radio_btn = QPushButton("选择")
    radio_btn.setCheckable(True)
    radio_btn.setProperty("variant", "channelChoice")
    radio_btn.setCursor(Qt.PointingHandCursor)
    radio_btn.setFixedSize(80, 36)

    row.addWidget(icon_label)
    row.addLayout(text_block, 1)
    row.addWidget(radio_btn)

    return {"frame": frame, "btn": radio_btn, "source_id": source_id}


# 用户可见的下载通道元数据(id 必须跟 kachina.config.json 的 source[].id 对应)
# key 是 kachina source id,value 是 UI 展示信息(title/desc/icon)
CHANNEL_DISPLAY = {
    "github": {
        "icon": "🐙",
        "title": "GitHub",
        "desc": "官方源,直连 GitHub release,海外/有代理选这个",
    },
    "cnb": {
        "icon": "🇨🇳",
        "title": "CNB",
        "desc": "国内加速(腾讯节点),国内网络选这个更快",
    },
}


def show_update_dialog(info: UpdateInfo, parent=None) -> bool:
    """显示更新页面。

    Returns: True 表示用户点了立即更新;False 表示取消/关闭
    """
    page = QDialog(parent)
    page.setObjectName("updateDialog")
    page.setWindowTitle(f"发现新版本 v{info.version}")
    page.resize(720, 600)
    page.setMinimumSize(560, 460)

    root_layout = QVBoxLayout(page)
    root_layout.setSpacing(0)
    root_layout.setContentsMargins(0, 0, 0, 0)

    # ============= 顶部 toolbar =============
    toolbar = QFrame()
    toolbar.setObjectName("updateToolbar")
    toolbar.setFixedHeight(78)
    tb_layout = QHBoxLayout(toolbar)
    tb_layout.setContentsMargins(24, 14, 18, 14)
    tb_layout.setSpacing(12)

    title_block = QVBoxLayout()
    title_block.setSpacing(3)
    title_label = QLabel(f"发现新版本 v{info.version}")
    title_label.setFont(QFont("Microsoft YaHei UI", 17, QFont.Bold))
    title_label.setProperty("role", "title")

    local_ver = get_local_version() or "未知"
    size_mb = info.size / 1024 / 1024 if info.size else 0
    published = _format_published_at(getattr(info, "published_at", "") or "")
    sub_parts = [f"当前 v{local_ver}", f"新版 v{info.version}", f"{size_mb:.1f} MB"]
    if published:
        sub_parts.append(f"发布于 {published}")
    subtitle = QLabel("  ·  ".join(sub_parts))
    subtitle.setProperty("role", "secondary")
    subtitle.setStyleSheet("font-size: 12px;")
    title_block.addWidget(title_label)
    title_block.addWidget(subtitle)

    close_btn = QPushButton("×")
    close_btn.setFixedSize(32, 32)
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setProperty("variant", "close")
    close_btn.clicked.connect(page.reject)

    tb_layout.addLayout(title_block)
    tb_layout.addStretch()
    tb_layout.addWidget(close_btn)
    root_layout.addWidget(toolbar)

    # ============= 中间 scrollable 内容 =============
    scroll = QScrollArea()
    scroll.setObjectName("updateScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)

    content = QWidget()
    content.setObjectName("updateContent")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(24, 20, 24, 20)
    content_layout.setSpacing(18)

    # ----- Release note 卡片 -----
    note_card = QFrame()
    note_card.setObjectName("updateCard")
    note_layout = QVBoxLayout(note_card)
    note_layout.setContentsMargins(22, 18, 22, 18)
    note_layout.setSpacing(12)

    note_header = QLabel("📋  更新内容")
    note_header.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    note_header.setProperty("role", "title")
    note_layout.addWidget(note_header)

    note_browser = QTextBrowser()
    note_browser.setObjectName("updateNotes")
    note_browser.setOpenExternalLinks(True)
    note_browser.setStyleSheet(
        "QTextBrowser#updateNotes { font-size: 13px; padding: 0; border: none; background: transparent; }"
    )
    note_browser.setMinimumHeight(160)

    raw_body = (info.body or "").strip()
    if not raw_body:
        from ui.theme import theme_manager
        note_browser.setHtml(
            f'<div style="color: {theme_manager().tokens["text_muted"]}; padding: 20px 0; text-align: center;">'
            "(此版本没有提供更新说明)"
            "</div>"
        )
    else:
        note_browser.setHtml(_render_markdown(raw_body))

    note_layout.addWidget(note_browser)
    content_layout.addWidget(note_card)

    # ----- 下载通道卡片:GitHub / CNB 二选一 -----
    # 通道列表硬编码在这里,不用再去解析 kachina.config.json:
    # - frozen 模式下 sys.executable 的临时目录猜不到项目根,解析不稳定
    # - 通道就这两个,加新通道时改这里 + kachina.config.json 两处即可
    channel_card = QFrame()
    channel_card.setObjectName("updateCard")
    ch_layout = QVBoxLayout(channel_card)
    ch_layout.setContentsMargins(22, 18, 22, 18)
    ch_layout.setSpacing(10)

    ch_title = QLabel("🚀  下载通道")
    ch_title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    ch_title.setProperty("role", "title")
    ch_layout.addWidget(ch_title)

    # 默认勾选 = 上次用户选择(持久化在 config.update_channel),否则 github
    try:
        from core.config import get_global_setting
        default_channel = get_global_setting("update_channel") or "github"
    except Exception:
        default_channel = "github"
    if default_channel not in CHANNEL_DISPLAY:
        default_channel = "github"

    # 跨 row 互斥:QButtonGroup(exclusive=True)
    from PySide6.QtWidgets import QButtonGroup
    channel_group = QButtonGroup(page)
    channel_group.setExclusive(True)

    channel_rows: dict = {}  # source_id -> dict(包含 btn)
    for idx, (sid, meta) in enumerate(CHANNEL_DISPLAY.items()):
        row = _make_channel_row(meta["icon"], meta["title"], meta["desc"], sid)
        ch_layout.addWidget(row["frame"])
        channel_group.addButton(row["btn"], idx)
        row["btn"].setProperty("source_id", sid)
        channel_rows[sid] = row
        if sid == default_channel:
            row["btn"].setChecked(True)
            row["btn"].setText("已选")

    # 选中态切换:其他按钮恢复"选择"文案
    def _on_channel_changed(checked_btn):
        for sid, r in channel_rows.items():
            btn = r["btn"]
            btn.setText("已选" if btn is checked_btn and btn.isChecked() else "选择")
    channel_group.buttonClicked.connect(_on_channel_changed)

    channel_hint = QLabel(
        "💡  选择通道后点「立即更新」,安装器会自动用对应通道下载,\n"
        "    不会再让你在安装器里选一次。"
    )
    channel_hint.setProperty("role", "muted")
    channel_hint.setStyleSheet("font-size: 11px; line-height: 1.5;")
    channel_hint.setWordWrap(True)
    ch_layout.addWidget(channel_hint)

    content_layout.addWidget(channel_card)
    content_layout.addStretch()
    scroll.setWidget(content)
    root_layout.addWidget(scroll, 1)

    # ============= 底部按钮区 =============
    bottom = QFrame()
    bottom.setObjectName("updateBottom")
    bottom.setFixedHeight(72)
    bottom_layout = QHBoxLayout(bottom)
    bottom_layout.setContentsMargins(24, 16, 24, 16)
    bottom_layout.setSpacing(12)

    cancel_btn = QPushButton("稍后再说")
    cancel_btn.setFixedHeight(40)
    cancel_btn.setMinimumWidth(110)
    cancel_btn.setCursor(Qt.PointingHandCursor)
    cancel_btn.setProperty("variant", "secondary")
    cancel_btn.clicked.connect(page.reject)

    update_btn = QPushButton("立即更新")
    update_btn.setFixedHeight(40)
    update_btn.setMinimumWidth(140)
    update_btn.setCursor(Qt.PointingHandCursor)
    update_btn.setDefault(True)
    update_btn.setProperty("variant", "primary")

    bottom_layout.addWidget(cancel_btn)
    bottom_layout.addStretch()
    bottom_layout.addWidget(update_btn)

    root_layout.addWidget(bottom)

    # ============= 立即更新逻辑 =============
    def _do_update():
        # 找出当前勾选的通道(只可能有一个,因 QButtonGroup exclusive)
        selected_id = default_channel
        for sid, r in channel_rows.items():
            if r["btn"].isChecked():
                selected_id = sid
                break

        # 把用户这次的选写回 config.json(下次更新弹窗默认勾这个)
        try:
            from core.config import set_global_setting
            set_global_setting("update_channel", selected_id)
        except Exception as e:
            logger.warning(f"保存 update_channel 失败: {e}")

        # 启动 kachina update.exe,把通道作为 --source 参数传过去
        # kachina 收到 --source 后,会在自己 UI 里跳过"选通道"那步,直接用对应源下载
        update_btn.setEnabled(False)
        cancel_btn.setEnabled(False)
        update_btn.setText("正在启动安装器...")
        try:
            from core.portable_updater import launch_kachina_update, get_app_dir
            app_dir = get_app_dir()
            # launch_kachina_update 内部会 os._exit(0),执行不到这里
            launch_kachina_update(app_dir, source_id=selected_id)
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