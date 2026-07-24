---
name: sysadmin-infra
description: "System administration and infrastructure: QEMU/VMs, web servers (nginx/pypi), mail, TLS certs, OS install, headless terminals, services in constrained containers. Use for configuring/running a service, server, or VM."
---
### Operating in Constrained Environments
* Do not assume standard sysadmin tools (`ps`, `ss`, `netstat`, `systemctl`, `mount`, `sudo`) are present in minimal containers.
* Substitute process liveness checks by scanning `/proc/[0-9]*/cmdline` or `/proc/*/comm`.
* Substitute port listening checks by reading `/proc/net/tcp` (note that local ports are hex-encoded) or probing via `curl`, `nc`, or bash `/dev/tcp` redirection.
* Handle missing `systemd` by invoking service binaries directly (e.g., `service <name> start` or daemon executables) and verifying PID files.
* Bypass `mount` permission errors (EPERM) by using userspace extraction tools (e.g., `7z`, `xorriso`, or Python libraries) to read ISOs and archives.

### QEMU & VM Automation
* Force serial console output by extracting the kernel/initrd from ISOs and booting with `-kernel`, `-initrd`, and `-append "console=ttyS0 ..."` (concatenate microcode blobs to the initrd if needed).
* Expose serial consoles via TCP/Unix sockets using `-serial` or `-chardev` with `server,nowait` to prevent QEMU from blocking on startup waiting for a client.
* Daemonize VMs cleanly using `-daemonize` and `-pidfile`; never combine `-daemonize` with `-nographic`.
* Account for slow boot times (especially under TCG when `/dev/kvm` is missing) by polling the serial log for readiness sentinels instead of using fixed sleeps.
* Wake up telnet/serial connections by sending an initial carriage return (`\r`) to draw out the prompt before expecting output.

### Shell & PTY Automation
* Avoid fragile regex matching on prompts; inject a custom sentinel (e.g., `export PS1='READY# '`) and disable echo (`stty -echo`) to bypass terminal control sequences.
* Send raw bytes when driving interactive shells programmatically to preserve control characters (Ctrl-C/Ctrl-D) and avoid newline mangling.
* Tolerate ANSI bracketed-paste sequences and CRLF line endings when asserting against raw PTY output, or render through a virtual screen library.

### Service Provisioning & Integration
* Always validate configuration files (e.g., `nginx -t`) before starting or reloading services to catch syntax errors early.
* When linking services (e.g., MTAs and mailing list managers), start the data-generating service first so dependent maps and sockets exist before the consumer starts.
* Run daemons under their dedicated service users using `runuser -u <user> -- <command>` if `sudo` is unavailable.
* Generate self-signed certificates in a single step using `openssl req -x509 -newkey rsa:<size> -nodes` to bypass interactive prompts and passphrase requirements.

### Python & Dependency Management
* Probe the environment for pre-installed Python toolchains (`pip`, `build`, `wheel`) before assuming they exist; install missing build tools in one shot.
* When migrating legacy Python, update standard library imports (e.g., `configparser`), replace Python 2 idioms, and remove deprecated scientific types (e.g., `np.float`).
* Clean up only regenerable scratch artifacts (e.g., `__pycache__`, transient venvs) before exiting; leave actively served artifacts intact.
