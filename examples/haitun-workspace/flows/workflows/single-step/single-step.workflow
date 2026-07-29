const single_step: Workflow;
const answer_step: Step;
const answer_name: StepName;
const deepseek_agent: Agent,Executor;
const request: Artifact;
const result: Artifact;

workflow single_step {
    input_workflow(single_step) == [request];
    output_workflow(single_step) == [result];
    step_name(answer_step) == answer_name;
    step_instruction(answer_step) == "Read the supplied inputs and return exactly one JSON object whose keys exactly match the required output artifacts. Do not call tools or add prose.";
    step_executor(answer_step) == deepseek_agent;
    consumes(answer_step) == [request];
    produces(answer_step) == [result];
}
