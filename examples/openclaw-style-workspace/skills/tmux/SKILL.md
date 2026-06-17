---
name: tmux
description: Control tmux sessions, windows, and panes via bash commands.
---

**Session management:**
```bash
tmux ls                                # list sessions
tmux new -s mysession                  # new named session
tmux attach -t mysession               # attach to session
tmux kill-session -t mysession         # kill session
```

**Window management:**
```bash
tmux new-window -t mysession -n mywin  # new window
tmux list-windows -t mysession         # list windows
tmux select-window -t mysession:mywin  # switch window
```

**Pane management:**
```bash
tmux split-window -h -t mysession      # split horizontally
tmux split-window -v -t mysession      # split vertically
tmux list-panes -t mysession           # list panes
```

**Send keys to a pane:**
```bash
tmux send-keys -t mysession:0.0 "ls -la" Enter
tmux send-keys -t mysession:0.1 "C-c" ""    # send Ctrl-C
```

**Capture pane output:**
```bash
tmux capture-pane -t mysession:0.0 -p          # current content
tmux capture-pane -t mysession:0.0 -p -S -100  # last 100 lines
```

**Wait for a prompt (polling pattern):**
```bash
# Wait until pane output contains "$" (shell prompt)
for i in $(seq 20); do
  tmux capture-pane -t mysession:0.0 -p | grep -q '\$' && break
  sleep 0.5
done
```

**Tips:**
- Target format: `session:window.pane` — e.g. `mysession:0.0` is session "mysession", window 0, pane 0.
- Use `tmux send-keys` to interact with interactive CLIs (REPLs, ssh sessions).
- Always `capture-pane` after sending keys to verify the command ran.
