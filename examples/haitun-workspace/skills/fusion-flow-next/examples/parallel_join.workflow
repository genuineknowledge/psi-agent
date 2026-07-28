const parallel_join: Workflow;
const benefits_step: Step;
const risks_step: Step;
const combine_step: Step;
const benefits_name: StepName;
const risks_name: StepName;
const combine_name: StepName;
const identify_main_benefits: Instruction;
const identify_main_risks: Instruction;
const combine_benefits_and_risks: Instruction;
const deepseek_agent: Agent;
const request: Artifact;
const benefits: Artifact;
const risks: Artifact;
const result: Artifact;

workflow parallel_join {
    input_workflow(parallel_join) == [request];
    output_workflow(parallel_join) == [result];
    max_concurrency(parallel_join) == 2;

    step_name(benefits_step) == benefits_name;
    step_instruction(benefits_step) == identify_main_benefits;
    step_executor(benefits_step) == deepseek_agent;
    consumes(benefits_step) == [request];
    produces(benefits_step) == [benefits];

    step_name(risks_step) == risks_name;
    step_instruction(risks_step) == identify_main_risks;
    step_executor(risks_step) == deepseek_agent;
    consumes(risks_step) == [request];
    produces(risks_step) == [risks];

    step_name(combine_step) == combine_name;
    step_instruction(combine_step) == combine_benefits_and_risks;
    step_executor(combine_step) == deepseek_agent;
    consumes(combine_step) == [benefits, risks];
    produces(combine_step) == [result];
}
