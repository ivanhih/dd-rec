"""
Mirror 酱 API 封装层（mirrorchyan.com）

职责:
  - 调 Mirror 酱 API 检查更新
  - 失败/未配置/超时一律返回 None,让上层走 GitHub 兜底

设计:
  - res_id 是公开标识符(类似 app id),开发者申请后填到 MIRRORCHYAN_RES_ID
  - res_id 为空字符串时,本模块完全禁用 —— 等价于未引入(零副作用)
  - CDK 不在本模块持久化,只通过参数传入,持久化在 config.json
  - 日志中不打印 CDK 明文
"""

import json
import logging
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== 配置 ====================
# 由开发者向 Mirror 酱申请后填这里(空 = 不启用 mirror 酱,完全等价于现状)
# 申请方式:加 QQ 群 1026040805 联系 Mirror 酱团队
MIRRORCHYAN_RES_ID = ""  # 例如 "DDREC"

# Mirror 酱 API endpoint
MIRRORCHYAN_API = "https://mirrorchyan.com/api/resources/{res_id}/latest"


# ==================== 数据类 ====================
@dataclass
class MirrorChyanInfo:
    """Mirror 酱 API 返回的检查更新结果

    Attributes:
        version: 远端版本号(已去掉 'v' 前缀)
        release_note: 版本日志
        download_url: 带时效的下载 URL(None 表示用户无 CDK 或 CDK 无效)
        raw_code: mirror 酱返回的原始 code(0=成功,其他=错误)
    """
    version: str
    release_note: str
    download_url: Optional[str]
    raw_code: int


# ==================== API ====================
def is_enabled() -> bool:
    """Mirror 酱是否启用(res_id 已配置)"""
    return bool(MIRRORCHYAN_RES_ID)


def check_mirror_chyan(current_version: str, cdk: str = "") -> Optional[MirrorChyanInfo]:
    """调 mirror 酱 API 检查更新。

    返回:
      None           — 未启用/API 不可达/超时/JSON 异常(调用方应回落到 GitHub)
      MirrorChyanInfo — 已成功调通:
        download_url is None   → 用户无 CDK 或 CDK 无效,下载走 GitHub 兜底
        download_url is str    → 用户有有效 CDK(本次不用,留给未来)

    注意:无论返回什么,下载 URL 一律走 GitHub release 直链(mirror 酱 url
    是带时效的一次性 URL,kachina 安装器接不了 —— 见计划文档)。
    """
    if not is_enabled():
        return None  # 没配置 res_id,直接不调(等价于未引入)

    url = MIRRORCHYAN_API.format(res_id=MIRRORCHYAN_RES_ID)
    params = {"current_version": current_version, "user_agent": "dd_rec_main"}
    if cdk:
        params["cdk"] = cdk
    full_url = f"{url}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(full_url, headers={"User-Agent": "dd_rec_main/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"mirror 酱 API 调用失败: {e}")
        return None

    code = data.get("code", -1)
    if code != 0:
        # 不打印 msg(可能含敏感信息),code 足够诊断
        logger.info(f"mirror 酱返回非 0: code={code}")
        # 返回一个表示"调通但失败"的结果,version 用本地版让上层走"无更新"分支
        return MirrorChyanInfo(
            version=current_version,
            release_note="",
            download_url=None,
            raw_code=code,
        )

    payload = data.get("data") or {}
    version_name = str(payload.get("version_name", "")).lstrip("v")
    logger.info(f"mirror 酱:远端版本={version_name},有 url={'是' if payload.get('url') else '否'}")
    return MirrorChyanInfo(
        version=version_name,
        release_note=payload.get("release_note", "") or "",
        download_url=payload.get("url"),  # None 表示无 CDK
        raw_code=0,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    if not MIRRORCHYAN_RES_ID:
        print("MIRRORCHYAN_RES_ID 未配置,跳过测试")
        sys.exit(0)
    cur = sys.argv[1] if len(sys.argv) > 1 else "0.0.0"
    cdk = sys.argv[2] if len(sys.argv) > 2 else ""
    info = check_mirror_chyan(cur, cdk)
    if info is None:
        print("API 不可达")
    else:
        print(f"version={info.version}, code={info.raw_code}, has_url={info.download_url is not None}")
