# Weixin Inbound File Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download files received through the Weixin iLink channel, save them locally, and pass stable local `FILE:` paths to the agent session.

**Architecture:** Keep the feature inside `src/psi_agent/channel/weixin_ilink.py`, matching the existing outbound `MEDIA:` implementation. `getupdates` item parsing will become async so it can download media before forwarding the message to the session.

**Tech Stack:** Python 3, aiohttp, pytest, existing `cryptography` dependency for AES-128-ECB decryption.

## Global Constraints

- Do not change other channels.
- Do not block or drop text messages when a media download fails.
- Store downloaded files under `WEIXIN_DOWNLOAD_DIR` when set, otherwise under `~/.psi-agent/channels/weixin-ilink/files`.
- Preserve existing outbound `MEDIA:` behavior.
- Use TDD: write failing tests before production code.

---

### Task 1: Inbound File Download

**Files:**
- Modify: `tests/psi_agent/channel/test_weixin_ilink.py`
- Modify: `src/psi_agent/channel/weixin_ilink.py`

**Interfaces:**
- Produces: `extract_weixin_ilink_messages(..., session: ClientSession | None = None) -> list[WeixinIlinkMessage]`
- Produces: local saved files and message text containing `FILE:<absolute-path>`

- [ ] **Step 1: Write failing tests**

Add tests for:
- successful file download from a `file_item.download_url`
- graceful fallback when a `file_item` has no downloadable URL

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=/public/home/wwb/Fusion-Agent/src /public/home/wwb/anaconda3/bin/python -m pytest /public/home/wwb/Fusion-Agent/tests/psi_agent/channel/test_weixin_ilink.py -q -o addopts=''
```

Expected: new tests fail because inbound file download is not implemented.

- [ ] **Step 3: Implement minimal support**

Implement:
- parse `file_item` and common file metadata
- download `download_url`/`downloadUrl`/`file_url`/`url`
- optionally decrypt when media carries `aes_key`
- sanitize names and save under the download root
- append `FILE:<path>` and metadata text to the inbound message
- append fallback text on download failure

- [ ] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=/public/home/wwb/Fusion-Agent/src /public/home/wwb/anaconda3/bin/python -m pytest /public/home/wwb/Fusion-Agent/tests/psi_agent/channel/test_weixin_ilink.py -q -o addopts=''
```

Expected: all Weixin channel tests pass.
