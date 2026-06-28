"""
高能片段分析器 —— 解析 XML 弹幕,找出『弹幕密度异常高』的区间。

v1 算法(只做『重复弹幕密度』):
1. 解析所有弹幕为 (time_sec, text, uid) 列表
2. 屏蔽词过滤(config.draw_words)
3. 滑动窗口(默认 10s,步长 1s)扫,统计每个窗口里『归一化后相同』的弹幕数
4. 出现 ≥ repeat_threshold(默认 5)的窗口视为命中
5. 合并相邻命中,过滤掉短于 min_clip_duration(默认 5s)的片段
6. 输出 Clip 列表,每条带 start/end/duration/peak_density/score/sample_texts

Rule 抽象:
    detect_clips(parts) -> list[Clip] 接受多个『规则』,v1 只有 RepeatRule。
    后续加 KeywordRule(关键词密度)/ SpeedRule(语速)等不影响公共调用。

性能:
    - 2h 录播 ~1-2w 弹幕,纯 Python 扫 1-3s
    - 跑在 QThread 里(progress + finished 信号),UI 不卡
"""
from __future__ import annotations
import os
import re
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Sequence
from PySide6.QtCore import QThread, Signal

from .recording_index import RecordingPart

logger = logging.getLogger(__name__)

# 归一化:去掉空白 + 取前 N 字符(N 太大反而拼错,太小撞 key)
NORM_PREFIX = 8
NORM_TRIM_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """把一条弹幕归一化成『去空白 + 前缀截断』,作为聚类 key。"""
    if not text:
        return ""
    t = NORM_TRIM_RE.sub("", text)
    return t[:NORM_PREFIX]


def _format_time(seconds: float) -> str:
    """把秒数格式化为 HH:MM:SS 或 MM:SS。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------- 数据结构 ----------------
@dataclass
class Clip:
    start: float           # 秒(相对 part 起始)
    end: float
    duration: float
    peak_density: float    # 最高窗口密度(条/秒)
    sample_texts: List[str] = field(default_factory=list)
    score: float = 0.0     # 综合打分(0-1,越大越像高能)

    def display_range(self) -> str:
        return f"{_format_time(self.start)}–{_format_time(self.end)}"


@dataclass
class AnalysisConfig:
    window_seconds: int = 10           # 滑动窗口大小
    window_step: int = 1               # 步长(秒)
    repeat_threshold: int = 5          # 单窗口内重复 key 数 ≥ 阈值视为命中
    min_clip_duration: int = 5         # 合并后剔除短于此的片段
    min_density_per_sec: float = 0.5   # 密度(条/秒)小于此过滤掉
    draw_words: List[str] = field(default_factory=list)  # 屏蔽词
    merge_gap: int = 3                 # 合并相邻命中时允许的最大间隔(秒)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AnalysisConfig":
        defaults = cls()
        if not d:
            return defaults
        return cls(
            window_seconds=int(d.get("window_seconds", defaults.window_seconds)),
            window_step=int(d.get("window_step", defaults.window_step)),
            repeat_threshold=int(d.get("repeat_threshold", defaults.repeat_threshold)),
            min_clip_duration=int(d.get("min_clip_duration", defaults.min_clip_duration)),
            min_density_per_sec=float(d.get("min_density_per_sec", defaults.min_density_per_sec)),
            draw_words=list(d.get("draw_words", defaults.draw_words) or []),
            merge_gap=int(d.get("merge_gap", defaults.merge_gap)),
        )


# ---------------- 规则抽象 ----------------
class Rule:
    """扩展点:v1 只有 RepeatRule,后续可加 KeywordRule / SpeedRule 等。"""
    name: str = "base"

    def detect(self, messages: List["DanmakuMsg"], cfg: AnalysisConfig) -> List[Clip]:
        raise NotImplementedError


@dataclass
class DanmakuMsg:
    time: float   # 秒
    text: str
    uid: str = ""


class RepeatRule(Rule):
    """滑动窗口:同 key 弹幕数 ≥ 阈值即视为命中。"""

    name = "repeat"

    def detect(self, messages: List[DanmakuMsg], cfg: AnalysisConfig) -> List[Clip]:
        if not messages:
            return []
        # 过滤掉短弹幕(几乎肯定是无意义刷屏)+ 屏蔽词
        filtered: List[DanmakuMsg] = []
        for m in messages:
            txt = (m.text or "").strip()
            if len(txt) < 2:
                continue
            if cfg.draw_words and any(w and w in txt for w in cfg.draw_words):
                continue
            filtered.append(m)
        if not filtered:
            return []
        # 按时间排序
        filtered.sort(key=lambda m: m.time)

        # 滑动窗口扫
        max_t = filtered[-1].time
        win = max(1, cfg.window_seconds)
        step = max(1, cfg.window_step)
        threshold = max(2, cfg.repeat_threshold)
        all_clips: List[Clip] = []

        # 二分加速:对每个窗口起点 t,start_idx 走二分
        from bisect import bisect_left
        times = [m.time for m in filtered]

        for t in _frange(0.0, max_t + 0.001, step):
            lo = bisect_left(times, t)
            hi = bisect_left(times, t + win)
            if hi - lo < threshold:
                continue
            # 统计归一化 key 出现次数
            counts: dict = {}
            samples: dict = {}
            for k in range(lo, hi):
                key = _normalize(filtered[k].text)
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
                if counts[key] == 1:
                    samples[key] = filtered[k].text
            if not counts:
                continue
            top_key = max(counts, key=lambda k: counts[k])
            top_count = counts[top_key]
            density = top_count / win
            if density < cfg.min_density_per_sec:
                continue
            all_clips.append(Clip(
                start=t,
                end=t + win,
                duration=win,
                peak_density=density,
                sample_texts=[samples[top_key]],
                score=_score(density, top_count, win),
            ))

        # 合并相邻/重叠的 clip
        return _merge_clips(all_clips, gap=cfg.merge_gap, min_duration=cfg.min_clip_duration)


def _frange(start: float, stop: float, step: float):
    """类似 np.arange 但纯 Python 实现。"""
    x = start
    eps = step * 0.001
    while x < stop - eps:
        yield x
        x += step


def _score(density: float, count: int, win: int) -> float:
    """简单归一化打分:0-1,密度和绝对数量综合。"""
    d = min(1.0, density / 1.0)
    c = min(1.0, count / max(1, win))
    return round(0.6 * d + 0.4 * c, 3)


def _merge_clips(clips: List[Clip], gap: int, min_duration: int) -> List[Clip]:
    """合并时间上相邻/重叠的 clip,再过滤掉过短的。"""
    if not clips:
        return []
    clips.sort(key=lambda c: (c.start, c.end))
    merged: List[Clip] = [clips[0]]
    for c in clips[1:]:
        prev = merged[-1]
        if c.start <= prev.end + gap:
            new_end = max(prev.end, c.end)
            new_density = max(prev.peak_density, c.peak_density)
            new_score = max(prev.score, c.score)
            samples = list(prev.sample_texts)
            for s in c.sample_texts:
                if s not in samples:
                    samples.append(s)
                if len(samples) >= 3:
                    break
            merged[-1] = Clip(
                start=prev.start,
                end=new_end,
                duration=new_end - prev.start,
                peak_density=new_density,
                sample_texts=samples[:3],
                score=new_score,
            )
        else:
            merged.append(c)
    return [c for c in merged if c.duration >= min_duration]


# ---------------- 解析器 ----------------
def parse_danmaku_xml(xml_path: str) -> List[DanmakuMsg]:
    """流式解析 B 站格式的 XML 弹幕。

    格式:<?xml ...?><i>
        <d p="time,type,size,color,?,uid,?,?">text</d>
        ...
      </i>

    性能:用 iterparse,parse 完即释放,内存 O(N)。
    """
    out: List[DanmakuMsg] = []
    if not xml_path or not os.path.exists(xml_path):
        return out
    try:
        context = ET.iterparse(xml_path, events=("end",))
        for _, elem in context:
            if elem.tag != "d":
                continue
            p = elem.attrib.get("p", "")
            parts = p.split(",")
            try:
                t = float(parts[0])
            except (ValueError, IndexError):
                t = 0.0
            uid = parts[5] if len(parts) > 5 else ""
            text = (elem.text or "").strip()
            if not text:
                continue
            out.append(DanmakuMsg(time=t, text=text, uid=uid))
            elem.clear()
    except ET.ParseError as e:
        logger.warning(f"弹幕 XML 解析失败 {xml_path}: {e}")
    except OSError as e:
        logger.warning(f"无法读取弹幕 XML {xml_path}: {e}")
    return out


# ---------------- 公共入口 ----------------
def detect_clips(messages: List[DanmakuMsg], cfg: AnalysisConfig,
                 rules: Optional[Sequence[Rule]] = None) -> List[Clip]:
    """对解析后的弹幕应用一组规则,返回合并后的 Clip 列表。"""
    rules = rules or [RepeatRule()]
    all_clips: List[Clip] = []
    for rule in rules:
        try:
            all_clips.extend(rule.detect(messages, cfg))
        except Exception as e:
            logger.error(f"规则 {rule.name} 执行失败: {e}")
    return _merge_clips(all_clips, gap=max(rules and 3 or 3, 3), min_duration=cfg.min_clip_duration)


# ---------------- QThread 包装 ----------------
class ClipAnalysisWorker(QThread):
    """后台分析线程 —— 跑完发 finished(Clip 列表)。"""
    progress = Signal(int)        # 0-100
    finished = Signal(list)       # list[Clip]
    failed = Signal(str)

    def __init__(self, part: RecordingPart, cfg: AnalysisConfig, parent=None):
        super().__init__(parent)
        self._part = part
        self._cfg = cfg
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.progress.emit(5)
            if self._cancel:
                self.finished.emit([])
                return
            xml = self._part.xml_path
            if not xml or not os.path.exists(xml):
                self.failed.emit("该录播没有对应的弹幕文件")
                return
            messages = parse_danmaku_xml(xml)
            self.progress.emit(40)
            if self._cancel:
                self.finished.emit([])
                return
            clips = detect_clips(messages, self._cfg)
            self.progress.emit(100)
            self.finished.emit(clips)
        except Exception as e:
            logger.exception("分析失败")
            self.failed.emit(str(e))
