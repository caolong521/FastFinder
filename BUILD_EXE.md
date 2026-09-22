# FastFinder Windows 打包说明

## 推荐：ONEDIR 极速启动版

FastFinder 是 PySide6 桌面工具。`onefile` 会在每次启动时把 Python、Qt DLL、插件等解压到临时目录，因此“只有一个 EXE”会换来明显更慢的启动。

正式日常使用和 GitHub Release 推荐：

```text
build_exe.bat
```

输出：

```text
dist/
└─ FastFinder/
   ├─ FastFinder.exe
   └─ _internal/ ...
```

**发布时要打包整个 `dist/FastFinder` 文件夹，不要只复制 EXE。**

建议 GitHub Release 压缩成：

```text
FastFinder-v3.0-Windows-x64.zip
```

用户解压后只需要双击 `FastFinder.exe`。

## 可选：单文件便携版

如果你确实需要一个独立 EXE：

```text
build_onefile.bat
```

输出：

```text
dist/FastFinder.exe
```

这个版本更方便复制，但 PySide6 每次启动需要解包，**启动速度会明显慢于 ONEDIR**。不建议把它作为 FastFinder 的默认 Release。

## 数据位置

无论哪种打包模式，FastFinder 的运行数据都放在 `FastFinder.exe` 所在目录旁：

```text
data/fastfinder.db
config/settings.json
logs/startup.log
```

因此数据库、收藏、历史和设置不会写进 PyInstaller 临时目录。

## 图标

打包配置包含：

```text
resources/fastfinder.ico
```

并同时使用 Windows AppUserModelID、Qt 窗口图标和原生 HWND 图标，使 EXE、窗口、任务栏、Alt+Tab、托盘尽量保持一致。

## 启动性能排查

启动后查看：

```text
logs/startup.log
```

V3.0 会记录 PySide6 导入、SQLite、MainWindow 模块导入、可见 UI 构建等阶段。辅助窗口、快速搜索窗、watchdog、自动索引调度均改为延迟加载，不应再阻塞主窗口首次显示。
