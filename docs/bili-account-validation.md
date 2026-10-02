# 内置扫码登录验证记录

日期：2026-10-02。基线：`f8b099673e2a9318342a1f8f057b999cac029944`。

## 自动验证

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest tests.test_media_http tests.test_account_ui tests.test_bili_auth tests.test_ffmpeg_flv_source tests.test_i18n tests.test_ui_responsiveness tests.test_stream_selection tests.test_config_migration -q
```

83 项通过。包括使用内置 FFmpeg 生成本地 HLS，再经媒体 relay 实际无损读取。
修改的 Python 模块通过 `py_compile`；`pip check` 未发现依赖冲突。

全量 `python -m unittest discover -s tests -q`：156 项，3 项失败、2 项错误。
在独立临时目录导出未修改的 HEAD 并运行全量测试，以下 5 项均在基线失败：

- `test_pipeline_uses_sample_validation_not_full_decode`：断言最终路径，但实际采样验证临时文件。
- `test_light_theme_applied_before_window`：已缓存的 main 模块与测试 mock 冲突。
- `test_recorders_started_lazily_after_reveal`：已缓存的 main 模块未使用测试房间配置。
- `test_overlay_deleted_after_reveal`：覆盖层指针已经置空，测试仍调用 `isVisible()`。
- `test_source_mode_update_button_skips_portable_checker`：已缓存的 main 模块没有使用 mock 更新器。

原基线全量还有紧凑按钮宽度和窗口隔离失败。本次修正设置按钮随文案调整最小宽度，相关多语言测试已通过。

## Standards

最终通过，0 项未解决发现。复审确认本地 FFmpeg 连接清除代理环境、媒体上游使用房间代理、已提交媒体响应发生错误后仅关闭连接、并发失效请求可匿名重试。未发现明确的项目标准违例或需整改的代码异味。

## Spec

最终通过，0 项未解决发现。可选登录、全局继承、手填优先、显式匿名、扫码取消竞态、账号失效回退均已覆盖。FFmpeg/HLS 后续请求读取最新凭据，健康连接不中断；裸 SESSDATA 保持兼容。

## 外部验证边界

B站真实 Web 接口可以生成二维码，并返回等待扫码状态。未登录任何真实账号。
手机确认后的账号录制、真实刷新令牌轮换和平台授权画质尚未实测。未构建或发布新 Portable 安装包。

## 侧边栏账号入口增补

用户追加要求：侧栏未登录显示默认登录按钮，点击直达扫码；已登录显示用户头像。
相对基线 `94742cb`，新增顶部头像框，复用现有账号扫码界面、后台头像加载与悬浮提示。
登录、退出、失效监控会同步按钮；取消旧头像回调，避免退出后恢复旧图。

`tests.test_account_ui`、`tests.test_i18n`、`tests.test_ui_responsiveness` 共 40 项通过。
全量共 159 项，仍为以上 3 项失败、2 项错误，没有新增失败。源码编译和 diff 格式检查通过。
原生 Qt 窗口截图确认未登录与登录后头像的布局；预览头像为测试图。

### Standards

通过，0 项发现。异步头像、过期结果取消、现有悬浮提示、主题样式和三语文案均符合项目惯例。未发现明确标准违例或需整改的代码异味。

### Spec

通过，0 项发现。未登录/失效显示登录入口，点击自动打开二维码；登录后显示头像，点击定位账号设置；状态变化即时刷新，符合本次新增要求。

## 扫码界面介绍增补

相对 `9a53e84` 增加登录用途和本地存储卡片，说明登录可选、账号访问权限、本地保存、不向 DD 服务器同步、向 B站发送必要凭据及退出清除。
检查 `AccountStore.replace`、`account_service`、用户目录解析及凭据调用点，确认账号信息写入软件目录下 `userdata/bili_account.json`，未发现 DD 账号上传或同步逻辑。
扫码界面改用主窗口内嵌 overlay，和房间/手填凭据一致；取消或关闭时均清理轮询与过期后台结果。

文案变更阶段的账号界面和多语言测试 28 项通过；全量 159 项为 3 项失败、2 项错误。这些测试是在内嵌 overlay 切换前运行的，不代表该 overlay 版本的自动测试结果。
文案阶段的原生 Qt 三语预览无裁切，简繁界面 420×620，英文 420×702；以上尺寸也是独立窗口版本记录。

### Standards

通过，0 项发现。三语文案、自动换行、纯文本渲染和 overlay 卡片样式符合项目惯例，未改动认证流程。

### Spec

通过，0 项发现。登录用途和本地保存说明与源码一致，明确了 B站请求仍需发送必要凭据；扫码界面和其它设置 overlay 一致。

## 内嵌扫码 overlay 增补

相对 `05de989`，扫码控件现在是中央窗口内容里的半透明遮罩与居中面板，使用 `QScrollArea` 适配较小窗口。关闭按钮、取消、Esc、点击遮罩和 `close()` 都汇入同一清理流程，停止轮询并废弃后台结果。
遮罩点击与 overlay 使用相同父坐标系；调整窗口宽度会重算说明文案高度。
本次原生 Qt 窗口预览：1360×780 时面板 420×668 且无需滚动；800×500 时滚动视口 420×452，底部扫码操作可纵向滚动访问，无横向滚动。
修改的 Python 文件通过 `py_compile`。按本轮协作要求没有重跑自动测试。

### Standards

通过，0 项未解决发现。复审建议的小窗口滚动、遮罩点击坐标和 `close()` 生命周期问题已修复。

### Spec

通过，0 项未解决发现。扫码登录使用主窗口内嵌 overlay，常规及小窗口都能访问扫码操作，关闭入口及后台轮询收尾齐全。
