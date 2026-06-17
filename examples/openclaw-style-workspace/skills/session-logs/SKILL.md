---
name: session-logs
description: Read and search workspace log files.
---

Use `read`, `grep`, and `bash` tools to inspect workspace log files.

**Find log files:**
```bash
find . -name "*.log" -o -name "*.log.*" | sort
ls -lht logs/ 2>/dev/null || echo "no logs/ dir"
```

**Read recent log entries:**
```bash
tail -n 100 logs/app.log
tail -f logs/app.log          # follow (use with timeout in bash tool)
```

**Search logs:**
```bash
grep -n "ERROR" logs/app.log
grep -n "ERROR\|WARN" logs/app.log | tail -50
grep -C3 "exception" logs/app.log   # 3 lines of context
```

**Filter by time range** (if logs have timestamps):
```bash
grep "2025-06-10 14:" logs/app.log  # logs from 14:xx
awk '/2025-06-10 14:00/,/2025-06-10 15:00/' logs/app.log
```

**Count error types:**
```bash
grep "ERROR" logs/app.log | grep -oP '\[.*?\]' | sort | uniq -c | sort -rn
```

**Search psi-agent workspace session history:**
Use `session_search` tool:
```
session_search(query="error", path=".")
```

**Tips:**
- Log files may be in `logs/`, `~/.local/share/`, `/var/log/`, or next to the running binary.
- Check `PSI_WORKSPACE_DIR` environment variable for workspace root.
- For very large logs, use `grep` instead of `read` to avoid loading the whole file.
