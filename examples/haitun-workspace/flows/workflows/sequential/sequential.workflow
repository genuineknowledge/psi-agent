const sequential: Workflow;
const draft_step: Step;
const polish_step: Step;
const deepseek_agent: Agent,Executor;
const request: Artifact;
const draft: Artifact;
const result: Artifact;

workflow sequential {
    input_workflow(sequential) == [request];
    output_workflow(sequential) == [result];

    step_name(draft_step) == "Draft";
    step_instruction(draft_step) == "Read the supplied inputs and return exactly one JSON object whose keys exactly match the required output artifacts. Do not call tools or add prose.";
    step_executor(draft_step) == deepseek_agent;
    consumes(draft_step) == [request];
    produces(draft_step) == [draft];

    step_name(polish_step) == "Polish";
    step_instruction(polish_step) == "Read the supplied inputs and return exactly one JSON object whose keys exactly match the required output artifacts. Do not call tools or add prose.";
    step_executor(polish_step) == deepseek_agent;
    consumes(polish_step) == [draft];
    produces(polish_step) == [result];
}
