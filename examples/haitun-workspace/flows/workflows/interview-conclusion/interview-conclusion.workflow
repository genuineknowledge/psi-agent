-- Workflow B: completed interview records -> evidence conclusion -> Human final confirmation.

const interview_conclusion:Workflow;

const load_interview_defaults_step:Step;
const collect_interview_evidence_step:Step;
const validate_interview_evidence_step:Step;
const assert_interview_evidence_ready_step:Step;
const synthesize_hiring_conclusion_step:Step;
const validate_hiring_conclusions_step:Step;
const assert_final_review_ready_step:Step;
const stage_final_review_step:Step;
const final_human_review_step:Step;
const collect_final_decisions_step:Step;
const persist_final_results_step:Step;
const append_report_step:Step;
const build_user_facing_summary_step:Step;

const interview_defaults_loader:Program,Executor;
const interview_evidence_validator:Program,Executor;
const hiring_conclusion_validator:Program,Executor;
const workflow_ready_assertion:Program,Executor;
const user_facing_summary_builder:Program,Executor;
const interview_evidence_agent:Agent,Executor;
const conclusion_agent:Agent,Executor;
const final_review_agent:Agent,Executor;
const persistence_agent:Agent,Executor;
const report_agent:Agent,Executor;
const final_reviewer:Human,Executor;

const feishu_bitable_search_records:Tool;
const feishu_bitable_create_records:Tool;
const read:Tool;
const feishu_doc_read:Tool;
const feishu_doc_append_content:Tool;
const high:ReasoningEffort;
const medium:ReasoningEffort;

const interview_record_ids:Artifact,List;
const interview_record_id:Artifact;
const conclusion_run_id:Artifact;
const feishu_config:Artifact;
const interview_evidence_items:Artifact,List;
const validated_interview_items:Artifact,List;
const validated_interview_item:Artifact;
const interview_validation_manifest:Artifact;
const hiring_conclusions:Artifact,List;
const validated_hiring_conclusions:Artifact;
const hiring_validation_manifest:Artifact;
const final_review_manifest:Artifact;
const final_human_response:Artifact;
const final_decisions:Artifact;
const result_write_receipt:Artifact;
const report_result:Artifact;
const user_facing_summary:Artifact;

workflow interview_conclusion {
    input_workflow(interview_conclusion) == [interview_record_ids];
    output_workflow(interview_conclusion) == [
        interview_validation_manifest,
        validated_hiring_conclusions,
        final_decisions,
        result_write_receipt,
        report_result,
        user_facing_summary
    ];
    max_concurrency(interview_conclusion) == 4;
    workflow_timeout(interview_conclusion) == 3600;

    program_path(interview_defaults_loader) == "./flows/workflows/resume-approval/programs/load_interview_defaults.py";
    program_path(interview_evidence_validator) == "./flows/workflows/resume-approval/programs/validate_interview_evidence.py";
    program_path(hiring_conclusion_validator) == "./flows/workflows/resume-approval/programs/validate_hiring_conclusions.py";
    program_path(workflow_ready_assertion) == "./flows/workflows/resume-approval/programs/assert_workflow_ready.py";
    program_path(user_facing_summary_builder) == "./flows/workflows/resume-approval/programs/build_user_facing_summary.py";

    step_name(load_interview_defaults_step) == "Load interview conclusion configuration";
    step_instruction(load_interview_defaults_step) == "Validate unique interview business keys, load Feishu destinations, and derive a deterministic conclusion run id.";
    step_executor(load_interview_defaults_step) == interview_defaults_loader;
    consumes(load_interview_defaults_step) == [interview_record_ids];
    produces(load_interview_defaults_step) == [conclusion_run_id, feishu_config];
    step_timeout(load_interview_defaults_step) == 180;

    step_name(collect_interview_evidence_step) == "Read one completed interview and its embedded assessment";
    step_instruction(collect_interview_evidence_step) == "./instructions/collect-interview-evidence.md";
    step_executor(collect_interview_evidence_step) == interview_evidence_agent;
    foreach_item(collect_interview_evidence_step, interview_record_ids) == interview_record_id;
    consumes(collect_interview_evidence_step) == [interview_record_id, feishu_config];
    produces(collect_interview_evidence_step) == [interview_evidence_items];
    step_timeout(collect_interview_evidence_step) == 300;
    max_attempts(collect_interview_evidence_step) == 2;

    step_name(validate_interview_evidence_step) == "Validate complete interview evidence aggregate";
    step_instruction(validate_interview_evidence_step) == "Fail closed unless every explicitly requested interview is completed, joined to exactly one approved sanitized talent-pool assessment, and byte-for-byte consistent with its schema 2.0 private handoff and schema 3.0 assessment.";
    step_executor(validate_interview_evidence_step) == interview_evidence_validator;
    consumes(validate_interview_evidence_step) == [
        interview_evidence_items,
        interview_record_ids,
        feishu_config
    ];
    produces(validate_interview_evidence_step) == [validated_interview_items, interview_validation_manifest];
    step_timeout(validate_interview_evidence_step) == 180;

    step_name(assert_interview_evidence_ready_step) == "Stop invalid interview evidence before synthesis";
    step_instruction(assert_interview_evidence_ready_step) == "Exit successfully only when every requested interview produced one complete validated evidence item. Produce no stdout.";
    step_executor(assert_interview_evidence_ready_step) == workflow_ready_assertion;
    consumes(assert_interview_evidence_ready_step) == [
        validated_interview_items,
        interview_validation_manifest
    ];
    step_timeout(assert_interview_evidence_ready_step) == 180;

    step_name(synthesize_hiring_conclusion_step) == "Synthesize one evidence-based hiring conclusion";
    step_instruction(synthesize_hiring_conclusion_step) == "./instructions/synthesize-hiring-conclusions.md";
    step_executor(synthesize_hiring_conclusion_step) == conclusion_agent;
    foreach_item(synthesize_hiring_conclusion_step, validated_interview_items) == validated_interview_item;
    consumes(synthesize_hiring_conclusion_step) == [validated_interview_item, interview_validation_manifest];
    depends_on(synthesize_hiring_conclusion_step, assert_interview_evidence_ready_step) == True;
    produces(synthesize_hiring_conclusion_step) == [hiring_conclusions];
    step_timeout(synthesize_hiring_conclusion_step) == 600;
    max_attempts(synthesize_hiring_conclusion_step) == 2;

    step_name(validate_hiring_conclusions_step) == "Validate hiring conclusions";
    step_instruction(validate_hiring_conclusions_step) == "Reject non-structured, evidence-free, duplicate, or internally inconsistent hiring conclusions before any final-review write.";
    step_executor(validate_hiring_conclusions_step) == hiring_conclusion_validator;
    consumes(validate_hiring_conclusions_step) == [hiring_conclusions, validated_interview_items];
    produces(validate_hiring_conclusions_step) == [
        validated_hiring_conclusions,
        hiring_validation_manifest
    ];
    step_timeout(validate_hiring_conclusions_step) == 180;

    step_name(assert_final_review_ready_step) == "Stop invalid conclusions before Feishu or Human review";
    step_instruction(assert_final_review_ready_step) == "Exit successfully only when hiring-conclusion validation is complete, error-free, and contains at least one conclusion. Produce no stdout.";
    step_executor(assert_final_review_ready_step) == workflow_ready_assertion;
    consumes(assert_final_review_ready_step) == [
        validated_hiring_conclusions,
        hiring_validation_manifest
    ];
    step_timeout(assert_final_review_ready_step) == 180;

    step_name(stage_final_review_step) == "Prepare existing interview rows for final review";
    step_instruction(stage_final_review_step) == "./instructions/stage-final-review.md";
    step_executor(stage_final_review_step) == final_review_agent;
    consumes(stage_final_review_step) == [validated_hiring_conclusions, conclusion_run_id, feishu_config];
    depends_on(stage_final_review_step, assert_final_review_ready_step) == True;
    produces(stage_final_review_step) == [final_review_manifest];
    step_timeout(stage_final_review_step) == 600;
    max_attempts(stage_final_review_step) == 2;

    step_name(final_human_review_step) == "Human final hiring confirmation";
    step_instruction(final_human_review_step) == "./instructions/prepare-final-human-review.md";
    step_executor(final_human_review_step) == final_reviewer;
    consumes(final_human_review_step) == [final_review_manifest];
    produces(final_human_review_step) == [final_human_response];

    step_name(collect_final_decisions_step) == "Read final hiring decisions";
    step_instruction(collect_final_decisions_step) == "./instructions/collect-final-decisions.md";
    step_executor(collect_final_decisions_step) == final_review_agent;
    consumes(collect_final_decisions_step) == [final_human_response, validated_hiring_conclusions, conclusion_run_id, feishu_config];
    produces(collect_final_decisions_step) == [final_decisions];
    step_timeout(collect_final_decisions_step) == 300;
    max_attempts(collect_final_decisions_step) == 2;

    step_name(persist_final_results_step) == "Verify persisted final interview conclusions";
    step_instruction(persist_final_results_step) == "./instructions/persist-final-results.md";
    step_executor(persist_final_results_step) == persistence_agent;
    consumes(persist_final_results_step) == [final_decisions, validated_hiring_conclusions, conclusion_run_id, feishu_config];
    produces(persist_final_results_step) == [result_write_receipt];
    step_timeout(persist_final_results_step) == 300;
    max_attempts(persist_final_results_step) == 2;

    step_name(append_report_step) == "Append hiring audit report";
    step_instruction(append_report_step) == "./instructions/append-report.md";
    step_executor(append_report_step) == report_agent;
    consumes(append_report_step) == [final_decisions, result_write_receipt, validated_hiring_conclusions, conclusion_run_id, feishu_config];
    produces(append_report_step) == [report_result];
    step_timeout(append_report_step) == 600;
    max_attempts(append_report_step) == 2;

    step_name(build_user_facing_summary_step) == "Build safe user-facing final-decision summary";
    step_instruction(build_user_facing_summary_step) == "Build the deterministic privacy-conscious business summary exposed to the invoking user.";
    step_executor(build_user_facing_summary_step) == user_facing_summary_builder;
    consumes(build_user_facing_summary_step) == [validated_hiring_conclusions, final_decisions, result_write_receipt, report_result, feishu_config];
    depends_on(build_user_facing_summary_step, append_report_step) == True;
    produces(build_user_facing_summary_step) == [user_facing_summary];
    step_timeout(build_user_facing_summary_step) == 180;

    allowed_tool(interview_evidence_agent, feishu_bitable_search_records);
    allowed_tool(interview_evidence_agent, read);
    agent_system_prompt(interview_evidence_agent) == "You read one completed interview by exact Feishu record_id, its private local sanitized handoff, and the linked visible talent row. Separate evidence columns do not exist: extract score support only from explicit Human interview notes or supplements, and fail closed when the narrative cannot support a score. Never join by name, fabricate missing evidence, or treat a chat message as evidence.";
    reasoning_effort(interview_evidence_agent) == medium;
    max_output_tokens(interview_evidence_agent) == 12288;
    max_turns(interview_evidence_agent) == 16;

    agent_system_prompt(conclusion_agent) == "You produce one hiring recommendation from an explicit role assessment and completed Human interview evidence. Missing decisive evidence means hold. Never write a Human final decision.";
    reasoning_effort(conclusion_agent) == high;
    max_output_tokens(conclusion_agent) == 12288;
    max_turns(conclusion_agent) == 8;

    allowed_tool(final_review_agent, feishu_bitable_search_records);
    agent_system_prompt(final_review_agent) == "You prepare and later read the exact existing interview row in interview_table_id by interview_record_id; use talent_record_id for validation only. Never create rows, join by name, read final status from the talent row, or infer the Human decision.";
    reasoning_effort(final_review_agent) == medium;
    max_output_tokens(final_review_agent) == 12288;
    max_turns(final_review_agent) == 24;

    allowed_tool(persistence_agent, feishu_bitable_search_records);
    agent_system_prompt(persistence_agent) == "You verify every Human-confirmed decision on its exact existing row in interview_table_id by interview_record_id. Do not create rows and fail closed on inconsistency.";
    reasoning_effort(persistence_agent) == medium;
    max_output_tokens(persistence_agent) == 8192;
    max_turns(persistence_agent) == 12;

    allowed_tool(report_agent, feishu_doc_read);
    allowed_tool(report_agent, feishu_doc_append_content);
    agent_system_prompt(report_agent) == "You append a privacy-conscious hiring audit report using a deterministic conclusion-run marker. The talent pool is the initial-review fact source; the interview table is the interview-evidence and final-hiring-status fact source.";
    reasoning_effort(report_agent) == medium;
    max_output_tokens(report_agent) == 8192;
    max_turns(report_agent) == 12;
}
