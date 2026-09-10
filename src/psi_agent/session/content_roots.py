"""Content roots — where a layer's content comes from, independent of who mounts it.

A ``Layer`` (see ``tool_layers``) answers "which tools dir is in scope, and at
what rank".  It does **not** answer "is this the same content as the layer the
last session opened", and that is the question compilation reuse turns on.  Until
now ``tool_registry._layer_id`` answered it with the resolved *tools dir path*,
which conflates two things that are not the same:

* **the content** — one shipped copy of official tools, on disk once;
* **the mount** — which workspace has it in scope right now.

They coincide only when every workspace has its own copy of everything.  Under
content layering they stop coinciding by design: the official root is mounted
read-only into many workspaces, and per-user roots sit beside it.  A path-derived
id then mints a *different* id per mount of the *same* bytes, so the process-wide
``_module_cache`` misses and every workspace re-compiles the shared layer.  That
is the cost this module exists to remove; measured locally at 114 files / 1.04 MB
per session on the feishu pack, and the reuse is only reachable once the id stops
being per-mount.

So a ``ContentRoot`` is the declared thing, and ``layer_id`` is minted from its
**name**, not from any path.  Two workspaces mounting the official root under
different paths (``/official/tools`` vs ``C:/pack/official/tools``, or two bind
mounts of one host dir) declare the same name and therefore share cache entries.

**Naming is the whole isolation contract.** The cache key stays
``(layer_id, file_hash)``, so two roots that declare the same name are asserting
their bytes are interchangeable — a same-named, same-content file in either one
may be served from the other.  ``distinct`` names are what keep a personal
``_priv_helper`` out of an official module, exactly as the path-derived id did.
The names therefore have to come from whoever mounts the roots (they know which
mounts are the same shipped content), never be inferred here: inferring from the
path is the behaviour being removed, and inferring from anything else would put a
guess on the isolation boundary.  ``from_spec`` refuses an unnamed root rather
than defaulting, because a silently-shared default name is precisely the failure
the key is meant to prevent.

Not a replacement for ``workspace_path``: schedules stay rooted at the user's
workspace (``agent.py``), which is a *mount*-side question — whose reminders
these are — and has no content-root answer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from psi_agent.session.tool_layers import Layer

# The env var a deployment declares its roots in.  Env rather than a new CLI flag
# on every entry point: Sessions are spawned by ``SessionManager`` (which forwards
# a fixed argument list), by the Gateway, and by ``psi-agent session`` directly,
# so a flag would have to be threaded through all three and kept in sync — while
# the mount layout is a property of the *deployment*, identical for every Session
# in the container, which is what env vars are already used for here (``PSI_APPDATA``,
# ``PSI_PRIVATE_OPEN_IDS``).  Unset → no layering, single-root behaviour unchanged.
CONTENT_ROOTS_ENV = "PSI_CONTENT_ROOTS"

# ``name=path`` entries, ``os.pathsep``-separated so a Windows drive letter's colon
# does not have to be escaped.  Priority is the entry's position: ascending, so the
# last entry is the most specific (closest to the user) — the same direction the
# mount list reads in, ``official:enterprise:users``.
_ENTRY_SPLIT = re.compile(r"\s*=\s*")

# A name goes into a module name via ``Layer.scope_token``, and it is an identity,
# so the character set is restricted rather than sanitised: sanitising would let
# ``official.v2`` and ``official_v2`` collapse into one id, silently sharing a
# cache namespace between two roots that declared themselves distinct.
_VALID_NAME = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


@dataclass(frozen=True)
class ContentRoot:
    """One shipped body of content: a declared *name* plus where it is mounted.

    *name* is the identity — it is what ``layer_id`` becomes, so it decides both
    compilation reuse (same name, same bytes → compile once) and isolation
    (different names never share a module).  *path* is this mount's location and
    is deliberately **not** part of the identity; that is the whole point.

    *priority* ranks the layer, higher being closer to the user, and is carried
    through to ``Layer.priority`` unchanged.
    """

    name: str
    path: Path
    priority: int

    @property
    def layer_id(self) -> str:
        """The cache / scope identity: the declared name, no path in it."""
        return self.name

    @property
    def tools_dir(self) -> Path:
        """``{path}/tools`` — where this root's tool files live."""
        return self.path / "tools"

    def as_layer(self) -> Layer:
        """The ``Layer`` the import hook and the registry consume."""
        return Layer(layer_id=self.layer_id, tools_dir=self.tools_dir, priority=self.priority)


def parse_content_roots(raw: str) -> list[ContentRoot]:
    """Parse ``name=path{sep}name=path`` into roots, ascending in priority.

    Position sets priority (first entry least specific) in steps of 10, leaving
    room to insert a tier without renumbering.  Malformed entries are dropped
    with a warning rather than raising: a bad env var must not make every Session
    in a deployment unstartable, and the surviving roots still load.  An entry
    with no ``=`` has no name, and this module does not invent one (see the
    module docstring) — so it is dropped too.
    """
    roots: list[ContentRoot] = []
    seen: set[str] = set()
    for piece in raw.split(os.pathsep):
        entry = piece.strip()
        if not entry:
            continue
        parts = _ENTRY_SPLIT.split(entry, maxsplit=1)
        if len(parts) != 2:
            logger.warning(f"Ignoring content root {entry!r}: expected 'name=path'")
            continue
        name, path_text = parts[0].strip(), parts[1].strip()
        if not _VALID_NAME.match(name):
            logger.warning(f"Ignoring content root {entry!r}: name {name!r} is not a valid layer name")
            continue
        if not path_text:
            logger.warning(f"Ignoring content root {entry!r}: no path given")
            continue
        if name in seen:
            # Two mounts declaring one name is the *intended* sharing case, but
            # two entries in one list cannot both be that name's mount: the
            # second would shadow the first's tools dir under a shared id.
            logger.warning(f"Ignoring content root {entry!r}: name {name!r} already declared")
            continue
        seen.add(name)
        roots.append(ContentRoot(name=name, path=Path(path_text), priority=len(roots) * 10))
    return roots


def content_roots_from_env(env: dict[str, str] | None = None) -> list[ContentRoot]:
    """Roots declared in ``PSI_CONTENT_ROOTS``; empty list when unset.

    Empty is the single-root world: the caller keeps its existing behaviour
    rather than getting a synthesised root, so a deployment that never sets the
    variable is byte-for-byte unaffected.
    """
    source = os.environ if env is None else env
    return parse_content_roots(source.get(CONTENT_ROOTS_ENV, ""))
