# Interview Conclusion Workflow

Canonical reusable entry for:

```text
/workflow:interview-conclusion
```

The workflow reads completed rows from the Feishu interview table. At final confirmation, the Human changes `面试状态` on the same exact interview row identified by `interview_record_id`; the workflow then reads that row back from `interview_table_id`. The linked `talent_record_id` is used only to validate the candidate's initial-review lineage and is never the destination for final interview status.

It uses the shared validation programs under `flows/workflows/resume-approval/programs/` and keeps its own deployable copy of every referenced instruction under `instructions/`. See `../resume-approval/README.md` for the complete three-workflow lifecycle and configuration contract.
