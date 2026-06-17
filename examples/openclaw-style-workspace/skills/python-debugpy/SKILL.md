---
name: python-debugpy
description: Set up and connect to a Python debugpy remote debug session.
---

Use debugpy to attach a debugger to a running Python process.

**Setup:**

1. Install debugpy if needed:
   ```bash
   uv add debugpy
   # or: pip install debugpy
   ```

2. Start the target script with debugpy:
   ```bash
   python -m debugpy --listen 5678 --wait-for-client your_script.py
   ```
   Or inject into an already-running process:
   ```python
   import debugpy
   debugpy.listen(5678)
   debugpy.wait_for_client()  # blocks until client connects
   ```

3. Connect from VS Code: add a launch config:
   ```json
   {
     "type": "python",
     "request": "attach",
     "connect": { "host": "localhost", "port": 5678 }
   }
   ```
   Or from the command line: `python -m debugpy --connect localhost:5678`

**Tips:**
- Use port 5678 by default; change if occupied.
- `--wait-for-client` pauses execution until debugger attaches.
- To debug a pytest run: `python -m debugpy --listen 5678 --wait-for-client -m pytest tests/`
- Check port is open: `bash` → `ss -tlnp | grep 5678`
