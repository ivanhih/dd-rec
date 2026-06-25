# DD录播机 发布指南

## 版本号规则

每次发布前，修改以下文件的版本号：

1. `core/updater.py` - `CURRENT_VERSION`
2. `bilirec.spec` (可选)

## 1. 打包 Portable 版本

```bash
# 1. 进入项目目录
cd C:\Users\user\PycharmProjects\bilirec

# 2. 清理旧构建
rmdir /s /q dist 2>nul
rmdir /s /q build 2>nul
rmdir /s /q __pycache__ 2>nul

# 3. 打包
pyinstaller bilirec.spec

# 4. 复制 ffmpeg（如果没有自动打包）
mkdir dist\ffmpeg
copy C:\ffmpeg\bin\ffmpeg.exe dist\ffmpeg\
copy C:\ffmpeg\bin\ffprobe.exe dist\ffmpeg\

# 5. 压缩发布
cd dist
powershell Compress-Archive -Path "*" -DestinationPath "DD录播机_v1.0.0_Portable.zip"
```

## 2. 创建安装包

需要安装 [Inno Setup](https://jrsoftware.org/isinfo.php)

```bash
# 1. 打包 Portable 版本（见上）

# 2. 编译安装包
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

输出：`installer/DD录播机_Setup_1.0.0.exe`

## 3. 发布到 GitHub

### 创建 Release

1. 打开 https://github.com/ivanhih/bilirec/releases
2. 点击 **Draft a new release**
3. 填写：
   - **Tag**: `v1.0.0`
   - **Title**: `DD录播机 v1.0.0`
   - **Description**: 更新说明
4. 上传文件：
   - `dist/DD录播机.exe` (必须)
   - `dist/DD录播机_v1.0.0_Portable.zip` (可选)
5. 点击 **Publish release**

### 自动更新

发布后，用户打开软件时会自动检查更新：
- 读取 GitHub Releases API 获取最新版本
- 比较版本号
- 弹出提示对话框
- 用户确认后下载并自动安装

## 4. 目录结构

打包后的目录结构：

```
DD录播机/
├── DD录播机.exe          # 主程序
├── ffmpeg/              # ffmpeg 工具
│   ├── ffmpeg.exe
│   └── ffprobe.exe
├── _internal/           # PyInstaller 内部文件
└── plugins/             # 插件目录（用户下载的插件）
```

## 5. 配置文件位置

- **Portable 模式**: `config.json` 在 exe 同目录下
- **安装模式**: `config.json` 在 `%APPDATA%/DD录播机/` 下

## 常见问题

### Q: 打包后缺少 ffmpeg
确保 `bilirec.spec` 中的 `FFMPEG_SOURCE` 路径正确，或者手动复制 ffmpeg 文件夹到 dist 目录。

### Q: 插件无法加载
确保 `bilirec.spec` 的 `hiddenimports` 包含 `plugins` 相关模块。

### Q: 自动更新失败
检查 GitHub Release 是否正确上传了 .exe 文件，且文件名以 .exe 结尾。
