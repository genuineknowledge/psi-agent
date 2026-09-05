const prompt: Artifact;
const thought: Artifact;
const action: Artifact;
const observation: Artifact;
const done: BoolArtifact;
const loop_done: BoolArtifact;

const reason: Step;
const env_step: Step;
const update: Step;
const terminal: TerminalStep;

const reason_executor: Agent, Executor;
const env: Agent, Executor;
const update_executor: Agent, Executor;
const terminal_validator: Program, Executor;

workflow react {
  input_workflow(react) == [prompt];

  consumes(reason) == [prompt];
  produces(reason) == [thought, action];

  consumes(env_step) == [action];
  produces(env_step) == [observation, done];

  consumes(update) == [prompt, thought, action, observation];
  produces(update) == [prompt];

  -- done is env.step's result; loop_done is closed loop control.
  consumes(terminal) == [done];
  produces(terminal) == [loop_done];

  output_workflow(react) == [action];

  step_executor(reason) == reason_executor;
  step_executor(env_step) == env;
  step_executor(update) == update_executor;
  step_executor(terminal) == terminal_validator;
  program_path(terminal_validator) == "./skills/workflow/examples/terminal_identity.py";

  step_name(reason) == "Reason";
  step_instruction(reason) == "Read prompt and produce thought and action as two separate outputs. Do not execute action.";

  step_name(env_step) == "env.step";
  step_instruction(env_step) == "Execute env.step(action) exactly once and produce observation and strict Boolean done as two separate outputs.";

  step_name(update) == "Update Prompt";
  step_instruction(update) == "Return update(prompt, thought, action, observation) as the next prompt.";

  step_name(terminal) == "If Done";
  step_instruction(terminal) == "Validate done and produce loop_done with exactly the same strict Boolean value.";
}
