---
name: fix-ci-timeout-jan
description: Fix CI timeout issue from January
category: devops
created_by: agent
created_at: 2026-01-20T00:00:00Z
---

CI was timing out because pytest had no timeout plugin. Added pytest-timeout.