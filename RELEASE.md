# DD录播机 发布指南（NSIS + onedir 方案）

## 版本号规则

**唯一改动点**：[`version.py`](version.py) 里的 `__version__`。

```python
# version.py
__version__ = "1.0.1"   # ← 改成下一个版本号
```

构建脚本、注册表、安装器都会自动读取这个值。

---

## 1. 本地构建

```bash
# 前置：装好 pyinstaller、NSIS 3.x、C:\ffmpeg\bin\ffmpeg.exe
python build.py
```

6 步流水线：

| # | 步骤 | 产物 |
|---|---|---|
| 1 | 清理旧构建 | - |
| 2 | 编译 updater.nsi | `installer\DDRecUpdater.exe`（~50KB） |
| 3 | pyinstaller onedir | `dist\DD录播机\DD录播机.exe` + 子目录 |
| 4 | 复制 ffmpeg | `dist\DD录播机\ffmpeg\*.exe` |
| 5 | 验证 updater 已嵌入 | 检查 `dist\DD录播机\DDRecUpdater.exe` 存在 |
| 6 | 编译 installer.nsi | `installer\DD录播机_Setup_<VERSION>.exe` |

最终产物：
- **`installer\DD录播机_Setup_<VERSION>.exe`** — 上传到 GitHub Release 的安装器
- `installer\DDRecUpdater.exe` — 已被 PyInstaller 打进 onedir，不需要单独发

---

## 2. 上传到 GitHub Release

```bash
# 1. 提交代码（version.py + 其他改动）
git add version.py
git commit -m "release: v1.0.2"

# 2. 打 tag 并推
git tag v1.0.2
git push origin master --tags

# 3. 创建 Release 并上传 Setup.exe
gh release create v1.0.2 \
    "installer/DD录播机_Setup_1.0.2.exe" \
    --title "DD录播机 v1.0.2" \
    --notes "$(cat <<'EOF'
- 修复 XXX
- 新增 XXX
EOF
)"
```

**重要**：tag 格式必须是 `v<semver>`（如 `v1.0.2`），程序用 `tag_name` 比版本号。
`DD录播机_Setup_<VERSION>.exe` 命名必须带 `Setup` 字样（程序靠 `endswith(".exe") and "Setup" in name` 识别）。

---

## 3. 用户端自动更新流程

```
用户启动 v1.0.1
  ↓
[main.py] 500ms 后异步调 core.updater.check_update()
  ↓ 命中 GitHub API: https://api.github.com/repos/ivanhih/dd-rec/releases/latest
  ↓ 找到 v1.0.2 资源: DD录播机_Setup_1.0.2.exe
  ↓ 比版本号: 1.0.2 > 1.0.1 ✓
  ↓
[弹更新对话框 — 来自 main.py 启动检查]
用户点 [立即更新]
  ↓
下载到 %TEMP%\DDRec_Setup_new.exe（带进度条）
  ↓ 下载完成
core.updater.apply_update(setup_path):
  - 从注册表读安装路径: HKCU\Software\DD\DD录播机\InstallDir
  - 找内嵌的 DDRecUpdater.exe
  - subprocess.Popen 启动它: /UPDATER_PATH=... /INSTDIR=... /LAUNCH=1
  - 主程序 os._exit(0)
  ↓
[DDRecUpdater.exe 接管]
  - taskkill /F /T DD录播机.exe（兜底再杀一次）
  - 等 3 秒让文件锁释放
  - 静默安装: "DD录播机_Setup_1.0.2.exe" /S /D=<原安装目录>
  - 安装完成 → 启动新版本: "<原安装目录>\DD录播机.exe"
  ↓
v1.0.2 启动
```

---

## 4. 老用户从旧版（zip/onefile）直升

`main.py` 启动时调 `core.config.migrate_legacy_data()`：

- 如果 `%AppData%\DDRec\config.json` 不存在
- 且 exe 同目录（旧安装位置）有 `config.json` / `data.json` / `录播文件/` / `plugins/`
- → 把这些数据复制到 `%AppData%\DDRec\`
- → 旧位置留 `MIGRATED.txt` 标记，幂等

**触发条件**：仅在 frozen（打包后）状态生效。开发态运行时不会触发（避免误把项目根的 config 搬到 `%AppData%`）。

**注意事项**：
- 只支持 onefile / zip → NSIS onedir 的单次迁移
- 录播文件用 `_merge_dir` 合并（不覆盖已有文件）
- 旧位置的 `MIGRATED.txt` 删除后会再次触发迁移

---

## 5. 安装/卸载行为

### 安装

- 用户自选安装目录（默认 `C:\Program Files\DD录播机`）
- 可装到任意盘符（D 盘、E 盘等）
- 无 UAC（`RequestExecutionLevel=user`）
- 写注册表 `HKCU\Software\DD\DD录播机\{InstallDir, Version}`
- 控制面板「添加/删除程序」写入

### 卸载

- 删除安装目录 `C:\Program Files\DD录播机\`（或用户自定义路径）
- 清注册表 `HKCU\Software\DD\DD录播机` 和 `...Uninstall\DD录播机`
- **保留** `%AppData%\DDRec\`（用户配置、录播文件、插件）

---

## 6. 已知限制 / 待优化

| 项 | 当前 | 备注 |
|---|---|---|
| **差分更新** | 整包下载（~120MB） | 每次都下完整 Setup.exe。ffmpeg 体积大是主因 |
| **代码签名** | 未签名 | 首次下载会被 SmartScreen 拦截，提示「仍要运行」 |
| **GitHub 速率限制** | 匿名 60次/小时 | 大量用户同时检查可能限流；可加 access_token |
| **更新期间断电** | 下次启动会重新检测 + 重新下载 | `%TEMP%\DDRec_Setup_new.exe` 残留无害 |
| **跨用户/跨机器迁移** | 不支持 | `%AppData%` 是 per-user，新机器要重装 |

### 不在本次范围（后续可加）

- 代码签名证书（避免 SmartScreen）
- 7z 差分包（生成旧→新 delta，下载体积可降至几 MB）
- 静默后台下载（用户无需点确认）
- 多 channel 灰度发布

---

## 7. 调试技巧

### 查看 updater 日志

updater 失败时会弹 `MessageBox` 显示错误码和路径。如果想看更详细输出，把 `updater.nsi` 第 17 行的 `ShowInstDetails hide` 改成 `ShowInstDetails normal` 重编译。

### 手动测试更新流程

```bash
# 1. 装好旧版（v1.0.1）
DD录播机_Setup_1.0.1.exe

# 2. 改 version.py 为 1.0.2，构建新版
# version.py: __version__ = "1.0.2"
python build.py

# 3. 上传新 Setup.exe 到 GitHub Release v1.0.2
gh release create v1.0.2 installer/DD录播机_Setup_1.0.2.exe

# 4. 在旧版里点 [检查更新] → 弹对话框 → [立即更新]
```

### 测试老用户迁移

```bash
# 1. 模拟旧版：手动复制 config.json 到某个目录
mkdir E:\FakeOldInstall
copy config.json E:\FakeOldInstall\

# 2. 把新版 setup 装到 E:\FakeOldInstall\
DD录播机_Setup_1.0.1.exe → 选 E:\FakeOldInstall\

# 3. 启动新版 → 应该看到 migrate log
# 4. 检查 %AppData%\DDRec\config.json 已被复制
# 5. 检查 E:\FakeOldInstall\MIGRATED.txt 存在
```