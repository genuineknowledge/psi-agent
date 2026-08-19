-- Workflow A: uploaded resumes -> AI screening -> talent-pool review task -> immutable A2 input.

const resume_approval:Workflow;

const load_defaults_step:Step;
const assert_defaults_program_ready_step:Step;
const fetch_reference_documents_step:Step;
const assert_reference_documents_ready_step:Step;
const extract_role_catalog_step:Step;
const validate_role_catalog_step:Step;
const assert_role_catalog_ready_step:Step;
const stage_resume_files_step:Step;
const assert_resume_staging_program_ready_step:Step;
const extract_resume_step:Step;
const analyze_resume_step:Step;
const build_assessment_repairs_round_1_step:Step;
const assert_repair_builder_round_1_program_ready_step:Step;
const repair_assessments_round_1_step:Step;
const merge_assessment_repairs_round_1_step:Step;
const assert_repair_merge_round_1_program_ready_step:Step;
const build_assessment_repairs_round_2_step:Step;
const assert_repair_builder_round_2_program_ready_step:Step;
const repair_assessments_round_2_step:Step;
const merge_assessment_repairs_round_2_step:Step;
const assert_repair_merge_round_2_program_ready_step:Step;
const validate_assessments_step:Step;
const cleanup_temporary_files_step:Step;
const assert_cleanup_program_ready_step:Step;
const assert_initial_review_ready_step:Step;
const stage_initial_review_step:Step;
const persist_initial_review_handoff_step:Step;
const assert_initial_review_handoff_ready_step:Step;
const build_user_facing_summary_step:Step;

const defaults_loader:Program,Executor;
const reference_document_fetcher:Program,Executor;
const role_catalog_validator:Program,Executor;
const resume_file_stager:Program,Executor;
const document_extractor:Program,Executor;
const temporary_file_cleaner:Program,Executor;
const assessment_repair_builder_round_1:Program,Executor;
const assessment_repair_merger_round_1:Program,Executor;
const assessment_repair_builder_round_2:Program,Executor;
const assessment_repair_merger_round_2:Program,Executor;
const assessment_validator:Program,Executor;
const workflow_ready_assertion:Program,Executor;
const program_error_assertion:Program,Executor;
const initial_review_handoff_persister:Program,Executor;
const user_facing_summary_builder:Program,Executor;
const role_catalog_agent:Agent,Executor;
const resume_analyzer:Agent,Executor;
const assessment_repair_agent:Agent,Executor;
const talent_pool_agent:Agent,Executor;

const read:Tool;
const read_pdf:Tool;
const feishu_bitable_search_records:Tool;
const feishu_bitable_create_records:Tool;
const feishu_bitable_update_record:Tool;
const feishu_drive_upload:Tool;
const high:ReasoningEffort;
const medium:ReasoningEffort;

const resume_files:Artifact,List;
const reference_document_config:Artifact,List;
const reference_documents:Artifact,List;
const reference_document_manifest:Artifact;
const role_catalog_draft:Artifact;
const role_catalog:Artifact;
const role_catalog_manifest:Artifact;
const staged_resume_files:Artifact,List;
const resume_staging_manifest:Artifact;
const target_role:Artifact;
const batch_id:Artifact;
const feishu_config:Artifact;
const resume_file:Artifact;
const extracted_resumes:Artifact,List;
const resume_extraction_receipts:Artifact,List;
const extracted_resume:Artifact;
const candidate_assessments:Artifact,List;
const assessment_repair_requests_round_1:Artifact,List;
const assessment_repair_request_round_1:Artifact;
const assessment_repair_manifest_round_1:Artifact;
const repaired_candidate_assessments_round_1:Artifact,List;
const candidate_assessments_round_1:Artifact,List;
const assessment_repair_merge_manifest_round_1:Artifact;
const assessment_repair_requests_round_2:Artifact,List;
const assessment_repair_request_round_2:Artifact;
const assessment_repair_manifest_round_2:Artifact;
const repaired_candidate_assessments_round_2:Artifact,List;
const candidate_assessments_repaired:Artifact,List;
const assessment_repair_merge_manifest_round_2:Artifact;
const validated_candidate_assessments:Artifact;
const assessment_validation_manifest:Artifact;
const cleanup_receipt:Artifact;
const cleanup_scope_manifest:Artifact;
const talent_pool_manifest:Artifact;
const initial_review_handoff:Artifact;
const initial_review_handoff_manifest:Artifact;
const initial_review_request:Artifact;
const user_facing_summary:Artifact;

workflow resume_approval {
    input_workflow(resume_approval) == [resume_files];
    output_workflow(resume_approval) == [
        validated_candidate_assessments,
        talent_pool_manifest,
        initial_review_handoff,
        initial_review_request,
        user_facing_summary
    ];
    max_concurrency(resume_approval) == 4;
    workflow_timeout(resume_approval) == 3600;

    program_path(defaults_loader) == "./flows/workflows/resume-approval/programs/load_defaults.py";
    program_path(reference_document_fetcher) == "./flows/workflows/resume-approval/programs/fetch_feishu_reference_documents.py";
    program_path(role_catalog_validator) == "./flows/workflows/resume-approval/programs/validate_role_catalog.py";
    program_path(resume_file_stager) == "./flows/workflows/resume-approval/programs/stage_resume_files.py";
    program_path(document_extractor) == "./flows/workflows/resume-approval/programs/extract_document.py";
    program_path(temporary_file_cleaner) == "./flows/workflows/resume-approval/programs/cleanup_extracted_text.py";
    program_path(assessment_repair_builder_round_1) == "./flows/workflows/resume-approval/programs/assessment_repair_pipeline.py";
    program_path(assessment_repair_merger_round_1) == "./flows/workflows/resume-approval/programs/assessment_repair_pipeline.py";
    program_path(assessment_repair_builder_round_2) == "./flows/workflows/resume-approval/programs/assessment_repair_pipeline.py";
    program_path(assessment_repair_merger_round_2) == "./flows/workflows/resume-approval/programs/assessment_repair_pipeline.py";
    program_path(assessment_validator) == "./flows/workflows/resume-approval/programs/validate_candidate_assessments.py";
    program_path(workflow_ready_assertion) == "./flows/workflows/resume-approval/programs/assert_workflow_ready.py";
    program_path(program_error_assertion) == "./flows/workflows/resume-approval/programs/assert_no_program_errors.py";
    program_path(initial_review_handoff_persister) == "./flows/workflows/resume-approval/programs/persist_initial_review_handoff.py";
    program_path(user_facing_summary_builder) == "./flows/workflows/resume-approval/programs/build_user_facing_summary.py";

    step_name(load_defaults_step) == "Load role, batch, reference sources, and Feishu configuration";
    step_instruction(load_defaults_step) == "Load local configuration and select one explicit active job requirement. Never infer the role from a resume.";
    step_executor(load_defaults_step) == defaults_loader;
    consumes(load_defaults_step) == [resume_files];
    produces(load_defaults_step) == [target_role, batch_id, feishu_config, reference_document_config];
    step_timeout(load_defaults_step) == 180;

    step_name(assert_defaults_program_ready_step) == "Stop when defaults loading returned a Program error";
    step_instruction(assert_defaults_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_defaults_program_ready_step) == program_error_assertion;
    consumes(assert_defaults_program_ready_step) == [target_role, batch_id, feishu_config, reference_document_config];
    step_timeout(assert_defaults_program_ready_step) == 180;

    step_name(fetch_reference_documents_step) == "Fetch and version Feishu recruitment documents";
    step_instruction(fetch_reference_documents_step) == "Read both configured Base document pages through their fixed Docx tokens, hash exact content, and return no partial documents when either source is unavailable.";
    step_executor(fetch_reference_documents_step) == reference_document_fetcher;
    consumes(fetch_reference_documents_step) == [reference_document_config];
    depends_on(fetch_reference_documents_step, assert_defaults_program_ready_step) == True;
    produces(fetch_reference_documents_step) == [reference_documents, reference_document_manifest];
    step_timeout(fetch_reference_documents_step) == 180;

    step_name(assert_reference_documents_ready_step) == "Stop batches with unavailable reference documents";
    step_instruction(assert_reference_documents_ready_step) == "Exit successfully only when both versioned reference documents are present and their manifest is complete and error-free. Produce no stdout.";
    step_executor(assert_reference_documents_ready_step) == workflow_ready_assertion;
    consumes(assert_reference_documents_ready_step) == [reference_documents, reference_document_manifest];
    step_timeout(assert_reference_documents_ready_step) == 180;

    step_name(extract_role_catalog_step) == "Extract source-grounded runtime roles";
    step_instruction(extract_role_catalog_step) == "./instructions/extract-role-catalog.md";
    step_executor(extract_role_catalog_step) == role_catalog_agent;
    consumes(extract_role_catalog_step) == [reference_documents, reference_document_manifest];
    depends_on(extract_role_catalog_step, assert_reference_documents_ready_step) == True;
    produces(extract_role_catalog_step) == [role_catalog_draft];
    step_timeout(extract_role_catalog_step) == 600;
    max_attempts(extract_role_catalog_step) == 2;

    step_name(validate_role_catalog_step) == "Validate runtime roles against the fixed source document";
    step_instruction(validate_role_catalog_step) == "Reject empty, duplicate, invented, unsupported, inactive-as-active, or candidate-example-derived roles and assign deterministic role identities.";
    step_executor(validate_role_catalog_step) == role_catalog_validator;
    consumes(validate_role_catalog_step) == [role_catalog_draft, reference_documents];
    produces(validate_role_catalog_step) == [role_catalog, role_catalog_manifest];
    step_timeout(validate_role_catalog_step) == 180;

    step_name(assert_role_catalog_ready_step) == "Stop batches without a validated active role catalog";
    step_instruction(assert_role_catalog_ready_step) == "Exit successfully only when runtime role-catalog validation is complete, error-free, and contains an active role. Produce no stdout.";
    step_executor(assert_role_catalog_ready_step) == workflow_ready_assertion;
    consumes(assert_role_catalog_ready_step) == [role_catalog_manifest];
    step_timeout(assert_role_catalog_ready_step) == 180;
    max_attempts(assert_role_catalog_ready_step) == 2;

    step_name(stage_resume_files_step) == "Stage uploaded resume files";
    step_instruction(stage_resume_files_step) == "Copy only trusted Feishu attachment paths or workspace files into the SHA-addressed workflow inbox and deduplicate identical content.";
    step_executor(stage_resume_files_step) == resume_file_stager;
    consumes(stage_resume_files_step) == [resume_files, batch_id];
    depends_on(stage_resume_files_step, assert_defaults_program_ready_step) == True;
    produces(stage_resume_files_step) == [staged_resume_files, resume_staging_manifest];
    step_timeout(stage_resume_files_step) == 180;

    step_name(assert_resume_staging_program_ready_step) == "Stop when resume staging returned a Program error";
    step_instruction(assert_resume_staging_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_resume_staging_program_ready_step) == program_error_assertion;
    consumes(assert_resume_staging_program_ready_step) == [staged_resume_files, resume_staging_manifest];
    step_timeout(assert_resume_staging_program_ready_step) == 180;

    step_name(extract_resume_step) == "Extract one staged resume";
    step_instruction(extract_resume_step) == "Inspect one staged resume and return safe extraction metadata. PDF content is read later through read_pdf.";
    step_executor(extract_resume_step) == document_extractor;
    foreach_item(extract_resume_step, staged_resume_files) == resume_file;
    consumes(extract_resume_step) == [resume_file];
    depends_on(extract_resume_step, assert_resume_staging_program_ready_step) == True;
    produces(extract_resume_step) == [extracted_resumes, resume_extraction_receipts];
    step_timeout(extract_resume_step) == 180;

    step_name(analyze_resume_step) == "Match and analyze one resume";
    step_instruction(analyze_resume_step) == "./instructions/analyze-resume.md";
    step_executor(analyze_resume_step) == resume_analyzer;
    foreach_item(analyze_resume_step, extracted_resumes) == extracted_resume;
    consumes(analyze_resume_step) == [extracted_resume, reference_documents, role_catalog, batch_id];
    depends_on(analyze_resume_step, assert_reference_documents_ready_step) == True;
    depends_on(analyze_resume_step, assert_role_catalog_ready_step) == True;
    produces(analyze_resume_step) == [candidate_assessments];
    step_timeout(analyze_resume_step) == 600;
    max_attempts(analyze_resume_step) == 2;

    step_name(build_assessment_repairs_round_1_step) == "Run full static assessment validation and build first repair batch";
    step_instruction(build_assessment_repairs_round_1_step) == "Run the complete deterministic assessment validator, preserve every diagnostic, and emit bounded repair requests only for candidate-local errors that can be repaired without changing source identity.";
    step_executor(build_assessment_repairs_round_1_step) == assessment_repair_builder_round_1;
    consumes(build_assessment_repairs_round_1_step) == [
        candidate_assessments,
        reference_documents,
        role_catalog,
        batch_id
    ];
    produces(build_assessment_repairs_round_1_step) == [
        assessment_repair_requests_round_1,
        assessment_repair_manifest_round_1
    ];
    step_timeout(build_assessment_repairs_round_1_step) == 180;

    step_name(assert_repair_builder_round_1_program_ready_step) == "Stop when first repair request generation returned a Program error";
    step_instruction(assert_repair_builder_round_1_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_repair_builder_round_1_program_ready_step) == program_error_assertion;
    consumes(assert_repair_builder_round_1_program_ready_step) == [assessment_repair_requests_round_1, assessment_repair_manifest_round_1];
    step_timeout(assert_repair_builder_round_1_program_ready_step) == 180;

    step_name(repair_assessments_round_1_step) == "Repair one invalid candidate assessment from first validation";
    step_instruction(repair_assessments_round_1_step) == "./instructions/repair-candidate-assessment.md";
    step_executor(repair_assessments_round_1_step) == assessment_repair_agent;
    foreach_item(repair_assessments_round_1_step, assessment_repair_requests_round_1) == assessment_repair_request_round_1;
    consumes(repair_assessments_round_1_step) == [
        assessment_repair_request_round_1,
        reference_documents,
        role_catalog,
        batch_id
    ];
    depends_on(repair_assessments_round_1_step, assert_repair_builder_round_1_program_ready_step) == True;
    produces(repair_assessments_round_1_step) == [repaired_candidate_assessments_round_1];
    step_timeout(repair_assessments_round_1_step) == 600;
    max_attempts(repair_assessments_round_1_step) == 2;

    step_name(merge_assessment_repairs_round_1_step) == "Merge first-round repairs without changing candidate identity";
    step_instruction(merge_assessment_repairs_round_1_step) == "Replace only the candidate indices named by first-round repair requests, preserve source hashes and batch identity, and emit an auditable merge manifest.";
    step_executor(merge_assessment_repairs_round_1_step) == assessment_repair_merger_round_1;
    consumes(merge_assessment_repairs_round_1_step) == [
        candidate_assessments,
        assessment_repair_requests_round_1,
        repaired_candidate_assessments_round_1,
        batch_id
    ];
    produces(merge_assessment_repairs_round_1_step) == [
        candidate_assessments_round_1,
        assessment_repair_merge_manifest_round_1
    ];
    step_timeout(merge_assessment_repairs_round_1_step) == 180;

    step_name(assert_repair_merge_round_1_program_ready_step) == "Stop when first repair merge returned a Program error";
    step_instruction(assert_repair_merge_round_1_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_repair_merge_round_1_program_ready_step) == program_error_assertion;
    consumes(assert_repair_merge_round_1_program_ready_step) == [candidate_assessments_round_1, assessment_repair_merge_manifest_round_1];
    step_timeout(assert_repair_merge_round_1_program_ready_step) == 180;

    step_name(build_assessment_repairs_round_2_step) == "Revalidate the complete batch and build second repair batch";
    step_instruction(build_assessment_repairs_round_2_step) == "Run the same complete deterministic validator after first-round repairs and emit a final bounded repair request for every remaining repairable candidate-local error.";
    step_executor(build_assessment_repairs_round_2_step) == assessment_repair_builder_round_2;
    consumes(build_assessment_repairs_round_2_step) == [
        candidate_assessments_round_1,
        reference_documents,
        role_catalog,
        batch_id
    ];
    depends_on(build_assessment_repairs_round_2_step, assert_repair_merge_round_1_program_ready_step) == True;
    produces(build_assessment_repairs_round_2_step) == [
        assessment_repair_requests_round_2,
        assessment_repair_manifest_round_2
    ];
    step_timeout(build_assessment_repairs_round_2_step) == 180;

    step_name(assert_repair_builder_round_2_program_ready_step) == "Stop when second repair request generation returned a Program error";
    step_instruction(assert_repair_builder_round_2_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_repair_builder_round_2_program_ready_step) == program_error_assertion;
    consumes(assert_repair_builder_round_2_program_ready_step) == [assessment_repair_requests_round_2, assessment_repair_manifest_round_2];
    step_timeout(assert_repair_builder_round_2_program_ready_step) == 180;

    step_name(repair_assessments_round_2_step) == "Repair one remaining invalid candidate assessment";
    step_instruction(repair_assessments_round_2_step) == "./instructions/repair-candidate-assessment.md";
    step_executor(repair_assessments_round_2_step) == assessment_repair_agent;
    foreach_item(repair_assessments_round_2_step, assessment_repair_requests_round_2) == assessment_repair_request_round_2;
    consumes(repair_assessments_round_2_step) == [
        assessment_repair_request_round_2,
        reference_documents,
        role_catalog,
        batch_id
    ];
    depends_on(repair_assessments_round_2_step, assert_repair_builder_round_2_program_ready_step) == True;
    produces(repair_assessments_round_2_step) == [repaired_candidate_assessments_round_2];
    step_timeout(repair_assessments_round_2_step) == 600;
    max_attempts(repair_assessments_round_2_step) == 2;

    step_name(merge_assessment_repairs_round_2_step) == "Merge second-round repairs without changing candidate identity";
    step_instruction(merge_assessment_repairs_round_2_step) == "Replace only the candidate indices named by second-round repair requests, preserve source hashes and batch identity, and emit the final candidate batch plus an audit manifest.";
    step_executor(merge_assessment_repairs_round_2_step) == assessment_repair_merger_round_2;
    consumes(merge_assessment_repairs_round_2_step) == [
        candidate_assessments_round_1,
        assessment_repair_requests_round_2,
        repaired_candidate_assessments_round_2,
        batch_id
    ];
    produces(merge_assessment_repairs_round_2_step) == [
        candidate_assessments_repaired,
        assessment_repair_merge_manifest_round_2
    ];
    step_timeout(merge_assessment_repairs_round_2_step) == 180;

    step_name(assert_repair_merge_round_2_program_ready_step) == "Stop when second repair merge returned a Program error";
    step_instruction(assert_repair_merge_round_2_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_repair_merge_round_2_program_ready_step) == program_error_assertion;
    consumes(assert_repair_merge_round_2_program_ready_step) == [candidate_assessments_repaired, assessment_repair_merge_manifest_round_2];
    step_timeout(assert_repair_merge_round_2_program_ready_step) == 180;

    step_name(validate_assessments_step) == "Validate the complete candidate batch";
    step_instruction(validate_assessments_step) == "After two strict repair rounds, keep complete business-constraint diagnostics as warnings and block only JSON whose structure or primitive types cannot be mapped to the Feishu initial-review table.";
    step_executor(validate_assessments_step) == assessment_validator;
    consumes(validate_assessments_step) == [
        candidate_assessments_repaired,
        reference_documents,
        role_catalog,
        batch_id
    ];
    depends_on(validate_assessments_step, assert_repair_merge_round_2_program_ready_step) == True;
    produces(validate_assessments_step) == [
        validated_candidate_assessments,
        assessment_validation_manifest
    ];
    step_timeout(validate_assessments_step) == 180;

    step_name(assert_initial_review_ready_step) == "Stop non-writeable batches before Feishu or Human review";
    step_instruction(assert_initial_review_ready_step) == "Exit successfully when final assessment JSON is table-writeable and contains at least one assessable candidate, even when business-constraint warnings remain. Produce no stdout.";
    step_executor(assert_initial_review_ready_step) == workflow_ready_assertion;
    consumes(assert_initial_review_ready_step) == [
        validated_candidate_assessments,
        assessment_validation_manifest
    ];
    depends_on(assert_initial_review_ready_step, validate_assessments_step) == True;
    step_timeout(assert_initial_review_ready_step) == 180;

    step_name(stage_initial_review_step) == "Create talent-pool initial snapshots";
    step_instruction(stage_initial_review_step) == "./instructions/stage-initial-review.md";
    step_executor(stage_initial_review_step) == talent_pool_agent;
    consumes(stage_initial_review_step) == [validated_candidate_assessments, staged_resume_files, batch_id, feishu_config];
    depends_on(stage_initial_review_step, assert_initial_review_ready_step) == True;
    produces(stage_initial_review_step) == [talent_pool_manifest];
    step_timeout(stage_initial_review_step) == 600;
    max_attempts(stage_initial_review_step) == 2;

    step_name(persist_initial_review_handoff_step) == "Persist immutable initial-review handoff";
    step_instruction(persist_initial_review_handoff_step) == "Validate the complete assessment and exact talent record coverage, persist the immutable review source for resume-interview-preparation, and return the Human-facing review request.";
    step_executor(persist_initial_review_handoff_step) == initial_review_handoff_persister;
    consumes(persist_initial_review_handoff_step) == [validated_candidate_assessments, talent_pool_manifest, role_catalog, batch_id, feishu_config];
    depends_on(persist_initial_review_handoff_step, stage_initial_review_step) == True;
    produces(persist_initial_review_handoff_step) == [initial_review_handoff, initial_review_handoff_manifest, initial_review_request];
    step_timeout(persist_initial_review_handoff_step) == 180;

    step_name(assert_initial_review_handoff_ready_step) == "Stop when initial-review handoff persistence returned a Program error";
    step_instruction(assert_initial_review_handoff_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_initial_review_handoff_ready_step) == program_error_assertion;
    consumes(assert_initial_review_handoff_ready_step) == [initial_review_handoff, initial_review_handoff_manifest, initial_review_request];
    depends_on(assert_initial_review_handoff_ready_step, persist_initial_review_handoff_step) == True;
    step_timeout(assert_initial_review_handoff_ready_step) == 180;

    step_name(cleanup_temporary_files_step) == "Delete persisted resume and extracted-text copies";
    step_instruction(cleanup_temporary_files_step) == "Delete only SHA-addressed temporary files after every talent row attachment has been uploaded or reused, read back, and accepted by the immutable handoff guard.";
    step_executor(cleanup_temporary_files_step) == temporary_file_cleaner;
    consumes(cleanup_temporary_files_step) == [staged_resume_files, extracted_resumes, candidate_assessments_repaired];
    depends_on(cleanup_temporary_files_step, assert_initial_review_handoff_ready_step) == True;
    produces(cleanup_temporary_files_step) == [cleanup_receipt, cleanup_scope_manifest];
    step_timeout(cleanup_temporary_files_step) == 180;

    step_name(assert_cleanup_program_ready_step) == "Stop when temporary cleanup returned a Program error";
    step_instruction(assert_cleanup_program_ready_step) == "Fail on any Program error-valued input and otherwise produce no stdout.";
    step_executor(assert_cleanup_program_ready_step) == program_error_assertion;
    consumes(assert_cleanup_program_ready_step) == [cleanup_receipt, cleanup_scope_manifest];
    depends_on(assert_cleanup_program_ready_step, cleanup_temporary_files_step) == True;
    step_timeout(assert_cleanup_program_ready_step) == 180;

    step_name(build_user_facing_summary_step) == "Build safe user-facing initial-review summary";
    step_instruction(build_user_facing_summary_step) == "Build the deterministic privacy-conscious business summary exposed to the invoking user.";
    step_executor(build_user_facing_summary_step) == user_facing_summary_builder;
    consumes(build_user_facing_summary_step) == [validated_candidate_assessments, talent_pool_manifest, initial_review_request, feishu_config];
    depends_on(build_user_facing_summary_step, assert_cleanup_program_ready_step) == True;
    produces(build_user_facing_summary_step) == [user_facing_summary];
    step_timeout(build_user_facing_summary_step) == 180;

    allowed_tool(resume_analyzer, read);
    allowed_tool(resume_analyzer, read_pdf);
    agent_system_prompt(resume_analyzer) == "You are a conservative evidence-based resume assessor. Use only the fixed online scoring document and validated runtime role catalog, preserve their exact revisions and role identity, distinguish unknown from negative evidence, emit normalized education level and institution names only, generate a safe evidence-backed verification question bank, and obey the deterministic interview gate. Do not call submit_step_result: this provider does not reliably encode this step's long function-call arguments as valid JSON. After using tools only for required resume reads, return exactly one valid JSON object with candidate_assessments as its sole top-level key in ordinary assistant content, with no Markdown or prose. This step-specific instruction overrides the generic instruction to submit through the tool.";
    reasoning_effort(resume_analyzer) == medium;
    max_output_tokens(resume_analyzer) == 32768;
    max_turns(resume_analyzer) == 10;

    agent_system_prompt(role_catalog_agent) == "You extract a complete runtime role catalog from exactly one fixed Feishu role-information document. Return exactly one valid JSON object with role_catalog_draft as its sole top-level key, with no Markdown or prose. Copy source evidence verbatim, distinguish concrete positions from headings, categories, and historical candidate cases, and never invent a role or infer one from a resume. Do not call submit_step_result.";
    reasoning_effort(role_catalog_agent) == high;
    max_output_tokens(role_catalog_agent) == 32768;
    max_turns(role_catalog_agent) == 6;

    agent_system_prompt(assessment_repair_agent) == "You repair exactly one schema 3.0 candidate assessment from deterministic validation diagnostics. Do not call submit_step_result: return exactly one valid JSON object in ordinary assistant content, using the exact required output artifact key as its sole top-level key. Preserve the complete source identity, batch id, fixed online document revisions, immutable runtime role key, and valid evidence; change only erroneous candidate-local fields and their direct dependents, and never weaken evidence merely to pass validation.";
    reasoning_effort(assessment_repair_agent) == high;
    max_output_tokens(assessment_repair_agent) == 32768;
    max_turns(assessment_repair_agent) == 6;

    allowed_tool(talent_pool_agent, feishu_bitable_search_records);
    allowed_tool(talent_pool_agent, feishu_bitable_create_records);
    allowed_tool(talent_pool_agent, feishu_bitable_update_record);
    allowed_tool(talent_pool_agent, feishu_drive_upload);
    agent_system_prompt(talent_pool_agent) == "You maintain the 15-field Chinese initial-review table using an exact 12-field AI-owned visible fingerprint, including the deterministic verification question bank, plus one native resume attachment. Bind each assessment to exactly one staged file only by source SHA-256, upload or backfill before cleanup, preserve Human-owned notes and decisions, and fail closed on duplicates, ambiguity, schema drift, unsafe question content, attachment readback mismatch, or untranslated internal values. Never expose question evidence metadata, file tokens, local paths, or temporary URLs; keep required technical IDs only inside the private manifest and out of error text.";
    reasoning_effort(talent_pool_agent) == high;
    max_output_tokens(talent_pool_agent) == 32768;
    max_turns(talent_pool_agent) == 24;

}
