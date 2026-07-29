-- Reusable overall-water-splitting co-scientist workflow.
-- Step instructions are bundle-relative; Program executables are
-- workspace-root-relative, as required by the public runner.

const coscientist_ows:Workflow;

const prepare_workflow_step:Step;
const recommend_1_step:Step;
const recommend_2_step:Step;
const recommend_3_step:Step;
const recommend_4_step:Step;
const merge_recommendation_outputs_step:Step;
const performance_proof_step:Step;
const mattergen_step:Step;
const mattersim_step:Step;
const synthesis_route_design_step:Step;
const synthesis_route_feasibility_analysis_step:Step;
const shutdown_step:Step;

const prepare_workflow:StepName;
const recommend_1:StepName;
const recommend_2:StepName;
const recommend_3:StepName;
const recommend_4:StepName;
const merge_recommendation_outputs:StepName;
const performance_proof:StepName;
const mattergen:StepName;
const mattersim:StepName;
const synthesis_route_design:StepName;
const synthesis_route_feasibility_analysis:StepName;
const shutdown:StepName;

const overall_water_splitting_knowledge_base:Artifact;
const laboratory_rule_base:Artifact;
const robot_chemist_skill_library:Artifact;
const successful_route_library:Artifact;
const historical_results:Artifact;
const result_directory_name:Artifact;
const candidate_knowledge_base_initial:Artifact;

const workflow_run_context:Artifact;
const scheduler_state:Artifact;
const prepare_workflow_step_result:Artifact;
const mattergen_stage_directory_initial:Artifact;
const mattersim_stage_directory_initial:Artifact;
const round_parallel_synthesis_stage_directory_initial:Artifact;
const candidate_catalyst_pool_initial:Artifact;
const candidate_catalyst_structure_pool_initial:Artifact;
const novel_and_stable_catalysts_initial:Artifact;
const fail_directory:Artifact;
const fail_candidates_directory_initial:Artifact;
const tmp_candidates_directory_initial:Artifact;
const tmp_knowledge_directory_initial:Artifact;
const recommender_slot_1_directory:Artifact;
const recommender_slot_2_directory:Artifact;
const recommender_slot_3_directory:Artifact;
const recommender_slot_4_directory:Artifact;

const recommendation_slot_1_results:Artifact;
const recommendation_slot_2_results:Artifact;
const recommendation_slot_3_results:Artifact;
const recommendation_slot_4_results:Artifact;
const tmp_candidates_directory_from_recommend_1:Artifact;
const tmp_candidates_directory_from_recommend_2:Artifact;
const tmp_candidates_directory_from_recommend_3:Artifact;
const tmp_candidates_directory_from_recommend_4:Artifact;
const tmp_knowledge_directory_from_recommend_1:Artifact;
const tmp_knowledge_directory_from_recommend_2:Artifact;
const tmp_knowledge_directory_from_recommend_3:Artifact;
const tmp_knowledge_directory_from_recommend_4:Artifact;
const tmp_candidates_directory_after_recommendations:Artifact;
const tmp_knowledge_directory:Artifact;

const candidate_catalyst_pool_after_performance_proof:Artifact;
const fail_candidates_directory_after_performance_proof:Artifact;
const tmp_candidates_directory:Artifact;
const performance_proven_catalysts:Artifact;
const performance_rejected_catalysts:Artifact;

const candidate_catalyst_pool:Artifact;
const mattergen_stage_directory:Artifact;
const candidate_catalyst_structure_pool_after_mattergen:Artifact;
const candidate_catalyst_structure_pool:Artifact;
const mattersim_stage_directory:Artifact;
const novel_and_stable_catalysts_after_mattersim:Artifact;
const fail_candidates_directory_after_mattersim:Artifact;
const non_novel_or_unstable_catalysts:Artifact;

const synthesis_route_update:Artifact;
const catalysts_unable_to_join_total_route:Artifact;
const round_parallel_synthesis_stage_directory:Artifact;
const novel_and_stable_catalysts:Artifact;
const fail_candidates_directory:Artifact;
const synthesis_input_summary:Artifact;
const round_parallel_synthesis_route:Artifact;
const round_parallel_synthesis_index:Artifact;
const cumulative_synthesis_parameter_csv_files:Artifact;
const source_liquid_inventory:Artifact;
const source_liquid_preparation_methods:Artifact;
const cumulative_synthesis_route:Artifact;
const chemskills_execution_spec:Artifact;
const source_liquid_bottle_preparation:Artifact;
const completed_96_well_plate_synthesis_route:Artifact;
const synthesis_safety_feasibility_judgment:Artifact;
const synthesis_safety_feasibility_judgment_audit:Artifact;
const candidate_knowledge_base:Artifact;
const workflow_closed:Artifact;

const recommender_1_agent:Agent,Executor;
const recommender_2_agent:Agent,Executor;
const recommender_3_agent:Agent,Executor;
const recommender_4_agent:Agent,Executor;
const crystal_generation_evaluation_agent:Agent,Executor;
const performance_prover_agent:Agent,Executor;
const synthesis_route_designer_agent:Agent,Executor;
const synthesis_safety_feasibility_judge_agent:Agent,Executor;
const main_coordinator_agent:Agent,Executor;
const prepare_workflow_program:Program,Executor;
const merge_recommendation_outputs_program:Program,Executor;

workflow coscientist_ows {
    -- DATA FLOW
    input_workflow(coscientist_ows) == [
        overall_water_splitting_knowledge_base,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        historical_results,
        result_directory_name,
        candidate_knowledge_base_initial
    ];

    consumes(prepare_workflow_step) == [
        result_directory_name,
        candidate_knowledge_base_initial
    ];
    produces(prepare_workflow_step) == [
        workflow_run_context,
        scheduler_state,
        mattergen_stage_directory_initial,
        mattersim_stage_directory_initial,
        round_parallel_synthesis_stage_directory_initial,
        candidate_knowledge_base,
        candidate_catalyst_pool_initial,
        candidate_catalyst_structure_pool_initial,
        novel_and_stable_catalysts_initial,
        fail_directory,
        fail_candidates_directory_initial,
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        recommender_slot_1_directory,
        recommender_slot_2_directory,
        recommender_slot_3_directory,
        recommender_slot_4_directory,
        prepare_workflow_step_result
    ];

    consumes(recommend_1_step) == [
        overall_water_splitting_knowledge_base,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        historical_results,
        workflow_run_context,
        scheduler_state,
        candidate_knowledge_base,
        candidate_catalyst_pool_initial,
        candidate_catalyst_structure_pool_initial,
        novel_and_stable_catalysts_initial,
        fail_candidates_directory_initial,
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        recommender_slot_1_directory
    ];
    produces(recommend_1_step) == [
        recommendation_slot_1_results,
        tmp_candidates_directory_from_recommend_1,
        tmp_knowledge_directory_from_recommend_1
    ];

    consumes(recommend_2_step) == [
        overall_water_splitting_knowledge_base,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        historical_results,
        workflow_run_context,
        scheduler_state,
        candidate_knowledge_base,
        candidate_catalyst_pool_initial,
        candidate_catalyst_structure_pool_initial,
        novel_and_stable_catalysts_initial,
        fail_candidates_directory_initial,
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        recommender_slot_2_directory
    ];
    produces(recommend_2_step) == [
        recommendation_slot_2_results,
        tmp_candidates_directory_from_recommend_2,
        tmp_knowledge_directory_from_recommend_2
    ];

    consumes(recommend_3_step) == [
        overall_water_splitting_knowledge_base,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        historical_results,
        workflow_run_context,
        scheduler_state,
        candidate_knowledge_base,
        candidate_catalyst_pool_initial,
        candidate_catalyst_structure_pool_initial,
        novel_and_stable_catalysts_initial,
        fail_candidates_directory_initial,
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        recommender_slot_3_directory
    ];
    produces(recommend_3_step) == [
        recommendation_slot_3_results,
        tmp_candidates_directory_from_recommend_3,
        tmp_knowledge_directory_from_recommend_3
    ];

    consumes(recommend_4_step) == [
        overall_water_splitting_knowledge_base,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        historical_results,
        workflow_run_context,
        scheduler_state,
        candidate_knowledge_base,
        candidate_catalyst_pool_initial,
        candidate_catalyst_structure_pool_initial,
        novel_and_stable_catalysts_initial,
        fail_candidates_directory_initial,
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        recommender_slot_4_directory
    ];
    produces(recommend_4_step) == [
        recommendation_slot_4_results,
        tmp_candidates_directory_from_recommend_4,
        tmp_knowledge_directory_from_recommend_4
    ];

    consumes(merge_recommendation_outputs_step) == [
        tmp_candidates_directory_initial,
        tmp_knowledge_directory_initial,
        tmp_candidates_directory_from_recommend_1,
        tmp_candidates_directory_from_recommend_2,
        tmp_candidates_directory_from_recommend_3,
        tmp_candidates_directory_from_recommend_4,
        tmp_knowledge_directory_from_recommend_1,
        tmp_knowledge_directory_from_recommend_2,
        tmp_knowledge_directory_from_recommend_3,
        tmp_knowledge_directory_from_recommend_4
    ];
    produces(merge_recommendation_outputs_step) == [
        tmp_candidates_directory_after_recommendations,
        tmp_knowledge_directory
    ];

    consumes(performance_proof_step) == [
        tmp_candidates_directory_after_recommendations,
        candidate_catalyst_pool_initial,
        fail_candidates_directory_initial
    ];
    produces(performance_proof_step) == [
        tmp_candidates_directory,
        candidate_catalyst_pool_after_performance_proof,
        fail_candidates_directory_after_performance_proof,
        performance_proven_catalysts,
        performance_rejected_catalysts
    ];

    consumes(mattergen_step) == [
        candidate_catalyst_pool_after_performance_proof,
        mattergen_stage_directory_initial,
        candidate_catalyst_structure_pool_initial
    ];
    produces(mattergen_step) == [
        candidate_catalyst_pool,
        mattergen_stage_directory,
        candidate_catalyst_structure_pool_after_mattergen
    ];

    consumes(mattersim_step) == [
        candidate_catalyst_structure_pool_after_mattergen,
        mattersim_stage_directory_initial,
        novel_and_stable_catalysts_initial,
        fail_candidates_directory_after_performance_proof,
        overall_water_splitting_knowledge_base
    ];
    produces(mattersim_step) == [
        candidate_catalyst_structure_pool,
        mattersim_stage_directory,
        novel_and_stable_catalysts_after_mattersim,
        fail_candidates_directory_after_mattersim,
        non_novel_or_unstable_catalysts
    ];

    consumes(synthesis_route_design_step) == [
        novel_and_stable_catalysts_after_mattersim,
        round_parallel_synthesis_stage_directory_initial,
        historical_results,
        laboratory_rule_base,
        robot_chemist_skill_library,
        successful_route_library,
        fail_candidates_directory_after_mattersim
    ];
    produces(synthesis_route_design_step) == [
        synthesis_route_update,
        catalysts_unable_to_join_total_route,
        round_parallel_synthesis_stage_directory,
        novel_and_stable_catalysts,
        fail_candidates_directory,
        synthesis_input_summary,
        round_parallel_synthesis_route,
        round_parallel_synthesis_index,
        cumulative_synthesis_parameter_csv_files,
        source_liquid_inventory,
        source_liquid_preparation_methods,
        cumulative_synthesis_route,
        chemskills_execution_spec,
        source_liquid_bottle_preparation,
        completed_96_well_plate_synthesis_route
    ];

    consumes(synthesis_route_feasibility_analysis_step) == [
        cumulative_synthesis_route,
        completed_96_well_plate_synthesis_route
    ];
    produces(synthesis_route_feasibility_analysis_step) == [
        synthesis_safety_feasibility_judgment,
        synthesis_safety_feasibility_judgment_audit
    ];

    consumes(shutdown_step) == [
        cumulative_synthesis_route,
        synthesis_safety_feasibility_judgment,
        synthesis_safety_feasibility_judgment_audit,
        completed_96_well_plate_synthesis_route
    ];
    produces(shutdown_step) == [workflow_closed];

    output_workflow(coscientist_ows) == [
        candidate_knowledge_base,
        historical_results,
        completed_96_well_plate_synthesis_route,
        synthesis_safety_feasibility_judgment,
        synthesis_safety_feasibility_judgment_audit,
        workflow_closed
    ];

    -- EXECUTOR ASSIGNMENT
    step_executor(prepare_workflow_step) == prepare_workflow_program;
    step_executor(recommend_1_step) == recommender_1_agent;
    step_executor(recommend_2_step) == recommender_2_agent;
    step_executor(recommend_3_step) == recommender_3_agent;
    step_executor(recommend_4_step) == recommender_4_agent;
    step_executor(merge_recommendation_outputs_step) == merge_recommendation_outputs_program;
    step_executor(performance_proof_step) == performance_prover_agent;
    step_executor(mattergen_step) == crystal_generation_evaluation_agent;
    step_executor(mattersim_step) == crystal_generation_evaluation_agent;
    step_executor(synthesis_route_design_step) == synthesis_route_designer_agent;
    step_executor(
        synthesis_route_feasibility_analysis_step
    ) == synthesis_safety_feasibility_judge_agent;
    step_executor(shutdown_step) == main_coordinator_agent;

    program_path(
        prepare_workflow_program
    ) == "./skills/coscientist-ows-entry/scripts/program.py";
    program_path(
        merge_recommendation_outputs_program
    ) == "./skills/coscientist-ows-entry/scripts/program.py";

    -- STEP CONFIGURATION
    step_name(prepare_workflow_step) == prepare_workflow;
    step_instruction(
        prepare_workflow_step
    ) == "./instructions/prepare-workflow.md";

    step_name(recommend_1_step) == recommend_1;
    step_instruction(
        recommend_1_step
    ) == "./instructions/recommend-candidate.md";
    step_name(recommend_2_step) == recommend_2;
    step_instruction(
        recommend_2_step
    ) == "./instructions/recommend-candidate.md";
    step_name(recommend_3_step) == recommend_3;
    step_instruction(
        recommend_3_step
    ) == "./instructions/recommend-candidate.md";
    step_name(recommend_4_step) == recommend_4;
    step_instruction(
        recommend_4_step
    ) == "./instructions/recommend-candidate.md";

    step_name(merge_recommendation_outputs_step) == merge_recommendation_outputs;
    step_instruction(
        merge_recommendation_outputs_step
    ) == "./instructions/merge-recommendation-outputs.md";

    step_name(performance_proof_step) == performance_proof;
    step_instruction(
        performance_proof_step
    ) == "./instructions/prove-performance.md";
    step_name(mattergen_step) == mattergen;
    step_instruction(
        mattergen_step
    ) == "./instructions/sample-structure.md";
    step_name(mattersim_step) == mattersim;
    step_instruction(
        mattersim_step
    ) == "./instructions/evaluate-structures.md";
    step_name(synthesis_route_design_step) == synthesis_route_design;
    step_instruction(
        synthesis_route_design_step
    ) == "./instructions/design-synthesis-route.md";
    step_name(
        synthesis_route_feasibility_analysis_step
    ) == synthesis_route_feasibility_analysis;
    step_instruction(
        synthesis_route_feasibility_analysis_step
    ) == "./instructions/analyze-route-feasibility.md";
    step_name(shutdown_step) == shutdown;
    step_instruction(
        shutdown_step
    ) == "./instructions/shutdown-workflow.md";

    -- SCHEDULING CONFIGURATION
    independent(recommend_1_step);
    independent(recommend_2_step);
    independent(recommend_3_step);
    independent(recommend_4_step);
    -- WORKFLOW CONFIGURATION
    max_concurrency(coscientist_ows) == 4;
}
