---
name: node-inspect-debugger
description: Debug Node.js processes using --inspect and Chrome DevTools.
---

**Start with inspector:**

```bash
node --inspect-brk your_script.js    # pauses at first line
node --inspect your_script.js        # runs normally, debugger can attach later
```

For npm scripts:
```bash
node --inspect-brk $(which npm) run dev
```

For tests:
```bash
node --inspect-brk $(which jest) --runInBand tests/
```

**Connect:**

- Open Chrome/Edge → `chrome://inspect` → click "Open dedicated DevTools for Node"
- Or use VS Code launch config:
  ```json
  {
    "type": "node",
    "request": "attach",
    "port": 9229
  }
  ```

**Check the inspector is listening:**
```bash
curl http://localhost:9229/json
```

**Tips:**
- Default port is 9229; use `--inspect=0.0.0.0:9230` to change host/port.
- `--inspect-brk` is preferred for scripts that exit immediately.
- For TypeScript with ts-node: `node --inspect-brk -r ts-node/register src/index.ts`
- Kill a stuck inspector: `bash` → `lsof -ti:9229 | xargs kill`
