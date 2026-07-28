const sequential: Workflow;
const draft_step: Step;
const polish_step: Step;
const draft_name: StepName;
const polish_name: StepName;
const draft_a_short_answer: Instruction;
const polish_for_clarity: Instruction;
const deepseek_agent: Agent;
const request: Artifact;
const draft: Artifact;
const result: Artifact;

workflow sequential {
    input_workflow(sequential) == [request];
    output_workflow(sequential) == [result];

    step_name(draft_step) == draft_name;
    step_instruction(draft_step) == draft_a_short_answer;
    step_executor(draft_step) == deepseek_agent;
    consumes(draft_step) == [request];
    produces(draft_step) == [draft];

    step_name(polish_step) == polish_name;
    step_instruction(polish_step) == polish_for_clarity;
    step_executor(polish_step) == deepseek_agent;
    consumes(polish_step) == [draft];
    produces(polish_step) == [result];
}
