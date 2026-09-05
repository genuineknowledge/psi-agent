# PR 描述草稿 —— 正负面清单卡交互层提案（请定夺吸收哪部分）

> 用法：fork genuineknowledge/psi-agent 后，把 `pn-cards/` 与 `mentor-cards-engine/` 放进对应目录
> （建议 `agents/feishu/` 下新建 `pn-cards/`），以本仓库 main 为基底开分支提交，PR 正文粘贴以下内容。

## Title

feishu: 正负面清单卡交互层提案（confirm/judge/override/feedback/weekly 卡片闭环）— 供参考吸收

## Body

**这不是替代现有 `_positive_negative_list` pipeline，而是补齐其缺失的「卡片交互层」。** 详见提交说明（`提交说明-正负面清单卡-20260905.md`），要点：

### 本 PR 提供（官方现有 pipeline 未覆盖的 4 个交互）

1. **候选批量确认**：会议纪要抽出的行为候选 → multi_use 卡逐行「确认/不记录/修改」→ 入库，修改走表单卡，原行原地刷终态（confirm_card + confirm_edit）。
2. **AI 判断 + mentor 点击复核**：锐评 + ★1-5 + 证据 + 建议 → 同意 / 调整（覆写表单改判）/ 打回，复核卡原地刷「已同意 / 已改判」终态（judge_card + judge_override）。
3. **结果反馈被记录人 + 双向闭环**：生效判断私聊反馈本人 → 开始复盘 / 补充说明；说明**读-拼-写回台账备注列，写成功才刷终态**（feedback_card + feedback_note + ledger_link 测试）。
4. **周小结卡**：某人本周已登记记录条数 + 状态分布 + 逐条展开 + 开始复盘（weekly_card）。

### 技术要点（可独立吸收的部分）

- `_pn_impl.py` 的卡片状态层：multi_use 逐行 action 预注册、终态幂等、状态 JSON 持久化（save/load/commit）、台账链接单元格渲染。
- 台账回写契约：`test_feishu_pn_ledger_link.py`（194 行）可作为 table 层回写的验收样例。
- mentor-cards-engine（周期报表卡引擎）作为独立参考目录一并附上；**运行数据（台账 token / 花名册 / 实卡内容）已排除**，不随源码公开。

### 边界与验证

- 7 个 `feishu_pn_*` 工具 + `_pn_impl.py`（4781 行）+ 2 测试（1962 行）；mentor 引擎代码 + 文档（约 1900 行）。
- 测试为真实租户流程契约测试，原随 `agents/feishu/tests/` 运行；本 PR 未做独立环境复验（如实说明）。
- 请维护者定夺：A 交互闭环设计 / B 卡片状态层 / C 台账回写契约 / D mentor 周期引擎 —— 吸收哪部分均可，其余无需合并。

### 与现有代码的关系

现有 `_positive_negative_list/`（reader/table/models/analyzer/reviews/notifications/dedupe/rules…）保留不动；
本 PR 纯增量，无冲突、无对现有文件的修改。
