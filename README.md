# DD 录播机

B 站直播自动录制桌面客户端。支持多房间监控、FLV 原生捕获、弹幕落盘、按时长/大小切割、Webhook 通知，以及 Portable 自动更新。

当前版本：**1.0.5**（见 [`version.py`](version.py)）

---

## 功能

- **多房间监控**：卡片式房间列表，独立开关监听
- **流选择**：分辨率 / 帧率 / 码率 / 编码 / 格式优先级；支持房间级覆盖
- **原生 FLV 捕获**：`avc + flv` 走 single-input 原生捕获与分段写盘；其他格式走 FFmpeg 桥接
- **后处理**：验证 → 无损 remux → 可选删除源文件（`media_pipeline`）
- **弹幕录制**：xml / jsonl / ass
- **封面下载**、路径模板（Liquid）、定时切割
- **Webhook / 远程通知**（blrec 兼容等）
- **主题 + 多语言**：深色 / 浅色；简体 / 繁体 / English
- **日志查看器**：关于页内 overlay，按 INFO / WARNING / ERROR / 崩溃筛选
- **Portable 更新**：启动时检查 GitHub Release（kachina / 本机更新通道）

---

## 截图 / 界面

| 模块 | 说明 |
|------|------|
| 频道页 | 房间卡片：直播状态、录制时长 / 速度 / 大小、切割 / 打开目录 |
| 设置页 | 全局录制、路径、转换、监控、通知 |
| 房间设置 | 全屏 overlay，房间级 Cookie / 流参数 / 切割覆盖 |
| 关于页 | 版本信息、打开日志、打开日志目录、检查更新 |

---

## 环境要求

- Windows 10/11（主要目标平台）
- Python **3.11+**（开发时以 3.14 验证过）
- 网络可访问 `live.bilibili.com` / 相关 API
- 录制依赖 FFmpeg（源码运行时需本机可用；打包版会内嵌）

---

## 源码运行

```bash
git clone https://github.com/ivanhih/dd-rec.git
cd dd-rec
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

运行时数据默认写在：

```text
<项目或 portable 根>/userdata/
  config.json
  log/bilirec.log
  log/crash.log
```

首次启动会生成默认配置；也可参考 [`config.example.json`](config.example.json)。

---

## 主要配置项

| 项 | 说明 |
|----|------|
| `stream_resolution` / `stream_codec` / `stream_format` | 拉流偏好 |
| `split_by_duration` / `split_by_size` | 自动切割 |
| `path_template` | 保存路径 Liquid 模板 |
| `chat_record_enabled` / `chat_format` | 弹幕 |
| `convert_enabled` / `convert_format` | 后处理 remux 目标格式 |
| `webhooks` | 录制事件回调 |
| `auto_start` / `prevent_sleep` | 开机自启、防止休眠 |

房间级设置可覆盖全局项（Cookie、流参数、自定义目录等）。

---

## 目录结构（简）

```text
main.py                 # 入口 / 主窗口
version.py              # 版本号唯一来源
core/
  recorder.py           # 录制主循环
  flv/                  # 原生 FLV 解析与分段
  stream_source.py      # HTTP-FLV 拉流
  media_pipeline.py     # 关闭后 remux / 校验
  danmaku_recorder.py   # 弹幕
  bili_api.py           # 直播 API
  http_ssl.py           # 进程级共享 SSL（Windows 并发安全）
  config.py             # 配置读写
  portable_updater.py   # Portable 更新检查
ui/
  theme.py / i18n.py    # 主题与多语言
  room_card.py          # 房间卡片
  settings_dialog.py    # 全局 / 房间设置 overlay
  log_viewer_dialog.py  # 日志 overlay
tests/                  # 单元 / UI 冒烟测试
build.py                # 打包流水线
RELEASE.md              # 发布与安装器说明
```

---

## 测试

```bash
python -m unittest tests.test_http_ssl tests.test_log_viewer tests.test_flv_parser tests.test_i18n -v
# 或更全
python -m unittest discover -s tests -v
```

---

## 构建 / 发布

版本号只改 `version.py`，然后：

```bash
python build.py
```

产物、GitHub Release、自动更新流程见 [`RELEASE.md`](RELEASE.md)。

Portable / kachina 相关配置见 `kachina.config.json`。

---

## 1.0.5 变更摘要

- 原生 FLV 捕获管线 + 媒体后处理
- 修复 Windows 多房间启动时 SSL 并发 access violation
- 修复录制线程直接刷新 UI 导致的长时间录制崩溃（状态信号改 `QueuedConnection`）
- 主题 / i18n / 启动页 / 设置与日志 overlay 重做
- 移除旧 launcher / mirror 残留，统一 packaging 命名

---

## 许可证与仓库

- 源码仓库：https://github.com/ivanhih/dd-rec
- 问题反馈：请在 GitHub Issues 提交，并尽量附上 `userdata/log/bilirec.log` 与 `crash.log` 尾部

---

## 免责声明

本项目仅供个人学习与合法用途。请遵守 B 站用户协议与当地法律法规；勿用于未授权转播或侵权内容录制。
