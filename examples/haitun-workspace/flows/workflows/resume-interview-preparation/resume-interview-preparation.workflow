-- Workflow A2: external Human review -> verified decisions -> interview preparation and write.

const resume_interview_preparation:Workflow;

const load_initial_review_handoff_step:Step;
const assert_initial_review_load_ready_step:Step;
const collect_initial_decisions_step:Step;
const persist_interview_stage_handoff_step:Step;
const assert_interview_stage_handoff_ready_step:Step;
const load_interview_stage_step:Step;
const assert_stage_load_ready_step:Step;
const prepare_interview_draft_step:Step;
const assemble_interview_write_batch_step:Step;
const assert_write_batch_ready_step:Step;
const write_interview_records_step:Step;
const persist_interview_handoffs_step:Step;
const assert_interview_handoff_ready_step:Step;
const build_user_facing_summary_step:Step;

const initial_review_handoff_loader:Program,Executor;
const interview_stage_handoff_persister:Program,Executor;
const interview_stage_loader:Program,Executor;
const interview_write_batch_assembler:Program,Executor;
const interview_handoff_persister:Program,Executor;
const program_error_assertion:Program,Executor;
const user_facing_summary_builder:Program,Executor;
const initial_decision_agent:Agent,Executor;
const interview_draft_agent:Agent,Executor;
const interview_write_agent:Agent,Executor;

const feishu_bitable_search_records:Tool;
const feishu_bitable_create_records:Tool;
const high:ReasoningEffort;

const initial_review_handoff:Artifact;
const initial_review_stage_bundle:Artifact;
const validated_candidate_assessments:Artifact;
const talent_pool_manifest:Artifact;
const role_catalog:Artifact;
const initial_review_batch_id:Artifact;
const initial_review_feishu_config:Artifact;
const initial_review_load_manifest:Artifact;
const initial_decision_bundle:Artifact;
const interview_stage_handoff:Artifact;
const interview_stage_handoff_manifest:Artifact;
const interview_stage_bundle:Artifact;
const approved_interview_tasks:Artifact,List;
const interview_task:Artifact;
const batch_id:Artifact;
const feishu_config:Artifact;
const stage_load_manifest:Artifact;
const interview_drafts:Artifact,List;
const interview_write_batch:Artifact;
const draft_validation_manifest:Artifact;
const interview_manifest:Artifact;
const interview_record_ids:Artifact,List;
const interview_handoff_receipt:Artifact;
const user_facing_summary:Artifact;

workflow resume_interview_preparation {
    input_workflow(resume_interview_preparation) == [initial_review_handoff];
    output_workflow(resume_interview_preparation) == [
        interview_manifest,
        interview_record_ids,
        interview_handoff_receipt,
        user_facing_summary
    ];
    max_concurrency(resume_interview_preparation) == 4;
    workflow_timeout(resume_interview_preparation) == 1800;

    program_path(initial_review_handoff_loader) == "./flows/workflows/resume-interview-preparation/programs/load_initial_review_handoff.py";
    program_path(interview_stage_handoff_persister) == "./flows/workflows/resume-approval/programs/persist_interview_stage_handoff.py";
    program_path(interview_stage_loader) == "./flows/workflows/resume-interview-preparation/programs/load_interview_stage.py";
    program_path(interview_write_batch_assembler) == "./flows/workflows/resume-interview-preparation/programs/assemble_interview_write_batch.py";
    program_path(interview_handoff_persister) == "./flows/workflows/resume-approval/programs/persist_interview_handoffs.py";
    program_path(program_error_assertion) == "./flows/workflows/resume-approval/programs/assert_no_program_errors.py";
    program_path(user_facing_summary_builder) == "./flows/workflows/resume-approval/programs/build_user_facing_summary.py";

    step_name(load_initial_review_handoff_step) == "Load immutable initial-review source";
    step_instruction(load_initial_review_handoff_step) == "Verify the strict descriptor, exact file hash and path, assessment-to-talent-record coverage, role provenance, and unchanged local Feishu destination before reading Human decisions.";
    step_executor(load_initial_review_handoff_step) == initial_review_handoff_loader;
    consumes(load_initial_review_handoff_step) == [initial_review_handoff];
    produces(load_initial_review_handoff_step) == [initial_review_stage_bundle, validated_candidate_assessments, talent_pool_manifest, role_catalog, initial_review_batch_id, initial_review_feishu_config, initial_review_load_manifest];
    step_timeout(load_initial_review_handoff_step) == 180;

    step_name(assert_initial_review_load_ready_step) == "Stop invalid initial-review input";
    step_instruction(assert_initial_review_load_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_initial_review_load_ready_step) == program_error_assertion;
    consumes(assert_initial_review_load_ready_step) == [initial_review_stage_bundle, validated_candidate_assessments, talent_pool_manifest, role_catalog, initial_review_batch_id, initial_review_feishu_config, initial_review_load_manifest];
    depends_on(assert_initial_review_load_ready_step, load_initial_review_handoff_step) == True;
    step_timeout(assert_initial_review_load_ready_step) == 180;

    step_name(collect_initial_decisions_step) == "Read and verify completed Human decisions";
    step_instruction(collect_initial_decisions_step) == "./instructions/collect-initial-decisions.md";
    step_executor(collect_initial_decisions_step) == initial_decision_agent;
    consumes(collect_initial_decisions_step) == [talent_pool_manifest, validated_candidate_assessments, initial_review_batch_id, initial_review_feishu_config];
    depends_on(collect_initial_decisions_step, assert_initial_review_load_ready_step) == True;
    produces(collect_initial_decisions_step) == [initial_decision_bundle];
    step_timeout(collect_initial_decisions_step) == 300;
    max_attempts(collect_initial_decisions_step) == 2;

    step_name(persist_interview_stage_handoff_step) == "Persist immutable reviewed interview-stage snapshot";
    step_instruction(persist_interview_stage_handoff_step) == "Require every expected talent row to have exactly one final 通过 or 不通过 decision and every assessment to exactly match the immutable validated source, then persist the canonical reviewed snapshot used by all downstream interview Steps.";
    step_executor(persist_interview_stage_handoff_step) == interview_stage_handoff_persister;
    consumes(persist_interview_stage_handoff_step) == [initial_decision_bundle, validated_candidate_assessments, talent_pool_manifest, role_catalog, initial_review_batch_id, initial_review_feishu_config];
    depends_on(persist_interview_stage_handoff_step, collect_initial_decisions_step) == True;
    produces(persist_interview_stage_handoff_step) == [interview_stage_handoff, interview_stage_handoff_manifest];
    step_timeout(persist_interview_stage_handoff_step) == 180;

    step_name(assert_interview_stage_handoff_ready_step) == "Stop incomplete or invalid Human review";
    step_instruction(assert_interview_stage_handoff_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_interview_stage_handoff_ready_step) == program_error_assertion;
    consumes(assert_interview_stage_handoff_ready_step) == [interview_stage_handoff, interview_stage_handoff_manifest];
    depends_on(assert_interview_stage_handoff_ready_step, persist_interview_stage_handoff_step) == True;
    step_timeout(assert_interview_stage_handoff_ready_step) == 180;

    step_name(load_interview_stage_step) == "Load and verify reviewed interview-stage snapshot";
    step_instruction(load_interview_stage_step) == "Verify the strict reviewed descriptor, exact file hash and path, complete final review counts with zero pending rows, role provenance, and unchanged local Feishu destination before any interview-table call.";
    step_executor(load_interview_stage_step) == interview_stage_loader;
    consumes(load_interview_stage_step) == [interview_stage_handoff];
    depends_on(load_interview_stage_step, assert_interview_stage_handoff_ready_step) == True;
    produces(load_interview_stage_step) == [interview_stage_bundle, approved_interview_tasks, batch_id, feishu_config, stage_load_manifest];
    step_timeout(load_interview_stage_step) == 180;

    step_name(assert_stage_load_ready_step) == "Stop invalid reviewed interview snapshot";
    step_instruction(assert_stage_load_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_stage_load_ready_step) == program_error_assertion;
    consumes(assert_stage_load_ready_step) == [interview_stage_bundle, approved_interview_tasks, batch_id, feishu_config, stage_load_manifest];
    depends_on(assert_stage_load_ready_step, load_interview_stage_step) == True;
    step_timeout(assert_stage_load_ready_step) == 180;

    step_name(prepare_interview_draft_step) == "Prepare one read-only interview draft";
    step_instruction(prepare_interview_draft_step) == "./instructions/prepare-interviews.md";
    step_executor(prepare_interview_draft_step) == interview_draft_agent;
    foreach_item(prepare_interview_draft_step, approved_interview_tasks) == interview_task;
    consumes(prepare_interview_draft_step) == [interview_task, feishu_config];
    depends_on(prepare_interview_draft_step, assert_stage_load_ready_step) == True;
    produces(prepare_interview_draft_step) == [interview_drafts];
    step_timeout(prepare_interview_draft_step) == 300;
    max_attempts(prepare_interview_draft_step) == 2;

    step_name(assemble_interview_write_batch_step) == "Assemble authoritative interview write batch";
    step_instruction(assemble_interview_write_batch_step) == "Join source-ordered one-candidate drafts to authoritative handoff tasks, require suggested questions to exactly equal the source-ordered assessment verification question rendering, normalize table-writeable text, and attach all identity fields from the handoff only.";
    step_executor(assemble_interview_write_batch_step) == interview_write_batch_assembler;
    consumes(assemble_interview_write_batch_step) == [approved_interview_tasks, interview_drafts, batch_id, feishu_config];
    produces(assemble_interview_write_batch_step) == [interview_write_batch, draft_validation_manifest];
    step_timeout(assemble_interview_write_batch_step) == 180;

    step_name(assert_write_batch_ready_step) == "Stop structurally invalid interview drafts";
    step_instruction(assert_write_batch_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_write_batch_ready_step) == program_error_assertion;
    consumes(assert_write_batch_ready_step) == [interview_write_batch, draft_validation_manifest];
    depends_on(assert_write_batch_ready_step, assemble_interview_write_batch_step) == True;
    step_timeout(assert_write_batch_ready_step) == 180;

    step_name(write_interview_records_step) == "Write or reuse exact interview rows";
    step_instruction(write_interview_records_step) == "./instructions/write-interview-records.md";
    step_executor(write_interview_records_step) == interview_write_agent;
    consumes(write_interview_records_step) == [interview_write_batch, feishu_config];
    depends_on(write_interview_records_step, assert_write_batch_ready_step) == True;
    produces(write_interview_records_step) == [interview_manifest];
    step_timeout(write_interview_records_step) == 600;
    max_attempts(write_interview_records_step) == 2;

    step_name(persist_interview_handoffs_step) == "Persist private interview handoffs";
    step_instruction(persist_interview_handoffs_step) == "Persist one private sanitized assessment handoff per exact Feishu interview record id and return the ids required by interview-conclusion.";
    step_executor(persist_interview_handoffs_step) == interview_handoff_persister;
    consumes(persist_interview_handoffs_step) == [interview_manifest, interview_stage_bundle];
    produces(persist_interview_handoffs_step) == [interview_record_ids, interview_handoff_receipt];
    step_timeout(persist_interview_handoffs_step) == 180;

    step_name(assert_interview_handoff_ready_step) == "Stop failed private interview handoff persistence";
    step_instruction(assert_interview_handoff_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_interview_handoff_ready_step) == program_error_assertion;
    consumes(assert_interview_handoff_ready_step) == [interview_record_ids, interview_handoff_receipt];
    depends_on(assert_interview_handoff_ready_step, persist_interview_handoffs_step) == True;
    step_timeout(assert_interview_handoff_ready_step) == 180;

    step_name(build_user_facing_summary_step) == "Build safe user-facing interview-preparation summary";
    step_instruction(build_user_facing_summary_step) == "Build the deterministic privacy-conscious business summary exposed to the invoking user.";
    step_executor(build_user_facing_summary_step) == user_facing_summary_builder;
    consumes(build_user_facing_summary_step) == [interview_stage_bundle, interview_manifest, interview_handoff_receipt, feishu_config];
    depends_on(build_user_facing_summary_step, assert_interview_handoff_ready_step) == True;
    produces(build_user_facing_summary_step) == [user_facing_summary];
    step_timeout(build_user_facing_summary_step) == 180;

    allowed_tool(initial_decision_agent, feishu_bitable_search_records);
    agent_system_prompt(initial_decision_agent) == "Read the exact talent records from the immutable initial-review source, verify the complete AI-owned fingerprint and one final Human decision per record, and return exactly one valid JSON object with initial_decision_bundle as its sole top-level key. Never join by name or write any Feishu row.";
    reasoning_effort(initial_decision_agent) == high;
    max_output_tokens(initial_decision_agent) == 32768;
    max_turns(initial_decision_agent) == 24;

    allowed_tool(interview_draft_agent, feishu_bitable_search_records);
    agent_system_prompt(interview_draft_agent) == "Prepare exactly one evidence-bound Chinese interview draft for the supplied approved candidate, copying suggested questions exactly from the assessment verification question bank. Historical comparison is optional and read-only. Return exactly one valid JSON object with interview_drafts as its sole top-level key, without Markdown or prose, and never invent or echo private identity fields.";
    reasoning_effort(interview_draft_agent) == high;
    max_output_tokens(interview_draft_agent) == 8192;
    max_turns(interview_draft_agent) == 8;

    allowed_tool(interview_write_agent, feishu_bitable_search_records);
    allowed_tool(interview_write_agent, feishu_bitable_create_records);
    agent_system_prompt(interview_write_agent) == "Persist only the exact Program-validated interview write batch. Never rewrite content or overwrite reused rows. Reconcile ambiguous creates by querying the immutable six-field fingerprint. Return exactly one valid JSON object with interview_manifest as its sole top-level key, without Markdown or prose.";
    reasoning_effort(interview_write_agent) == high;
    max_output_tokens(interview_write_agent) == 32768;
    max_turns(interview_write_agent) == 24;
}
