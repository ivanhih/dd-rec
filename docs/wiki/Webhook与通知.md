# Webhook 与通知

## Webhook

配置：

- `webhooks`：回调地址
- `webhook_format`：默认 `blrec`（兼容 blrec / 部分上传器生态）
- `webhook_only_verified_artifacts`：仅对后处理校验通过的文件发送 split 类事件

### 典型事件语义

实现以 `core/recorder.py` 为准，常见包括：

| 事件 | 时机 |
|------|------|
| 录制开始 / 结束 | 一场直播录制会话起止 |
| `recording_split` / FileClosed 类 | 分段后处理成功，携带 `file` / `size` / `duration` / `session_id` |

`session_id` 在会话内保持一致，便于下游（如上传器）去重与关联多段文件。

### 对接建议

1. 先用本地 HTTP 调试服务接收 payload，确认字段
2. 打开 `webhook_only_verified_artifacts`，避免半截文件被入库
3. 后处理是异步的：以产物 webhook 为准，而不是“刚切段”的瞬间

## 系统/远程通知

- `notify_enabled`、`notify_url`
- `notify_on_live_end`、`notify_on_error`
- `notify_title_template` / `notify_body_template`（Liquid，可按 event 与 URL scheme 分支）

本地 UI 也会在监控开闭、切割成功/失败、删除房间等操作时弹出应用内通知。
