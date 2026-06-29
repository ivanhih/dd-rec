"""
Cloudflare R2 通道配置

R2 公开 URL 模板(让 kachina update.exe 能下载 Install.exe)。
填好下面常量后,用户即可在更新页面选择"Cloudflare R2"通道。

注意:
  - R2 上传的文件名要跟 kachina 模板一致: dd_rec.Install.{ver}.exe
  - versionRegex 跟 GitHub 源一样(从 URL 末尾解析 vX.Y.Z)
  - 设为空字符串 = 不启用 R2 通道(选项灰掉,但不报错)

启用流程:
  1. 注册 Cloudflare 账号 https://dash.cloudflare.com/sign-up
  2. 添加信用卡验证(不会扣费)
  3. R2 → Create bucket → 命名如 dd-rec-releases → 区域 Automatic
  4. 桶设置 → Public access → 启用 → 复制公开 URL
  5. 把 R2_PUBLIC_BASE 填成你的公开 URL(去掉末尾的 /)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== 你需要填的 ====================
# R2 桶公开访问的 base URL
# 例: https://pub-abc123def456.r2.dev/dd-rec-releases
# 这个 URL 是公开的(给所有用户下载用),可以提交到 git,会编译进 exe
R2_PUBLIC_BASE = "https://pub-402ecb62c36c4b0ba05c007d3fe1dca2.r2.dev/dd-rec-releases"


def is_enabled() -> bool:
    """R2 是否配置可用(非空 + 看起来像 URL)"""
    if not R2_PUBLIC_BASE:
        return False
    return R2_PUBLIC_BASE.startswith("http://") or R2_PUBLIC_BASE.startswith("https://")


def build_source_uri(version: str) -> Optional[str]:
    """生成 kachina source.uri 模板字符串(供 R2 通道用)

    返回示例:
      https://pub-xxx.r2.dev/dd-rec-releases/dd_rec.Install.${version}.exe

    kachina 不会替换 ${version} 占位符之外的任何东西 —— URL 模板要保持纯净,
    不能加 #versionRegex= 之类的后缀(那是 mirror 酱的伪语法,kachina 不支持)。

    Args:
        version: 当前可用的远端版本号(用于占位检查,实际 kachina 自己替换)

    Returns:
        URI 模板字符串,或 None(未配置)
    """
    if not is_enabled():
        return None
    base = R2_PUBLIC_BASE.rstrip("/")
    return f"{base}/dd_rec.Install.${{version}}.exe"


if __name__ == "__main__":
    # 单元测试
    print(f"is_enabled() = {is_enabled()}")
    print(f"build_source_uri('1.0.4') = {build_source_uri('1.0.4')}")