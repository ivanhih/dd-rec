# core/utils.py
import platform
import re
import os
import datetime


def format_size(bytes_size: int) -> str:
    """容量格式化"""
    if bytes_size < 1024:
        return f"{bytes_size:.0f} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


# ==================== Liquid 模板相关 ====================
from liquid import Environment
from liquid.filter import liquid_filter

def _make_liquid_env():
    env = Environment()

    @liquid_filter
    def date_filter(value, fmt: str = "%Y-%m-%d", tz: str = "utc") -> str:
        if isinstance(value, str):
            try:
                value = datetime.datetime.fromisoformat(value)
            except Exception:
                return value

        if not isinstance(value, datetime.datetime):
            return str(value)

        if tz.lower() == "local":
            if value.tzinfo is not None:
                value = value.astimezone()
        else:
            if value.tzinfo is not None:
                value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        ms = f"{value.microsecond // 1000:03d}"
        fmt_processed = fmt.replace("%3f", ms)
        return value.strftime(fmt_processed)

    env.add_filter("date", date_filter)
    return env


LIQUID_ENV = _make_liquid_env()


def render_path_template(template_str: str, **kwargs) -> str:
    """渲染 Liquid 路径模板"""
    tpl = LIQUID_ENV.from_string(template_str)
    return tpl.render(**kwargs)