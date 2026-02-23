# Champion Watchdog 定时任务说明

更新时间：2026-02-14

## 1. 目标

通过定时运行 Champion Watchdog，实现：

1. 自动生成告警清单（WARN/CRITICAL）。
2. 自动产出回滚建议（是否回滚、目标版本、建议动作）。
3. 持续输出审计产物，供 GUI/API 查询。

## 2. 脚本与产物

脚本：

- `/Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py`

默认产物：

- `/Users/fanjie/Documents/github/FiPro_1/monitoring/dashboards/champion_watchdog.json`
- `/Users/fanjie/Documents/github/FiPro_1/monitoring/dashboards/champion_watchdog.md`
- 审计运行：`/Users/fanjie/Documents/github/FiPro_1/.run/champion_watchdog_runs/*.json`

## 3. 手动运行

只基于历史健康检查做评估：

```bash
/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python \
  /Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py \
  --lookback-runs 20 \
  --consecutive-fail-critical 2 \
  --fail-rate-warn 0.25 \
  --fail-rate-critical 0.5 \
  --rollback-storm-critical 2
```

先执行一次健康检查再评估：

```bash
/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python \
  /Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py \
  --run-health-check \
  --health-check-json /Users/fanjie/Documents/github/FiPro_1/docs/champion_watchdog_health_check.sample.json
```

## 4. macOS（launchd）

保存为：`~/Library/LaunchAgents/com.fipro1.champion.watchdog.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.fipro1.champion.watchdog</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python</string>
    <string>/Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py</string>
    <string>--lookback-runs</string>
    <string>20</string>
    <string>--consecutive-fail-critical</string>
    <string>2</string>
    <string>--fail-rate-warn</string>
    <string>0.25</string>
    <string>--fail-rate-critical</string>
    <string>0.5</string>
    <string>--rollback-storm-critical</string>
    <string>2</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key><integer>12</integer>
      <key>Minute</key><integer>10</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>23</integer>
      <key>Minute</key><integer>10</integer>
    </dict>
  </array>
  <key>StandardOutPath</key>
  <string>/Users/fanjie/Documents/github/FiPro_1/.run/champion_watchdog_launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/fanjie/Documents/github/FiPro_1/.run/champion_watchdog_launchd.err.log</string>
</dict>
</plist>
```

加载与查看：

```bash
launchctl unload ~/Library/LaunchAgents/com.fipro1.champion.watchdog.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.fipro1.champion.watchdog.plist
launchctl list | rg com.fipro1.champion.watchdog
```

## 5. Win11（任务计划）

建议每天两次（12:10 / 23:10）执行：

```powershell
$py = "C:\Users\fanjie\Documents\github\FiPro_1\.venv\Scripts\python.exe"
$script = "C:\Users\fanjie\Documents\github\FiPro_1\scripts\champion_watchdog.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "$script --lookback-runs 20 --consecutive-fail-critical 2 --fail-rate-warn 0.25 --fail-rate-critical 0.5 --rollback-storm-critical 2"
$trigger1 = New-ScheduledTaskTrigger -Daily -At 12:10
$trigger2 = New-ScheduledTaskTrigger -Daily -At 23:10
Register-ScheduledTask -TaskName "FiPro1-Champion-Watchdog" -Action $action -Trigger @($trigger1, $trigger2) -Description "FiPro_1 champion watchdog monitor" -Force
```
