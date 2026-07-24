---
name: legacy-emulation-setup
description: "Getting legacy or version-pinned software running under an emulator/VM (QEMU, DOSBox, retro OS installs): matching the exact emulator version the task names, supplying old shared libraries, choosing compatible device models, and wiring the control/monitor interface the verifier drives. Use for 'install/boot <old OS or app> compatible with <specific emulator version>' tasks."
---
### The version pin in the prompt is the answer, not flavor text
*   **When the task says "compatible with <emulator> version X", treat that version as a hard requirement.** The stock emulator from `apt-get` is usually a much newer major version, and newer versions frequently break old guests — e.g. booting to a black/garbled framebuffer so the verifier's visual/keyboard test never sees the desktop. Getting the *named* version is often the whole task.
*   **Two reliable ways to obtain a pinned version:** (1) download the prebuilt old package (from an older distro pool), extract the binary, fetch its old shared-library dependencies into a local dir, and run with `LD_LIBRARY_PATH` pointing there; (2) compile that version from source — check whether the build deps are already installed in the image (they often are), then `./configure --target-list=... && make`. Source build is slower but avoids flaky downloads.
*   **Pick the device models the old guest expects, not the modern defaults.** Legacy guests often need an old graphics adapter (e.g. `-vga cirrus`), IDE rather than virtio disks, and a period-appropriate machine type. The default modern `-vga`/virtio devices are a common cause of no-display or no-boot.

### Wire the exact control interface the verifier uses
*   **The grader usually drives the guest through a specific control channel — match its hardcoded path/port.** E.g. a keyboard/visual test may connect to an HMP monitor socket at a fixed path; you must launch the emulator exposing that exact socket (`-monitor unix:/tmp/...,server,nowait`) or the test can't send input. Read the verifier to find the path/port it expects.
*   **If a web/remote interface is required** (e.g. noVNC over websockify behind nginx on a given port), stand it up on the exact port and confirm it serves before finishing.

### Drive the guest's console with a PERSISTENT serial + an expect script
Configuring a guest from inside (login, install packages, edit config, enable a
service) means typing into its console and reading the results — the #1 time sink
and failure point in these tasks. Do it robustly:
*   **Boot QEMU with the serial console on a PERSISTENT socket, not stdio/pty tied to your shell.** Use `-nographic` plus `-serial telnet:127.0.0.1:<port>,server,nowait` (or a TCP/unix socket). Then the VM keeps running independently and you can attach, detach, and re-attach a driver to it — instead of losing the console every time a foreground command returns. Constantly relaunching QEMU and switching serial transports (stdio→pty→telnet→tcp) is how trials burn their whole budget; pick the persistent-socket form once and commit.
*   **Drive the console with a single `expect` script, not one-command-at-a-time keystrokes.** Hand-stepping the login + setup over many turns is far too slow (each turn is an LLM round-trip) and races the boot. Write ONE expect script that: connects to the serial socket, handles the `login:` prompt (send the user, handle no-password / already-logged-in), sets a unique `PS1` or echoes a unique marker after each command so it can reliably wait for completion, then runs the whole setup sequence (install packages, edit config files, set passwords, enable+start the service) end to end.
*   **Prefer editing config files directly over interactive setup wizards.** For SSH, write `/etc/ssh/sshd_config` (`PermitRootLogin yes`, `PasswordAuthentication yes`), set the password via `chpasswd`/`passwd`, install the server (`apk add openssh`), and enable+start it — driving each as a marker-terminated command, rather than relying on a distro `setup-*` TUI that's hard to automate.
*   **Expose the service to the host via QEMU user-net port forwarding** (`-netdev user,id=n,hostfwd=tcp::<hostport>-:<guestport>`) so the grader's `ssh -p <hostport> localhost` reaches the guest's port.

### Verify the way the grader will observe it
*   **The grader observes the guest externally** (screenshot/OCR of the framebuffer, sending keystrokes, checking a file the guest wrote). Reproduce that: boot headless, take a screenshot, confirm the expected screen is actually rendered (not black/garbled) before assuming success.
*   Boot is slow and stateful — drive installs non-interactively where possible, snapshot/save progress, and budget for multi-minute boots; don't conclude failure from a slow first boot.
