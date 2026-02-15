# Tushare 增量更新（定时）

## 脚本

- 脚本路径：`/Users/fanjie/Documents/github/FiPro_1/scripts/tushare_incremental_update.py`
- 默认行为：
  - 交易类接口：按交易日逻辑更新（默认更新“昨日”窗口）
  - 新闻/语料类接口：周末与非交易日也会更新
  - 每 3 个交易日做一次完整性检查（输出 QA 报表）

## 手动执行

```bash
/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python \
  /Users/fanjie/Documents/github/FiPro_1/scripts/tushare_incremental_update.py \
  --root /Volumes/dockcase2tb/database_all
```

---

## macOS（launchd）

保存为：`~/Library/LaunchAgents/com.fipro1.tushare.incremental.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.fipro1.tushare.incremental</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python</string>
    <string>/Users/fanjie/Documents/github/FiPro_1/scripts/tushare_incremental_update.py</string>
    <string>--root</string>
    <string>/Volumes/dockcase2tb/database_all</string>
  </array>

  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key><integer>12</integer>
      <key>Minute</key><integer>0</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>23</integer>
      <key>Minute</key><integer>0</integer>
    </dict>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/fanjie/Documents/github/FiPro_1</string>

  <key>StandardOutPath</key>
  <string>/Users/fanjie/Documents/github/FiPro_1/.run/tushare_incremental_launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/fanjie/Documents/github/FiPro_1/.run/tushare_incremental_launchd.err.log</string>

  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
```

加载：

```bash
launchctl unload ~/Library/LaunchAgents/com.fipro1.tushare.incremental.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.fipro1.tushare.incremental.plist
launchctl list | rg com.fipro1.tushare.incremental
```

---

## Windows（任务计划）

在 PowerShell 执行（建两个任务：12:00 和 23:00）：

```powershell
$python = "C:\Users\fanjie\Documents\github\FiPro_1\.venv\Scripts\python.exe"
$script = "C:\Users\fanjie\Documents\github\FiPro_1\scripts\tushare_incremental_update.py"
$root = "D:\database_all"

schtasks /Create /TN "FiPro1_Tushare_Inc_1200" /SC DAILY /ST 12:00 /F `
  /TR "`"$python`" `"$script`" --root `"$root`""

schtasks /Create /TN "FiPro1_Tushare_Inc_2300" /SC DAILY /ST 23:00 /F `
  /TR "`"$python`" `"$script`" --root `"$root`""
```

查看任务：

```powershell
schtasks /Query /TN "FiPro1_Tushare_Inc_1200" /V /FO LIST
schtasks /Query /TN "FiPro1_Tushare_Inc_2300" /V /FO LIST
```

---

## 结果与状态文件

- 最近一次运行摘要：`/Volumes/dockcase2tb/database_all/_meta/manifests/tushare_incremental_last_run.json`
- 增量状态（交易日计数）：`/Volumes/dockcase2tb/database_all/_meta/checkpoints/tushare_incremental_state.json`
- 完整性检查报表（每 3 交易日）：`/Volumes/dockcase2tb/database_all/_meta/qa/incremental_completeness_YYYYMMDD.csv`

