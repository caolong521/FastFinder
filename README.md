# FastFinder

FastFinder 是一个基于 **Python + PySide6 + SQLite + RapidFuzz** 的 Windows 本地文件 / 文件夹快速搜索工具。

目标不是做一次性的“扫描脚本”，而是做一个可以长期驻留托盘、随时呼出、搜索速度快、数据完全保存在本机的开源桌面工具。

## 主要功能

### 搜索

- SQLite 长期索引：常用目录打开即搜
- 指定目录临时搜索：直接粘贴路径，不写数据库
- 文件与文件夹统一搜索
- 输入即搜索，默认 120ms debounce
- RankingEngine 相关度分层
- RapidFuzz 拼写容错
- SQLite FTS5 可用时自动预筛候选
- 文件类型、时间、大小、路径过滤
- 支持 `ext:` / `type:` / `path:` / `after:` / `before:` / `size:`
- 相关度 / 修改时间 / 创建时间 / 大小 / 名称 / 类型 / 路径排序

### 高频使用体验

- 系统托盘后台运行
- 自定义主窗口全局快捷键，默认 `Ctrl+Alt+Space`
- 独立“小搜索窗”快捷键，默认 `Ctrl+Alt+F`
- 单实例运行：重复启动时直接唤醒已经运行的 FastFinder
- 搜索历史 + 搜索框自动补全
- 收藏文件 / 收藏文件夹
- 常用文件自动轻量加权
- 搜索结果可以直接拖到桌面、资源管理器或其它支持文件拖入的软件

### 索引管理

- 多个长期索引目录
- 后台建立 / 更新索引
- watchdog 监听新增、删除、移动、修改
- 索引窗口关闭后后台任务继续
- 可按目录清空索引数据
- 可删除长期索引存档，带二次确认
- 删除 FastFinder 索引不会删除真实磁盘文件

### Windows 开机自启

开机自启 **默认关闭**。

只有用户在：

```text
设置 → Windows 开机自启
```

主动勾选并保存后才会启用。

FastFinder 使用当前用户的 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，不写系统级 HKLM。正常情况下不需要管理员权限；如果公司组策略或安全软件禁止修改启动项，程序会提示失败，不会绕过系统限制。

## 安装

```powershell
cd C:\path\to\FileFinder
python -m pip install -r requirements.txt
```

SQLite 不需要额外安装，Python 自带 `sqlite3`。

## 运行

开发 / 看控制台报错：

```powershell
python main.py
```

或双击：

```text
start_debug.bat
```

日常无黑色控制台运行：

```text
start.bat
```

## 第一次使用

可以选择两种方式。

### 方式 A：长期索引

适合经常使用的目录：

```text
C:\Projects
D:\Work
D:\Documents
```

点击 `索引库` → `+ 添加长期索引`。

### 方式 B：指定目录临时搜索

切换到：

```text
📁 指定目录
```

直接粘贴目录路径并按 Enter。扫描结果只保存在本次进程内存中，不写 SQLite；如果以后经常用，可以再点击“保存到索引库”。

## 搜索例子

```text
modbus
modbus config
QueryData
```

只搜 DLL：

```text
modbus ext:dll
```

只搜文件夹：

```text
universal type:folder
```

限定路径：

```text
config path:protocol
```

日期：

```text
log after:2026-09-01
```

大小：

```text
video size:>100mb
```

## 快捷键

完整说明见：

```text
SHORTCUTS.md
```

默认：

```text
Ctrl + Alt + Space    显示 / 隐藏主窗口
Ctrl + Alt + F        呼出小搜索窗
Ctrl + H              搜索历史
Ctrl + B              收藏
Ctrl + I              索引库
```

## 默认忽略目录

- `node_modules`
- `.git`
- `.vs`
- `.idea`
- `__pycache__`

`bin`、`obj`、`venv`、`.venv` 默认不忽略，因为开发场景经常需要搜索 DLL / EXE / Python 环境文件。

## 本地数据

数据库：

```text
data/fastfinder.db
```

设置：

```text
config/settings.json
```

数据库除文件/文件夹索引外，还保存搜索历史、收藏和打开次数。所有数据均保存在本地。

## 技术栈

- Python 3.12+
- PySide6
- SQLite / FTS5
- RapidFuzz
- watchdog
- Windows `RegisterHotKey`
- Windows HKCU Run 启动项
- Qt Local Server 单实例 IPC

## License

FastFinder 使用 **MIT License** 开源。

你可以在遵守 MIT License 的前提下使用、复制、修改、合并、发布、分发、再许可或销售本软件；发布副本或重要代码片段时，需要保留原版权声明和 MIT License 许可声明。

完整许可证见：

```text
LICENSE
```

软件、开发者与隐私说明见：

```text
SOFTWARE_INFO.md
```
