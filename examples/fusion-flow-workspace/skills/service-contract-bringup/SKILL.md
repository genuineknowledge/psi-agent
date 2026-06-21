---
name: service-contract-bringup
description: "Stand up a long-running service/daemon/server/VM that exposes an exact, externally-probed contract (a literal port, URL/endpoint, socket or file path, config location, log format, or process cmdline) and leave it running, when a grader probes that surface out-of-band (connects to the port, curls the URL, clones/pushes a repo, installs from the index, reads the file, pgreps the process, hashes an artifact). Use when the task verb is run/start/serve/set up/configure/launch/host/expose/publish/deploy, the success condition is a connection or request succeeding, and the service must remain reachable after setup; covers web servers, package indexes, git+http and push-to-deploy endpoints, VM/emulator control planes, and content-publishing pipelines."
---
# Skill: service-contract-bringup (stand up a long-running service to an exact, externally-probed contract)

## When this skill applies

Load this skill whenever the task has this **shape**:

> Bring up a **long-running service / daemon / server / VM** that exposes a
> **specific observable contract** — a port to listen on, a URL/endpoint to
> answer, a socket or file path to create, a config file at an exact location, a
> log in an exact format, a process whose command line carries certain flags — and
> **leave it running**. A grader then probes that contract **out-of-band**: it
> connects to the port, `curl`s the URL, installs from the index, reads the file,
> `pgrep`s the process, or hashes an artifact. It never inspects *how* you built
> it; it checks *what is observable*.

**Match signals (any of):**
- The spec names **exact ports / paths / endpoints** ("listen on port 8080",
  "serve `/var/www/html`", "socket at `/tmp/…`", "config in `/etc/.../X.conf`",
  "available at `http://server:PORT/…`").
- The verb is *run / start / serve / set up / configure / launch / host / expose*
  a service, and the success condition is a connection or request succeeding.
- The spec stresses the service must be **reachable** / **left running** /
  "accessible after setup is complete".
- It names a **specific version or compatibility constraint** for the tool/image
  ("compatible with version X.Y", "using <specific tool>").
- A control or test channel is required "for automated testing" / "programmatic"
  access, separate from the human-facing one.

The defining tell is the combination **"produce a running process" + "it must
satisfy an exact, externally-observable surface" + "the grader connects from the
outside."** When you see that, this skill applies regardless of whether the
service is a web server, a package index, a VM, a git endpoint, or an IPC plane.

## The single most important insight

**The deliverable is the observable runtime contract, not the steps you took. The
grader is an out-of-band client: it will touch a specific, literal surface — every
port number, socket/file path, URL, process-cmdline token, config location, and
output format — and check it exactly. Your job is to reproduce that surface
*literally* and keep the process alive.** The single hardest-won rule, stated up
front: **"done" means the grader's read-probe passes against the surface AT
HAND-OFF — not that your commands succeeded, not that an earlier probe was green,
not that the system looks tidy. Your last action before finishing must be to re-run
that probe and confirm it still passes (step 8).** Three corollaries dominate
pass/fail on this class:

1. **Bind the EXACT surface, not an equivalent one.** A port, path, or endpoint
   named in the prompt or hardcoded in the verifier is a literal contract. The
   "functionally equivalent" alternative — a different port, a neighboring socket
   path, a working but differently-located config file — **fails**, because the
   grader connects to the named one. If your setup naturally lands elsewhere,
   bridge it to the exact surface (bind there, or symlink/alias the path the
   grader expects).

2. **Honor named version / compatibility constraints; the default often fails
   silently.** When the spec pins a tool/image/protocol version ("compatible with
   X.Y", "using tool T"), the modern default frequently does **not** satisfy the
   grader and fails *without an error* — it starts, but produces the wrong
   observable behavior (a blank screen, a 4xx, an incompatible artifact). Obtain
   the specified version even if it costs extra work (download a pinned package,
   extract it with its old libraries, or build it from source — build deps are
   often pre-installed precisely because this is expected).

3. **Leave it running, and keep probed artifacts — AND your verified state —
   immutable.** The process IS the deliverable; never tear it down as a "cleanup"
   step. If the grader reads or **hashes an input artifact**, do not mutate it —
   serve it read-only or via copy-on-write/snapshot so its bytes (and hash) stay
   identical. **Equally: once your self-probe is green, do NOT "clean up" the state
   you just verified.** A recurring, measured self-kill (observed 15/15 on one
   instance) is the agent self-testing successfully, then deleting its self-test
   artifacts / "resetting to a clean state for the grader" — and thereby destroying
   the working pipeline. This is driven by a **wrong mental model of the grader**:
   "if I leave my test data behind, the grader's own write will conflict / find
   'nothing to do'." It won't — the grader runs its **own** client workflow in its
   **own** workspace and does not inspect your leftovers; a pre-existing record is a
   harmless base for its write. **The contract is a LIVE, ALREADY-WORKING surface —
   submit it exactly as verified. The only safe cleanup is your separate client
   scratch dir, never the served state, the receiver, or the trigger/hook.**

   **Do NOT reset the receiver/source to an "empty, ready" state for the grader.**
   The most damaging form of this mistake is reasoning: *"if I leave my deployed
   artifact and my commit/record behind, the grader's own write will fail with
   'nothing to commit' / a conflict — so I'll wipe the served file and reset the
   receiver to empty so the grader starts clean."* This is the single dominant
   measured killer and it is **always wrong**, for two independent reasons, either of
   which alone fails you:
   (a) **The read-probe may read the artifact's CURRENT state directly, and never
   re-run your pipeline at all.** You cannot assume the grader's write will
   re-materialize the artifact — the check may simply read what is deployed *right
   now*. If you emptied it, the probe reads nothing → failure. **Your
   already-deployed, already-verified artifact IS the graded deliverable; leave it
   in place.**
   (b) Even if the grader does perform its own write, a pre-existing artifact/commit
   does **not** make it fail — it writes into its own workspace and overwrites /
   fast-forwards; "nothing to commit" cannot be caused by your leftovers.
   So there is **no** scenario where wiping the deployed state helps, and a clear one
   where it is fatal. After a green self-probe, the deployed artifact, the receiver's
   refs/records, the hook, and the server must all be left **exactly as the probe
   left them**. Resetting "to look clean" / "so the grader can run from scratch"
   destroys the very thing being graded.

4. **Provision the identity the grader connects AS — it usually does not exist by
   default.** When the grader reaches the service over an authenticated channel (an
   SSH login, a specific OS account, a role/key/password), it connects as **one
   specific identity**, and that identity must already EXIST with working
   credentials before the grader arrives. The base image frequently ships *without*
   that account, or without the auth method (password/key) enabled — so the daemon
   starts, your own root-side test passes, and the grader's connection is
   nonetheless **refused**, the write never lands, and the read-probe gets nothing.
   You will not discover this unless you self-probe as that exact identity (step 5).
   The fix is mechanical but mandatory: **create the account the grader logs in as,
   set the exact credential it presents, enable the auth method it uses, and make
   every resource on the deploy path (receiver, hook, served dir) writable by that
   identity** — not by root, and not by a placeholder account you invented. A
   pipeline that only works when *you* (root / a local file path) drive it, but not
   when the grader's account drives it over the real channel, fails every time.

   **Do NOT let a reassuring prompt cancel this. The single most common way this
   step gets skipped: the prompt says something like "I'll set up login / auth is
   handled / you don't have to worry about that," and you conclude the account is
   someone else's job and move on.** That reasoning is a trap and it fails the task.
   A prompt that hand-waves authentication is *not* evidence the account exists right
   now — empirically, "login is handled" almost always means **the account does NOT
   exist yet in this image**, and the only thing "handled" is that you're allowed to
   create it however you like. The contract item "the grader's identity can actually
   connect and write" is **yours to guarantee and yours to verify**, no matter what
   the prompt says about login. The rule that overrides the prompt: **you may only
   treat login as "ready" once you have, with your own hands, connected as the
   grader's exact identity over the real channel and completed a full write
   round-trip. If you cannot point to a self-probe where you authenticated as that
   identity and the write landed, login is NOT ready — so make it ready (create the
   account, set the credential, enable the auth method) yourself.** Reassuring prose
   never substitutes for an executed, identity-exact self-probe.

> This is the discovery that most often separates passing from failing on this
> class. Do not optimize the *method*; reverse-engineer the *contract* and match
> it to the byte/port/path.

## A decision procedure (run this for any instance)

1. **Enumerate the exact contract surface — write it down as a checklist.** From
   the prompt *and* the verifier, list every literal: ports, bind addresses,
   URL paths, socket/file paths, config-file locations, required cmdline flags,
   content/log formats, artifact hashes, **and the identity the grader connects as
   (the account/user, the credential it presents, the auth method)**. These are your
   acceptance tests.
2. **Read the verifier if present** (`tests/`, `solution/`, `task.toml`,
   `run-tests.sh`, `*_test.py`). It usually shows *exactly* how it probes:
   `pgrep`, `netstat`/`ss` for a port, `socket.connect`, `curl`/`urlopen` a path,
   `pip install --index-url`, reading `/proc/<pid>/cmdline`, hashing a file. Each
   probe is a contract item you must satisfy literally — including hardcoded paths
   the prompt never mentioned. **If it connects over an authenticated channel, note
   the exact account + credential it uses** (e.g. who it logs in as, what password/
   key it sends); that login identity is itself a contract item.
3. **Provision the grader's identity before you build the pipeline.** Check whether
   the account the grader logs in as already exists (`id <user>`, `/etc/passwd`); the
   base image usually does NOT have it. If absent, create it, set the exact credential
   the grader presents, enable the auth method (e.g. turn on password auth), and plan
   for every resource on the deploy path (receiver, hook, served dir) to be writable
   by **that** identity — via ownership or a shared group, never root-only. Do this
   first so the rest of the pipeline is built and tested under the real identity, not
   under root. **A prompt saying "login is handled / I'll set up auth / don't worry
   about that" does NOT exempt you from this — it almost always means the account
   does not exist yet and you are cleared to create it. Never skip provisioning on the
   strength of reassuring prose; the only proof login is ready is a self-probe where
   you connected as that identity and a write landed (step 6).** (See corollary 4.)
4. **Resolve version/compatibility constraints.** If a specific version is
   named, plan how to obtain it (pinned package + libs, or build from source)
   before writing the launch command. Verify `--version` matches.
5. **Bring the service up bound to the exact surface.** Prefer the simplest launch
   that satisfies the contract; add only the flags the grader checks.
6. **Self-probe the way the grader will, from outside the process, using the
   grader's exact client identity/credentials.** `pgrep` it; `ss`/`curl`/`connect`
   the port/endpoint; send the control command; confirm the exact content/format;
   re-hash any input artifact to prove it's unchanged. **If the grader authenticates
   as a specific user/role, you MUST self-probe as that exact identity over the same
   channel — not as root, not from a local file path, not as a placeholder account
   you invented.** This is non-negotiable: a root-side or file-path test "succeeds"
   while the grader's real connection is refused (missing account, wrong credential,
   auth method off, deploy dir not writable by that identity) — and that gap stays
   **completely invisible** until you exercise the exact identity. Drive the *full*
   client round-trip as the grader (connect → write/push → read-probe), fix until
   every checklist item passes under that identity.
7. **Leave everything running and DO NOT clean up the verified state.** Daemonize or
   background the service; do not stop it, its proxy, or its bridge at the end. Once
   the self-probe is green, **stop** — do not delete deployed content, reset
   receivers, clear logs/refs, or "tidy up for the grader." Submit the live, working
   surface as-is. The only thing safe to remove is your own separate client scratch
   workspace. **Do not assume the grader will re-deploy the artifact for you: the
   check may read the deployed artifact's current state directly. A generic
   "minimal state changes / leave no extra files" instinct does NOT apply to the
   deliverable you just deployed — that artifact is exactly what's being graded, so
   leaving it in place IS the correct final state. See corollary 3.**
8. **MANDATORY FINAL GATE — re-run the read-probe as the literal last action, and do
   not finish until it passes.** Whatever you did after your first green probe
   (cleanup, resets, "tidying up"), the only state that counts is the state **right
   now**, at hand-off. So immediately before declaring the task complete, run the
   grader's read-probe one more time against the live surface — the exact probe the
   grader uses (e.g. `curl <the served URL>`, connect the port, read the file/socket)
   — and confirm it returns the exact expected content. **If it does not (404, empty,
   refused, wrong bytes), you are NOT done: re-deploy / re-materialize the artifact
   and bind/restart the service until this final probe passes, then stop without
   touching anything else.** This gate exists specifically to catch the dominant
   failure on this class: an agent that deployed correctly, verified green, then
   "reset to a clean state" and shipped an empty surface. Treat a green *final* probe
   — not a green *earlier* probe, not "my commands succeeded", not "the state looks
   tidy" — as the only definition of done. **This overrides any general instruction
   to minimize state changes or leave the system pristine:** the deployed artifact
   must be present and serveable at the moment you hand off, full stop.

## Why this generalizes (same steps, different domains)

The *service* differs; the *discipline* is identical.

| Concern | VM / emulator (display + control plane) | Package / index server | Git + HTTP publish | Web server + logging |
|---|---|---|---|---|
| Exact listen surface | display port + web port + **control socket at a hardcoded path** | TCP port + index URL path | ssh endpoint + HTTP port + served URL path | TCP port + served root |
| Version / compat constraint | "compatible with <pinned emulator version>" → default version renders nothing | package **name + version** exact | git transport over the expected protocol | log-format tokens / directive names exact |
| Immutable probed artifact | base disk image is hashed → snapshot/COW, never write back | built wheel/sdist served verbatim | pushed file served byte-for-byte | served file content exact |
| Control / test channel | programmatic key/command channel separate from the human UI | `pip install` round-trip | `git push` round-trip | request that exercises the config |
| Content provenance (who writes what the probe reads) | guest OS renders the framebuffer; you only service IO | build step emits the artifact; you serve it verbatim | **a receive-side hook (`post-receive`) materializes the served content on every push — you wire the pipeline, the push produces the bytes** | you place the file, server serves it |
| Self-probe like the grader | `pgrep`; port up; send a control command; re-hash image | `pip install --index-url …` | `curl http://…/file` | `curl localhost:PORT` |
| Leave running | daemonized process | server daemon | post-receive hook + web server | service restarted, stays up |

The "must be reachable / left running" requirement is the signal that a contract
will be probed out-of-band. Whenever you see it, reach for *enumerate-surface →
match-literally → self-probe → leave-running*, not "did my command succeed?"

<!-- APPEND_BELOW -->

## Example domain: an emulator/VM with a display + a programmatic control plane

One concrete instantiation (a retro-OS-in-an-emulator task). The contract had
several literal surfaces, each independently probed:

- **A display the grader can watch.** A remote-framebuffer display on an exact
  display/port, plus a web front-end on an exact port (a reverse proxy → a
  websocket bridge → the framebuffer). Verifier asserts both ports listen, and
  takes screenshots to confirm the GUI actually rendered.
- **A pinned tool version is mandatory.** The image only renders correctly under
  one *old* emulator version named in the prompt; the distro's current version
  starts but shows a black screen, so the screenshot-diff check fails. You must
  fetch/extract/build that pinned version. With it (and the era-appropriate
  display adapter), the guest boots straight to its desktop unattended.
- **A control plane at a hardcoded socket path.** The grader sends key events by
  connecting to a monitor/IPC socket at a *fixed* path and asserting the screen
  changes ≥ a threshold. A control socket at any *other* path — even a working one
  — is invisible to it; bind the exact path (or symlink to it).
- **An immutable disk artifact.** The grader hashes the base image and extracts
  core files from it; run the guest in snapshot / copy-on-write mode so the base
  bytes never change, or both the hash and file-integrity checks fail.
- **Leave it running.** The emulator, the proxy, and the bridge must all still be
  up when the grader runs.

General lesson from this instance: when a task says "compatible with <specific
version>" and gives you a pre-built artifact, treat the version line as a hard
requirement, and expect a *hidden* control-surface path inside the verifier that
the prompt only gestures at ("programmatic", "for automated testing").

## Example domain: a network content/endpoint server (package index, git+http, web root)

When the service is "serve specific content at a specific address" the same
discipline applies with a request instead of a framebuffer:

- **Exact port + path + content.** The grader does a real client operation —
  `pip install --index-url http://host:PORT/simple PKG==VER`, `curl
  http://host:PORT/file`, `GET /` — and checks the *result*. Bind the named port,
  serve the named path, and make the bytes/behavior exactly what's asked (right
  package name **and** version, right file body, right status).
- **Config at the exact location, in the exact format.** Tasks that grade the
  config itself (e.g. logging/rate-limit directives) require the directive in the
  *named* file with the *named* tokens — a working config in a different file
  fails. Disable conflicting defaults (e.g. a stock default site) so your block is
  the one actually serving.
- **Round-trip self-probe.** Run the grader's own client operation yourself before
  declaring done: install from your index, `curl` the URL, push to the git
  endpoint and fetch it back over HTTP.

## Example domain: a push→deploy pipeline whose served content is produced by a receive-side hook

A sharper variant of the content-server shape: the grader doesn't just fetch a
file you placed — it runs a **write client operation** (a push/upload/commit over
the control channel) and then probes the **read** surface, and the bytes it reads
must have been **produced by a hook the write triggers**. The pipeline, not a
hand-placed file, is the deliverable. The recurring instantiation is a versioned
repository whose pushes auto-publish to a web root, but the pattern covers any
"upload here → it appears served there" deploy.

Decisive, easily-missed facts (re-expressed as discover-and-pin actions):

- **You cannot serve content out of the same object you push into, and you cannot
  push into a checked-out working state.** A repository whose current ref is
  checked out **refuses** the write by default (a "deny current branch" guard); the
  fix is a **content-less / bare** receiving object plus a **receive-side hook**
  that materializes the working content into a *separate* served location. Discover
  whether the push target rejects writes when it owns a live working tree, and if
  so split "receive" from "serve": bare/contentless receiver → hook → docroot.
- **The served path is a third location, distinct from both the receiver and the
  source ref.** Pin the exact served root the read-probe hits and have the hook
  deploy there; don't serve the receiver's internal directory.
- **The hook fires on a specific named ref/event.** The write client targets one
  branch/ref; the hook must deploy on exactly that event (guard on the ref name, or
  deploy it unconditionally). A hook keyed to the wrong ref silently deploys
  nothing.
- **The read-probe asserts BOTH transport status AND exact body** (e.g. status 200
  *and* the pushed bytes), at a literal port and a literal path at the served root.
  Match the port/path/body exactly; bind all interfaces so the out-of-band client
  reaches it.
- **The write channel's auth may be pre-provisioned by the harness** — keep the
  channel's daemon up and the account/endpoint present, but don't sink iterations
  into credential setup the prompt says is handled.
- **Round-trip self-probe is mandatory and is the *full* client workflow:** perform
  the write (clone/push/upload) yourself, then run the exact read-probe, and confirm
  the hook-produced bytes are served before declaring done. "My daemon started" is
  not evidence the pipeline deploys.
- **After a green round-trip, DO NOT delete what you deployed (the dominant measured
  failure on this exact instance — 15/15).** The trap is reasoning "my self-test left
  a record/commit/file behind, so the grader's own push will fail with 'nothing to
  commit' / a conflict — let me reset to empty." False: the grader pushes its **own**
  commit from its **own** clone; your leftover commit is a harmless fast-forward base
  and your deployed file is simply overwritten. Wiping the receiver's refs/logs or
  emptying the docroot turns a verified 200 into a 404. Leave the deployed state, the
  hook, and the server exactly as the self-probe left them.

General lesson: when the success condition is "do operation X against the service,
then observe Y", the deliverable is the **X→Y wiring** (a receive-side hook /
trigger), and the service is correct only when a real X actually yields Y on the
out-of-band probe.

## Reference scaffold

[service_bringup_template.py](service_bringup_template.py) is a pluggable
*launch → wait-ready → out-of-band-probe → report* harness. Plug in three parts
for your instance: `launch()` (start the daemon, keep the handle alive),
`ready()` (cheap readiness probe), and a `contract` list of `Probe`s that connect
**from outside the process** and assert the literal surface. It ships generic
probes for the recurring contract items — process alive (`pgrep`-style), a TCP
port listening, an HTTP path returning exact content, a `/proc/<pid>/cmdline`
token, a UNIX socket existing at an exact path, and a UNIX control
request/response — and **two self-tested example instances in different domains**:

- `http-file-contract` — serves exact bytes at an exact TCP port + URL path
  (shape of package/index servers, git-over-http, web roots).
- `unix-control-contract` — exposes a control endpoint at an exact UNIX socket
  path (shape of VM/daemon monitor sockets and IPC control planes).

`python3 service_bringup_template.py --selftest` brings up both as real separate
processes and verifies each contract out-of-band (**verified: both domains pass**).
The template tears them down only for test hygiene — its docstring stresses that
in a real task you **leave the process running**.

## Failure modes that cost the most iterations

- **Binding an equivalent-but-different surface.** A different port, a neighboring
  socket/file path, a config in the wrong file. The grader connects to the
  *named* surface; equivalence doesn't count. Match the literal, or bridge/symlink
  to it.
- **Accepting the default tool/version when one is pinned.** The modern default
  often starts cleanly but produces the wrong observable behavior (blank output,
  wrong artifact, incompatibility) — a *silent* failure. Get the named version.
- **Tearing the service down as "cleanup".** The running process is the
  deliverable; stopping it (or its proxy/bridge) at the end fails everything.
- **"Cleaning up" the verified state after a green self-probe (a dominant measured
  killer).** Deleting deployed content, resetting the receiver's refs/logs, or
  emptying the served location to "look clean for the grader" — driven by the false
  belief that leftovers will conflict with the grader's own write — destroys the
  working contract. The grader uses its own workspace and overwrites/fast-forwards;
  your verified state is the deliverable. STOP when green; submit as-is.
- **Not provisioning the identity the grader connects as (a dominant measured
  killer).** The grader logs in over its authenticated channel as one specific
  account with a specific credential; the base image usually does **not** ship that
  account or have the auth method enabled. If you build and test the whole pipeline
  as root / via a local file path, it looks perfect — and then the grader's real
  connection is refused, the write never lands, and the read-probe returns nothing
  (a 404 / empty / connection-refused). Create the exact account, set the exact
  credential, enable the auth method, and make the receiver + hook + served dir
  writable by that identity (ownership or a shared group). This failure is invisible
  from the root side; only a self-probe **as the grader's identity** exposes it.
- **Letting a reassuring prompt talk you out of provisioning the identity (the exact
  measured failure on this class).** When the prompt says "I'll set up login / auth is
  handled / you don't have to worry about that," it is tempting to conclude the account
  is someone else's responsibility and skip creating it. Agents that did this still
  failed: they correctly *noticed* the account was missing, then deferred to the prompt
  and built everything as root — and the grader's real login was refused. "Login is
  handled" is not evidence the account exists now; it almost always means it does not,
  and that you may create it. Treat "the grader's identity can connect and write" as
  yours to guarantee regardless of the prompt; the only thing that discharges it is an
  executed self-probe under that exact identity.
- **Self-probing as a placeholder identity (or as root / a local file path) instead
  of the grader's exact one.** If the grader authenticates as a specific user/role,
  a missing account, a wrong credential, a disabled auth method, or a deploy dir not
  writable by *that* account only surfaces under that identity. A root-side or
  file-path round-trip that "passes" proves nothing about whether the grader can
  connect. Always drive the full client round-trip as the grader's exact identity
  over the same channel before declaring done.
- **Mutating a probed input artifact.** If the grader hashes or re-reads an input,
  writing to it changes the hash/contents. Serve read-only / snapshot / COW.
- **Declaring done from "my command exited 0" instead of an out-of-band probe.**
  A daemon can start and still not answer the contract (wrong bind address, race,
  conflicting default config still on the port). Always self-probe the way the
  grader will, from outside the process, before finishing.
- **Missing the hidden contract item in the verifier.** Prompts under-specify;
  the verifier hardcodes extra literals (a socket path, a cmdline flag, an exact
  log token). Read the verifier and satisfy every probe, not just the prose.
