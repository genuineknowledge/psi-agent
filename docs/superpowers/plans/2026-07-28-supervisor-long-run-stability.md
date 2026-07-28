# 用户画像与 Supervisor 长期稳定性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立、运行并迭代修复 250 轮确定性压力测试与 30–50 轮真实模型验收。

**Architecture:** 独立 long-run harness 生成多领域轮次和故障日程，通过正式 profile、SupervisorManager 与 store 接口执行，逐轮校验不变量并保存快照。真实 runner 使用相同场景和指标模型，但经完整 HTTP/SSE Session 调用 DeepSeek。

**Tech Stack:** Python 3.14、anyio、pytest、YAML/JSONL、psi-agent Session/Supervisor、DeepSeek。

---

### Task 1：可观测状态和幂等基础

**Files:**
- Modify: `examples/haitun-workspace/systems/supervisor.py`
- Modify: `examples/haitun-workspace/systems/supervisor_store.py`
- Modify: `examples/haitun-workspace/tools/_user_profile.py`
- Test: `tests/integration/test_haitun_supervisor.py`
- Test: `tests/integration/test_haitun_profile.py`

- [ ] 写失败测试：event_id 重放不重复更新、故障分类和恢复状态可观测。
- [ ] 运行测试确认 RED。
- [ ] 实现最小幂等账本、状态与分阶段 metrics。
- [ ] 运行测试确认 GREEN。

### Task 2：领域规范化和地图长期一致性

**Files:**
- Modify: `examples/haitun-workspace/systems/supervisor_protocol.py`
- Modify: `examples/haitun-workspace/systems/supervisor.py`
- Modify: `examples/haitun-workspace/systems/supervisor_store.py`
- Test: `tests/integration/test_haitun_supervisor.py`

- [ ] 写失败测试：business/law 规范化、aliases 合并、无悬空边、跨域隔离。
- [ ] 运行测试确认 RED。
- [ ] 实现 domain_id 规范化与 map validator。
- [ ] 运行测试确认 GREEN。

### Task 3：注册信息弱先验

**Files:**
- Create: `examples/haitun-workspace/systems/user_registration.py`
- Modify: `examples/haitun-workspace/systems/system.py`
- Modify: `examples/haitun-workspace/tools/_user_profile.py`
- Test: `tests/integration/test_haitun_profile.py`

- [ ] 写失败测试：注册强领域/新领域不覆盖当前话题证据。
- [ ] 运行测试确认 RED。
- [ ] 实现哈希用户注册信息加载和弱先验注入。
- [ ] 运行测试确认 GREEN。

### Task 4：长期压力测试 harness

**Files:**
- Create: `examples/haitun-workspace/long_run_scenarios.py`
- Create: `examples/haitun-workspace/run_supervisor_stability.py`
- Create: `examples/haitun-workspace/stability_invariants.py`
- Test: `tests/integration/test_haitun_long_run.py`

- [ ] 写失败测试：10 阶段场景恰好 250 轮、包含跨域/深浅/重复/故障事件。
- [ ] 运行测试确认 RED。
- [ ] 实现场景、确定性 Advice、故障注入和逐轮快照。
- [ ] 增加画像、map、heatmap、恢复和性能不变量。
- [ ] 运行 25 轮预检并修复。
- [ ] 运行完整 250 轮并生成 JSON/Markdown 报告。

### Task 5：真实模型 runner

**Files:**
- Create: `examples/haitun-workspace/run_real_multidomain_evaluation.py`
- Create: `examples/haitun-workspace/build_stability_reports.py`
- Test: `tests/integration/test_haitun_long_run.py`

- [ ] 写失败测试：唯一实验身份、SSE 完整记录、状态快照、可恢复续跑。
- [ ] 运行测试确认 RED。
- [ ] 实现 30 轮真实 runner 和 checkpoint。
- [ ] 运行 30 轮并评估门槛。
- [ ] 无阻断失败时扩展到 50 轮。

### Task 6：最终验证与文档

**Files:**
- Modify: `examples/haitun-supervisor-workspace/README.md`
- Create: `artifacts/supervisor-stability-*/README.md`

- [ ] 运行聚焦 pytest、Ruff、Ty。
- [ ] 核对 250 轮证据、真实对话、画像、地图、热力图和恢复记录。
- [ ] 输出达标/未达标指标与剩余风险，不使用 Mock 冒充真实结果。

