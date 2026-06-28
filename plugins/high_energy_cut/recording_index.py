"""
录播目录索引 —— 扫描磁盘上已录制的 MP4 + 弹幕 XML 配对。

约定布局(与主程序 `core/recorder.py:_build_save_path` + `path_template` 一致):
    <base_save_dir>/<uname>/<YYYY-MM-DD>/<room>-<uname>-<title>-<timestamp>.mp4
    <base_save_dir>/<uname>/<YYYY-MM-DD>/<room>-<uname>-<title>-<timestamp>.xml

XML 弹幕与 MP4 同目录同名(仅后缀不同),由 `core.danmaku_recorder.DanmakuRecorder`
生成。MP4 与 XML 的 basename(stem)严格一致才能配对成功。

扫描策略:
1. 拿『全局默认 save_dir』和所有已添加直播间的『effective save_dir』,合并去重。
2. 遍历每个根目录下的所有子目录(uname),再下钻一层 YYYY-MM-DD/。
3. 一个 date folder 内可能有多个 part(直播被切分多次),它们都归属同一段录播会话;
   UI 上按"日期"汇总展示一段会话下的所有 part。

不创建任何文件,纯只读扫描。性能:几千个文件秒级。
"""
from __future__ import annotations
import os
import re
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 文件名中时间戳部分格式:2025-12-30-203015-123(YYYY-MM-DD-HHMMSS-mmm)
# 兼容主程序 path_template: `{{ ctime | date: '%Y-%m-%d-%H%M%S-%3f', 'local' }}`
_FILENAME_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})-(\d{3})")

# date folder 名称格式:YYYY-MM-DD
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class RecordingPart:
    """单个分 P(一个 MP4 + 对应 XML)。"""
    mp4_path: str
    xml_path: Optional[str]  # 可能为 None —— 没弹幕就跳过这个 part
    uname: str
    room_id: str
    title: str
    start_time: datetime  # 从文件名解析出的精确开始时间
    size_bytes: int

    @property
    def base_name(self) -> str:
        return os.path.splitext(os.path.basename(self.mp4_path))[0]

    @property
    def has_danmaku(self) -> bool:
        return bool(self.xml_path) and os.path.exists(self.xml_path)


@dataclass
class RecordingSession:
    """同一日期下的一组 part(可能 1 个 part 也可能多个,取决于主播是否被切分)。"""
    uname: str
    date: date
    parts: List[RecordingPart] = field(default_factory=list)

    @property
    def has_danmaku(self) -> bool:
        return any(p.has_danmaku for p in self.parts)

    @property
    def total_size_bytes(self) -> int:
        return sum(p.size_bytes for p in self.parts)

    @property
    def duration_seconds(self) -> float:
        """按 part 时间戳差估算;单 part 时返回 0(没数据估不出)。"""
        if len(self.parts) < 2:
            return 0.0
        ts = sorted(p.start_time for p in self.parts)
        return (ts[-1] - ts[0]).total_seconds()


class RecordingIndex:
    """磁盘扫描 + 内存索引。"""

    def __init__(self, app_context=None):
        self._app = app_context
        # uname -> date -> RecordingSession
        self._index: Dict[str, Dict[date, RecordingSession]] = {}
        # 上次扫描的根目录列表(用于重扫)
        self._scan_roots: List[str] = []

    # ---------------- 公共 API ----------------
    def refresh(self) -> None:
        """全量重扫(扫描 < 几秒,可手动调)。"""
        self._index.clear()
        self._scan_roots = self._collect_scan_roots()
        for root in self._scan_roots:
            if not os.path.isdir(root):
                continue
            self._scan_root(root)

    def list_unames(self) -> List[str]:
        """返回所有有录播的主播名(uname 列表)。"""
        return sorted(self._index.keys())

    def list_dates_for(self, uname: str) -> List[date]:
        """返回某主播下所有有录播的日期(倒序)。"""
        if uname not in self._index:
            return []
        return sorted(self._index[uname].keys(), reverse=True)

    def get_session(self, uname: str, d: date) -> Optional[RecordingSession]:
        if uname not in self._index:
            return None
        return self._index[uname].get(d)

    def get_all_sessions_with_danmaku(self) -> List[RecordingSession]:
        """返回所有『有弹幕』的录播会话 —— 分析器的入口。"""
        out = []
        for uname, by_date in self._index.items():
            for d, sess in by_date.items():
                if sess.has_danmaku:
                    out.append(sess)
        return out

    # ---------------- 内部 ----------------
    def _collect_scan_roots(self) -> List[str]:
        """合并『全局默认目录』和所有已添加直播间的 effective 目录。"""
        roots: List[str] = []
        if self._app is not None:
            try:
                base = self._app.get_save_dir()
                if base:
                    roots.append(base)
            except Exception as e:
                logger.debug(f"get_save_dir 失败: {e}")
            try:
                for room in self._app.list_known_rooms() or []:
                    rid = str(room.get("room_id") or "")
                    uname = str(room.get("uname") or "")
                    if not rid or not uname:
                        continue
                    eff = self._app.get_effective_save_dir_for_room(rid, uname)
                    if eff and eff not in roots:
                        roots.append(eff)
            except Exception as e:
                logger.debug(f"list_known_rooms 失败: {e}")
        # 去重 + 保留顺序
        seen = set()
        uniq = []
        for r in roots:
            if r not in seen:
                seen.add(r)
                uniq.append(r)
        return uniq

    def _scan_root(self, root: str) -> None:
        """扫描一个 save_dir 根目录(里头是 <uname>/<date>/part.* 结构)。"""
        try:
            unames = [d for d in os.listdir(root) if not d.startswith(".")]
        except OSError as e:
            logger.warning(f"无法列出 {root}: {e}")
            return
        for uname in unames:
            uname_dir = os.path.join(root, uname)
            if not os.path.isdir(uname_dir):
                continue
            self._scan_uname_dir(uname, uname_dir)

    def _scan_uname_dir(self, uname: str, uname_dir: str) -> None:
        try:
            entries = os.listdir(uname_dir)
        except OSError:
            return
        for name in entries:
            full = os.path.join(uname_dir, name)
            if not os.path.isdir(full):
                continue
            m = _DATE_DIR_RE.match(name)
            if not m:
                # 可能是『高能剪切/』之类的输出目录,跳过
                continue
            try:
                d = date.fromisoformat(name)
            except ValueError:
                continue
            self._scan_date_dir(uname, d, full)

    def _scan_date_dir(self, uname: str, d: date, date_dir: str) -> None:
        try:
            files = os.listdir(date_dir)
        except OSError:
            return
        # 一次扫完,group by stem
        mp4_by_stem: Dict[str, str] = {}
        xml_by_stem: Dict[str, str] = {}
        for fname in files:
            full = os.path.join(date_dir, fname)
            if not os.path.isfile(full):
                continue
            stem, ext = os.path.splitext(fname)
            ext = ext.lower()
            if ext == ".mp4":
                mp4_by_stem[stem] = full
            elif ext == ".xml":
                xml_by_stem[stem] = full
        if not mp4_by_stem:
            return
        session = self._index.setdefault(uname, {}).setdefault(d, RecordingSession(uname=uname, date=d))
        for stem, mp4_path in mp4_by_stem.items():
            parsed = _parse_filename(stem, default_uname=uname)
            if parsed is None:
                continue
            start_time, room_id, title, uname_in_name = parsed
            xml_path = xml_by_stem.get(stem)
            try:
                size = os.path.getsize(mp4_path)
            except OSError:
                size = 0
            session.parts.append(RecordingPart(
                mp4_path=mp4_path,
                xml_path=xml_path,
                uname=uname_in_name or uname,
                room_id=room_id,
                title=title,
                start_time=start_time,
                size_bytes=size,
            ))
        # 按开始时间排序
        session.parts.sort(key=lambda p: p.start_time)


def _parse_filename(stem: str, default_uname: str) -> Optional[Tuple[datetime, str, str, str]]:
    """从 part 文件名 stem 解析 (start_time, room_id, title, uname)。

    期望格式(与 path_template 对齐):
        <room_id>-<uname>-<title>-<YYYY-MM-DD-HHMMSS-mmm>
    但 title 经常带 `-`,所以从右往左用 `-` 切更稳。
    """
    # 先抽出末尾的时间戳
    m = _FILENAME_TS_RE.search(stem)
    if not m:
        return None
    date_str, hh, mm, ss, mmm = m.groups()
    try:
        start_time = datetime.strptime(f"{date_str} {hh}:{mm}:{ss}.{mmm}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    # 时间戳之前的所有内容: <room>-<uname>-<title>
    head = stem[: m.start()].rstrip("-")
    if not head:
        return None
    # 用 '-' 切分: 第一段 room_id, 第二段 uname, 其余拼成 title
    parts = head.split("-", 2)
    room_id = parts[0] if parts else ""
    uname = parts[1] if len(parts) > 1 else default_uname
    title = parts[2] if len(parts) > 2 else ""
    return start_time, room_id, title, uname
