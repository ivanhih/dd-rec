# ui/theme.py
"""应用主题：深/浅两套语义 token、应用级 QSS/QPalette 与 ThemeManager。

约定：
- 配置层仍使用中文展示字符串（"深色" / "浅色"），本模块负责规范化与呈现。
- ThemeManager 只改呈现，不写配置；持久化由 core.config 负责。
- 局部 setStyleSheet 只保留几何/字号；颜色统一由这里生成的应用 QSS 控制。
"""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK_THEME = "深色"
LIGHT_THEME = "浅色"

DARK_TOKENS = {
    # 表面：三档色度
    # 1) window 页面底  2) toolbar/panel 中间层  3) card 前景卡片
    "window": "#0F0F13",
    "sidebar": "#16171E",
    "surface": "#262836",
    "panel": "#1A1B24",
    "toolbar": "#1A1B24",
    "card": "#262836",
    "card_hover": "#2E3040",
    "surface_raised": "#2E3040",
    # 搜索框：相对中间层明显抬起，不再贴窗
    "surface_input": "#323447",
    "surface_code": "#14151D",
    "surface_hover": "#35374A",
    "surface_pressed": "#3E4054",
    # 工具栏按钮：芯片色，明显区别于 toolbar
    "control": "#303246",
    "control_hover": "#3A3C52",
    "control_pressed": "#44465C",
    "control_border": "#4B4D63",
    # 边框
    "border": "#3A3C50",
    "border_subtle": "#2C2E3C",
    "border_strong": "#4F5168",
    "card_border": "#3F4156",
    "card_border_hover": "#55576E",
    "focus": "#3B82F6",
    # 文字
    "text_primary": "#F8FAFC",
    "text": "#E2E8F0",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "text_on_accent": "#FFFFFF",
    "placeholder": "#64748B",
    # 强调
    "primary": "#3B82F6",
    "primary_hover": "#2563EB",
    "primary_disabled_bg": "#252631",
    "primary_disabled_text": "#64748B",
    # 交互
    "selection": "#3B82F6",
    "scroll_track": "#0F0F13",
    "scroll_handle": "#2D2E3A",
    "scroll_handle_hover": "#3D3E4F",
    "tooltip_background": "#1F2030",
    "tooltip_border": "#3D3E4F",
    "tooltip_text": "#FFFFFF",
    "overlay_scrim": "rgba(0, 0, 0, 0.6)",
    # 开关
    "toggle_on": "#4ADE80",
    "toggle_off": "#373A48",
    "toggle_thumb": "#FFFFFF",
    # 状态
    "success": "#4ADE80",
    "info": "#3B82F6",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    # 通知
    "notify_success_bg": "#12241A",
    "notify_success_border": "#1E4732",
    "notify_success_title": "#4ADE80",
    "notify_info_bg": "#131C2B",
    "notify_info_border": "#1E3A5F",
    "notify_info_title": "#60A5FA",
    "notify_error_bg": "#2B1618",
    "notify_error_border": "#5F1E22",
    "notify_error_title": "#F87171",
    "notify_warning_bg": "#2A2210",
    "notify_warning_border": "#5C4A1A",
    "notify_warning_title": "#FBBF24",
    "notify_crash_bg": "#24182B",
    "notify_crash_border": "#4A2A5F",
    "notify_crash_title": "#C084FC",
    # Room Card 操作按钮
    "action_folder_hover": "#2E2A1A",
    "action_folder_pressed": "#4A3A2A",
    "action_cut_hover": "#2A1A2E",
    "action_cut_pressed": "#3A2A3E",
    "action_delete_hover": "#2E1A1E",
    "action_delete_pressed": "#3E1A2E",
    "action_settings_hover": "#1A2E20",
    "action_settings_pressed": "#2A3E30",
}

LIGHT_TOKENS = {
    # 表面：三档色度
    # 1) window 页面底  2) toolbar/panel 中间层  3) card 前景卡片
    "window": "#C2CAD9",
    "sidebar": "#D7DEEB",
    "surface": "#FFFFFF",
    "panel": "#E4EAF5",
    "toolbar": "#E4EAF5",
    "card": "#FFFFFF",
    "card_hover": "#F7F9FC",
    "surface_raised": "#FFFFFF",
    # 搜索框：近白控件 + 强边框，相对中间层清晰浮起；与纯白卡片略分
    "surface_input": "#F5F7FC",
    "surface_code": "#D5DCEC",
    "surface_hover": "#F1F5FB",
    "surface_pressed": "#DCE3EF",
    # 工具栏按钮：白芯片 + 深边框
    "control": "#FFFFFF",
    "control_hover": "#F8FAFC",
    "control_pressed": "#E8EEF8",
    "control_border": "#7F8CA3",
    # 边框
    "border": "#9AA7BC",
    "border_subtle": "#B0BBCD",
    "border_strong": "#7F8CA3",
    "card_border": "#8F9CB1",
    "card_border_hover": "#718099",
    "focus": "#3B82F6",
    # 文字
    "text_primary": "#0F172A",
    "text": "#1E293B",
    "text_secondary": "#475569",
    "text_muted": "#64748B",
    "text_on_accent": "#FFFFFF",
    "placeholder": "#94A3B8",
    # 强调
    "primary": "#3B82F6",
    "primary_hover": "#2563EB",
    "primary_disabled_bg": "#E2E8F0",
    "primary_disabled_text": "#94A3B8",
    # 交互
    "selection": "#3B82F6",
    "scroll_track": "#C2CAD9",
    "scroll_handle": "#9AA7BC",
    "scroll_handle_hover": "#7F8CA3",
    "tooltip_background": "#1E293B",
    "tooltip_border": "#334155",
    "tooltip_text": "#F8FAFC",
    "overlay_scrim": "rgba(15, 23, 42, 0.35)",
    # 开关
    "toggle_on": "#22C55E",
    "toggle_off": "#B4BECF",
    "toggle_thumb": "#FFFFFF",
    # 状态
    "success": "#16A34A",
    "info": "#2563EB",
    "warning": "#D97706",
    "danger": "#DC2626",
    # 通知
    "notify_success_bg": "#F0FDF4",
    "notify_success_border": "#BBF7D0",
    "notify_success_title": "#16A34A",
    "notify_info_bg": "#EFF6FF",
    "notify_info_border": "#BFDBFE",
    "notify_info_title": "#2563EB",
    "notify_error_bg": "#FEF2F2",
    "notify_error_border": "#FECACA",
    "notify_error_title": "#DC2626",
    "notify_warning_bg": "#FFFBEB",
    "notify_warning_border": "#FDE68A",
    "notify_warning_title": "#D97706",
    "notify_crash_bg": "#FAF5FF",
    "notify_crash_border": "#E9D5FF",
    "notify_crash_title": "#9333EA",
    # Room Card 操作按钮
    "action_folder_hover": "#FEF3C7",
    "action_folder_pressed": "#FDE68A",
    "action_cut_hover": "#EDE9FE",
    "action_cut_pressed": "#DDD6FE",
    "action_delete_hover": "#FEE2E2",
    "action_delete_pressed": "#FECACA",
    "action_settings_hover": "#DCFCE7",
    "action_settings_pressed": "#BBF7D0",
}

THEMES = {
    DARK_THEME: DARK_TOKENS,
    LIGHT_THEME: LIGHT_TOKENS,
}


def normalize_theme(value) -> str:
    """把配置里的主题值规范化为受支持的主题名；未知/缺失一律回退深色。"""
    if value == LIGHT_THEME:
        return LIGHT_THEME
    return DARK_THEME


def build_palette(tokens) -> QPalette:
    palette = QPalette()
    window = QColor(tokens["window"])
    base = QColor(tokens["surface_input"])
    text = QColor(tokens["text"])
    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, QColor(tokens["text_primary"]))
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, QColor(tokens["surface"]))
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, QColor(tokens["surface"]))
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.Highlight, QColor(tokens["selection"]))
    palette.setColor(QPalette.HighlightedText, QColor(tokens["text_on_accent"]))
    palette.setColor(QPalette.ToolTipBase, QColor(tokens["tooltip_background"]))
    palette.setColor(QPalette.ToolTipText, QColor(tokens["tooltip_text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(tokens["placeholder"]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(tokens["text_muted"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(tokens["text_muted"]))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(tokens["text_muted"]))
    return palette


def build_stylesheet(tokens) -> str:
    t = tokens
    return f"""
/* ===== 顶层表面 ===== */
QMainWindow, QDialog {{
    background-color: {t['window']};
}}
QWidget#appRoot {{
    background-color: {t['window']};
}}
QWidget#sidebar {{
    background-color: {t['sidebar']};
}}
QFrame#roomCard {{
    background-color: {t['card']};
    border: 1px solid {t['card_border']};
    border-radius: 14px;
}}
QFrame#roomCard:hover {{
    background-color: {t['card_hover']};
    border: 1px solid {t['card_border_hover']};
}}
QFrame#settingsCard {{
    background-color: {t['panel']};
    border: 1px solid {t['border']};
    border-radius: 14px;
}}
QFrame#aboutCard {{
    background-color: {t['panel']};
    border: 1px solid {t['border_subtle']};
    border-radius: 14px;
}}
QFrame#channelsToolbar {{
    background-color: {t['toolbar']};
    border: 1px solid {t['border']};
    border-radius: 14px;
}}
QFrame#channelsToolbar QLineEdit,
QFrame#channelsToolbar QLineEdit#channelSearch {{
    background-color: {t['surface_input']};
    border: 1px solid {t['control_border']};
    border-radius: 10px;
}}
QFrame#channelsToolbar QLineEdit:hover,
QFrame#channelsToolbar QLineEdit#channelSearch:hover {{
    border: 1px solid {t['border_strong']};
}}
QFrame#channelsToolbar QLineEdit:focus,
QFrame#channelsToolbar QLineEdit#channelSearch:focus {{
    border: 1px solid {t['focus']};
}}
QLabel#roomAvatar {{
    border-radius: 32px;
    background-color: {t['surface_hover']};
    border: none;
}}
QLabel#roomTag {{
    background-color: transparent;
    padding: 3px 12px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
}}
QLabel#roomTag[accentColor="#3B82F6"] {{ color: #3B82F6; border: 1px solid #3B82F6; }}
QLabel#roomTag[accentColor="#7C3AED"] {{ color: #7C3AED; border: 1px solid #7C3AED; }}
QLabel#roomTag[accentColor="#A855F7"] {{ color: #A855F7; border: 1px solid #A855F7; }}
QFrame#roomCard QToolButton[roomAction] {{
    background-color: transparent;
    border-radius: 10px;
    font-size: 18px;
    border: none;
}}
QFrame#roomCard QToolButton[roomAction="folder"] {{ color: {t['warning']}; }}
QFrame#roomCard QToolButton[roomAction="folder"]:hover {{ background-color: {t['action_folder_hover']}; }}
QFrame#roomCard QToolButton[roomAction="folder"]:pressed {{ background-color: {t['action_folder_pressed']}; }}
QFrame#roomCard QToolButton[roomAction="cut"] {{ color: #8B5CF6; }}
QFrame#roomCard QToolButton[roomAction="cut"]:hover {{ background-color: {t['action_cut_hover']}; }}
QFrame#roomCard QToolButton[roomAction="cut"]:pressed {{ background-color: {t['action_cut_pressed']}; }}
QFrame#roomCard QToolButton[roomAction="delete"] {{ color: {t['danger']}; }}
QFrame#roomCard QToolButton[roomAction="delete"]:hover {{ background-color: {t['action_delete_hover']}; }}
QFrame#roomCard QToolButton[roomAction="delete"]:pressed {{ background-color: {t['action_delete_pressed']}; }}
QFrame#roomCard QToolButton[roomAction="settings"] {{ color: {t['success']}; }}
QFrame#roomCard QToolButton[roomAction="settings"]:hover {{ background-color: {t['action_settings_hover']}; }}
QFrame#roomCard QToolButton[roomAction="settings"]:pressed {{ background-color: {t['action_settings_pressed']}; }}
QFrame#roomCard QToolButton[roomAction]:disabled {{
    color: {t['text_muted']};
    background-color: transparent;
}}
QWidget#startupOverlay {{
    background-color: {t['window']};
}}
QWidget#startupProgressTrack {{
    background-color: {t['surface_pressed']};
    border-radius: 4px;
}}
QWidget#startupProgressFill {{
    background-color: {t['primary']};
    border-radius: 4px;
}}
QPlainTextEdit#logTextView {{
    background-color: {t['surface_code']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 8px;
}}
QDialog#updateDialog, QWidget#updateContent, QScrollArea#updateScroll {{
    background-color: {t['window']};
}}
QFrame#updateToolbar, QFrame#updateBottom {{
    background-color: {t['panel']};
    border: none;
}}
QFrame#updateToolbar {{ border-bottom: 1px solid {t['border']}; }}
QFrame#updateBottom {{ border-top: 1px solid {t['border']}; }}
QFrame#updateCard {{
    background-color: {t['card']};
    border: 1px solid {t['card_border']};
    border-radius: 12px;
}}
QFrame#updateChannelRow {{
    background-color: {t['surface_hover']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
QFrame#updateChannelRow:hover {{ background-color: {t['surface_pressed']}; }}
QPushButton[variant="channelChoice"] {{
    background-color: {t['surface_pressed']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    padding: 0 12px;
    min-height: 30px;
    min-width: 56px;
}}
QPushButton[variant="channelChoice"]:hover {{
    background-color: {t['surface_hover']};
    color: {t['text_primary']};
}}
QPushButton[variant="channelChoice"]:checked {{
    background-color: {t['primary']};
    color: {t['text_on_accent']};
    border-color: {t['primary']};
}}
QPushButton[variant="channelChoice"]:checked:hover {{ background-color: {t['primary_hover']}; }}
/* 日志筛选：未选中用浅底+色字，选中用实色底 */
QPushButton[variant="channelChoice"][filterTone="info"] {{
    color: {t['notify_info_title']};
    border-color: {t['notify_info_border']};
    background-color: {t['notify_info_bg']};
}}
QPushButton[variant="channelChoice"][filterTone="info"]:hover {{
    color: {t['info']};
    border-color: {t['info']};
}}
QPushButton[variant="channelChoice"][filterTone="info"]:checked,
QPushButton[variant="channelChoice"][filterTone="info"]:checked:hover {{
    background-color: {t['info']};
    color: {t['text_on_accent']};
    border-color: {t['info']};
}}
QPushButton[variant="channelChoice"][filterTone="warning"] {{
    color: {t['notify_warning_title']};
    border-color: {t['notify_warning_border']};
    background-color: {t['notify_warning_bg']};
}}
QPushButton[variant="channelChoice"][filterTone="warning"]:hover {{
    color: {t['warning']};
    border-color: {t['warning']};
}}
QPushButton[variant="channelChoice"][filterTone="warning"]:checked,
QPushButton[variant="channelChoice"][filterTone="warning"]:checked:hover {{
    background-color: {t['warning']};
    color: {t['text_on_accent']};
    border-color: {t['warning']};
}}
QPushButton[variant="channelChoice"][filterTone="error"] {{
    color: {t['notify_error_title']};
    border-color: {t['notify_error_border']};
    background-color: {t['notify_error_bg']};
}}
QPushButton[variant="channelChoice"][filterTone="error"]:hover {{
    color: {t['danger']};
    border-color: {t['danger']};
}}
QPushButton[variant="channelChoice"][filterTone="error"]:checked,
QPushButton[variant="channelChoice"][filterTone="error"]:checked:hover {{
    background-color: {t['danger']};
    color: {t['text_on_accent']};
    border-color: {t['danger']};
}}
QPushButton[variant="channelChoice"][filterTone="crash"] {{
    color: {t['notify_crash_title']};
    border-color: {t['notify_crash_border']};
    background-color: {t['notify_crash_bg']};
}}
QPushButton[variant="channelChoice"][filterTone="crash"]:hover {{
    color: {t['notify_crash_title']};
    border-color: {t['notify_crash_title']};
}}
QPushButton[variant="channelChoice"][filterTone="crash"]:checked,
QPushButton[variant="channelChoice"][filterTone="crash"]:checked:hover {{
    background-color: {t['notify_crash_title']};
    color: {t['text_on_accent']};
    border-color: {t['notify_crash_title']};
}}
QFrame#cardDivider {{
    background-color: {t['border_subtle']};
    border: none;
    max-height: 1px;
}}
QFrame#settingDivider {{
    background-color: {t['surface_hover']};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}
QLabel[role="desc"], QLabel[role="itemDesc"] {{
    color: {t['text_secondary']};
    font-size: 12px;
}}
QLabel[role="itemTitle"] {{
    color: {t['text']};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#saveBadge {{
    color: {t['success']};
    background-color: {t['notify_success_bg']};
    border: 1px solid {t['notify_success_border']};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#primaryBtn {{
    background-color: {t['primary']};
    color: {t['text_on_accent']};
}}
QPushButton#primaryBtn:hover {{
    background-color: {t['primary_hover']};
}}

/* ===== 侧栏导航 ===== */
QWidget#sidebar QToolButton {{
    background-color: transparent;
    color: {t['text_muted']};
    border-radius: 12px;
    font-size: 24px;
    border: none;
}}
QWidget#sidebar QToolButton:hover {{
    background-color: {t['surface_hover']};
    color: {t['text_secondary']};
}}
QWidget#sidebar QToolButton[active="true"] {{
    background-color: transparent;
    color: {t['text_primary']};
}}
QWidget#sidebar QToolButton[active="true"]:hover {{
    background-color: {t['surface_hover']};
    color: {t['text_primary']};
}}
QFrame#sidebarNavIndicator {{
    background-color: {t['primary']};
    border: none;
    border-radius: 2px;
}}

/* ===== 频道页工具栏 ===== */
QToolButton#toolbarButton {{
    background-color: {t['control']};
    border-radius: 10px;
    border: 1px solid {t['control_border']};
}}
QToolButton#toolbarButton:hover {{
    background-color: {t['control_hover']};
    border-color: {t['border_strong']};
}}
QToolButton#toolbarButton:pressed {{
    background-color: {t['control_pressed']};
    border-color: {t['border_strong']};
}}

/* ===== 文字语义 ===== */
QLabel {{
    color: {t['text']};
    background: transparent;
}}
QLabel[role="title"] {{
    color: {t['text_primary']};
}}
QLabel[role="secondary"] {{
    color: {t['text_secondary']};
}}
QLabel[role="muted"] {{
    color: {t['text_muted']};
}}
QLabel[role="link"] {{
    color: {t['primary']};
}}
QLabel[statusTone="neutral"]  {{ color: {t['text_muted']}; }}
QLabel[statusTone="success"]  {{ color: {t['success']}; }}
QLabel[statusTone="warning"]  {{ color: {t['warning']}; }}
QLabel[statusTone="danger"]   {{ color: {t['danger']}; }}
QLabel[statusTone="info"]     {{ color: {t['info']}; }}

/* ===== 输入控件 ===== */
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTextBrowser, QSpinBox {{
    background-color: {t['surface_input']};
    border: 1px solid {t['control_border']};
    border-radius: 10px;
    padding: 8px 12px;
    color: {t['text']};
    selection-background-color: {t['selection']};
    selection-color: {t['text_on_accent']};
}}
QLineEdit:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover {{
    border: 1px solid {t['border_strong']};
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border: 1px solid {t['focus']};
}}
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{
    color: {t['text_muted']};
    background-color: {t['surface_hover']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['surface_raised']};
    border: 1px solid {t['border']};
    color: {t['text']};
    selection-background-color: {t['surface_hover']};
    selection-color: {t['text_primary']};
    outline: none;
}}

/* ===== 按钮 ===== */
QPushButton {{
    background-color: {t['surface_hover']};
    color: {t['text']};
    border: none;
    border-radius: 10px;
    padding: 10px 20px;
    font-size: 14px;
}}
QPushButton[settingsCompact="true"] {{
    padding: 0 8px;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: {t['surface_pressed']};
    color: {t['text_primary']};
}}
QPushButton:pressed {{
    background-color: {t['border']};
    color: {t['text_primary']};
}}
QPushButton:disabled {{
    background-color: {t['primary_disabled_bg']};
    color: {t['primary_disabled_text']};
}}
QPushButton[variant="primary"] {{
    background-color: {t['primary']};
    color: {t['text_on_accent']};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {t['primary_hover']};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {t['primary_hover']};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {t['primary_disabled_bg']};
    color: {t['primary_disabled_text']};
}}
QPushButton[variant="danger"] {{
    background-color: {t['danger']};
    color: {t['text_on_accent']};
}}
QPushButton[variant="danger"]:hover {{
    background-color: #DC2626;
}}
QPushButton[variant="danger"]:pressed {{
    background-color: #B91C1C;
}}
QPushButton[variant="secondary"] {{
    background-color: {t['surface_hover']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
QPushButton[variant="secondary"]:hover {{
    background-color: {t['surface_pressed']};
    color: {t['text_primary']};
    border-color: {t['border_strong']};
}}
QPushButton[variant="secondary"]:pressed {{
    background-color: {t['border']};
    color: {t['text_primary']};
    border-color: {t['border_strong']};
}}
QPushButton[variant="reset"] {{
    background: transparent;
    border: 1px solid {t['border_strong']};
    border-radius: 6px;
    color: {t['text_secondary']};
    font-size: 11px;
    padding: 0 8px;
}}
QPushButton[variant="reset"]:hover {{
    background: {t['surface_pressed']};
    color: {t['danger']};
    border-color: {t['danger']};
}}
QPushButton[variant="reset"]:pressed {{
    background: {t['action_delete_pressed']};
    color: {t['danger']};
    border-color: {t['danger']};
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    color: {t['text_secondary']};
}}
QPushButton[variant="ghost"]:hover {{
    color: {t['primary']};
}}
QPushButton[variant="ghost"]:pressed {{
    color: {t['primary_hover']};
}}

/* ===== 复选框 ===== */
QCheckBox {{
    color: {t['text']};
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t['border_strong']};
    border-radius: 4px;
    background-color: {t['surface_input']};
}}
QCheckBox::indicator:checked {{
    background-color: {t['primary']};
    border-color: {t['primary']};
}}

/* ===== 菜单 ===== */
QMenu {{
    background-color: {t['surface_raised']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 6px;
    color: {t['text']};
}}
QMenu::item {{
    padding: 8px 24px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {t['surface_hover']};
    color: {t['text_primary']};
}}
QMenu::item:disabled {{
    color: {t['text_muted']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t['border_subtle']};
    margin: 4px 8px;
}}

/* ===== 提示 ===== */
QToolTip {{
    background-color: {t['tooltip_background']};
    color: {t['tooltip_text']};
    border: 1px solid {t['tooltip_border']};
    border-radius: 6px;
    padding: 4px 8px;
}}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{
    background: {t['scroll_track']};
    width: 10px;
    border-radius: 5px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t['scroll_handle']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['scroll_handle_hover']};
}}
QScrollBar:horizontal {{
    background: {t['scroll_track']};
    height: 10px;
    border-radius: 5px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scroll_handle']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t['scroll_handle_hover']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}

/* ===== Tab ===== */
QTabWidget::pane {{
    border: none;
    background-color: {t['surface']};
}}
QTabBar::tab {{
    background-color: {t['surface_hover']};
    color: {t['text_secondary']};
    padding: 10px 20px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
}}
QTabBar::tab:hover {{
    background-color: {t['surface_pressed']};
    color: {t['text_primary']};
}}
QTabBar::tab:selected {{
    background-color: {t['primary']};
    color: {t['text_on_accent']};
}}
QTabBar::tab:selected:hover {{
    background-color: {t['primary_hover']};
    color: {t['text_on_accent']};
}}

/* ===== 浮层 ===== */
QWidget#modalOverlay {{
    background: {t['overlay_scrim']};
}}
QFrame#settingsOverlayPanel, QFrame#logViewerPanel {{
    background-color: {t['surface_raised']};
    border: 1px solid {t['border']};
    border-radius: 14px;
}}
QFrame#settingsOverlayPanel QLabel, QFrame#logViewerPanel QLabel {{
    background: transparent;
}}
QWidget#logViewerTopBar {{
    background-color: {t['panel']};
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    border-bottom: 1px solid {t['border']};
}}
QFrame#accountFrame {{
    background-color: {t['surface_input']};
    border-radius: 8px;
}}
QFrame#roomSettingsPanel {{
    background-color: {t['surface_raised']};
    border: 1px solid {t['border_strong']};
    border-radius: 16px;
}}
QWidget#roomSettingsTopBar {{
    background-color: {t['panel']};
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
    border-bottom: 1px solid {t['border']};
}}
QLabel#roomBadge {{
    color: {t['info']};
    background-color: {t['notify_info_bg']};
    border: 1px solid {t['notify_info_border']};
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 12px;
}}
QLabel#inheritBadge {{
    color: {t['success']};
    background-color: {t['notify_success_bg']};
    border: 1px solid {t['notify_success_border']};
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
}}
QPushButton[variant="close"] {{
    background-color: {t['surface_hover']};
    border: 1px solid {t['border']};
    color: {t['text_primary']};
    /* 覆盖全局 padding:10px 20px，否则 34x34 按钮只剩空框看不到 X */
    padding: 0;
    margin: 0;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    font-size: 18px;
    font-weight: 700;
    border-radius: 8px;
    text-align: center;
}}
QPushButton[variant="close"]:hover {{
    background-color: {t['notify_error_bg']};
    border: 1px solid {t['notify_error_border']};
    color: {t['danger']};
}}
QPushButton[variant="close"]:pressed {{
    background-color: {t['surface_pressed']};
    color: {t['danger']};
}}

/* ===== 状态色工具 ===== */
*[statusTone="neutral"]  {{ color: {t['text_muted']}; }}
*[statusTone="success"]  {{ color: {t['success']}; }}
*[statusTone="warning"]  {{ color: {t['warning']}; }}
*[statusTone="danger"]   {{ color: {t['danger']}; }}
*[statusTone="info"]     {{ color: {t['info']}; }}

/* ===== 通知 toast ===== */
QWidget#notificationCard {{
    border-radius: 10px;
    border: 1px solid {t['notify_info_border']};
    background-color: {t['notify_info_bg']};
}}
QWidget#notificationCard[level="success"] {{
    background-color: {t['notify_success_bg']};
    border-color: {t['notify_success_border']};
}}
QWidget#notificationCard[level="info"] {{
    background-color: {t['notify_info_bg']};
    border-color: {t['notify_info_border']};
}}
QWidget#notificationCard[level="error"] {{
    background-color: {t['notify_error_bg']};
    border-color: {t['notify_error_border']};
}}
QFrame#notificationAccent {{
    border: none;
    border-radius: 2px;
    background-color: {t['info']};
}}
QWidget#notificationCard[level="success"] QFrame#notificationAccent {{ background-color: {t['success']}; }}
QWidget#notificationCard[level="error"]   QFrame#notificationAccent {{ background-color: {t['danger']}; }}
QLabel#notificationIcon {{
    color: {t['text_on_accent']};
    background-color: {t['info']};
    border-radius: 14px;
    font-size: 14px;
    font-weight: 700;
    border: none;
}}
QWidget#notificationCard[level="success"] QLabel#notificationIcon {{ background-color: {t['success']}; }}
QWidget#notificationCard[level="error"]   QLabel#notificationIcon {{ background-color: {t['danger']}; }}
QLabel#notificationTitle {{
    font-size: 14px;
    font-weight: 600;
    border: none;
    background: transparent;
    color: {t['notify_info_title']};
}}
QWidget#notificationCard[level="success"] QLabel#notificationTitle {{ color: {t['notify_success_title']}; }}
QWidget#notificationCard[level="error"]   QLabel#notificationTitle {{ color: {t['notify_error_title']}; }}
QLabel#notificationBadge {{
    color: {t['text_on_accent']};
    background-color: {t['info']};
    border-radius: 9px;
    font-size: 11px;
    font-weight: 700;
    padding: 1px 6px;
}}
QWidget#notificationCard[level="success"] QLabel#notificationBadge {{ background-color: {t['success']}; }}
QWidget#notificationCard[level="error"]   QLabel#notificationBadge {{ background-color: {t['danger']}; }}
QLabel#notificationMessage {{
    color: {t['text']};
    font-size: 13px;
    border: none;
    background: transparent;
}}
QToolButton#notificationClose {{
    background-color: transparent;
    color: {t['text_secondary']};
    border-radius: 4px;
    font-size: 14px;
    border: none;
}}
QToolButton#notificationClose:hover {{
    color: {t['text_primary']};
    background-color: {t['surface_pressed']};
}}
"""


def enable_surface(widget):
    """让 QFrame/QWidget 真正绘制应用 QSS 中的 background-color。

    仅靠 objectName + 全局 QSS 时，某些 NoFrame QFrame 不会画背景。
    不要 setAutoFillBackground(True)：那会用 QPalette 窗口色盖住 QSS 背景。
    父控件若使用未加选择器的 setStyleSheet("background-color: ...")，
    也会污染子树，透明/页面容器必须写成 objectName 限定规则。
    """
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(False)
    return widget


def repolish(widget):
    """动态属性变化后让指定控件重取应用 QSS。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class ThemeManager(QObject):
    """进程级主题管理：应用 QPalette/QSS，真实切换时发 changed。"""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = None
        self._tokens = DARK_TOKENS

    @property
    def current_name(self) -> str:
        return self._name or DARK_THEME

    @property
    def tokens(self):
        return self._tokens

    def apply(self, app, value) -> str:
        """应用主题。必须在 GUI 线程、QApplication 存在后调用。不写配置。"""
        name = normalize_theme(value)
        if name == self._name:
            return name
        tokens = THEMES[name]
        app.setPalette(build_palette(tokens))
        app.setStyleSheet(build_stylesheet(tokens))
        self._name = name
        self._tokens = tokens
        self.changed.emit(name)
        return name


_manager = None


def theme_manager() -> ThemeManager:
    global _manager
    if _manager is None:
        app = QApplication.instance()
        _manager = ThemeManager(parent=app)
    return _manager
