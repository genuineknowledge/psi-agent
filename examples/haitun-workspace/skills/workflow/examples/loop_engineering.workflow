const state: Artifact;
const work: Artifact;
const candidate: Artifact;
const verification: Artifact;
const next_state: Artifact;
const done: BoolArtifact;

const discover: Step;
const engineer: Step;
const verify: Step;
const advance: Step;
const commit: Step;
const should_stop: TerminalStep;

const discover_agent: Agent, Executor;
const engineer_agent: Agent, Executor;
const verify_agent: Agent, Executor;
const advance_agent: Agent, Executor;
const commit_agent: Agent, Executor;
const stop_agent: Agent, Executor;

workflow loop_engineering {
  input_workflow(loop_engineering) == [state];

  consumes(discover) == [state];
  produces(discover) == [work];

  consumes(engineer) == [state, work];
  produces(engineer) == [candidate];

  consumes(verify) == [state, candidate];
  produces(verify) == [verification];

  consumes(advance) == [state, work, candidate, verification];
  produces(advance) == [next_state];

  consumes(should_stop) == [work, verification];
  produces(should_stop) == [done];

  consumes(commit) == [next_state];
  produces(commit) == [state];

  output_workflow(loop_engineering) == [state];

  step_executor(discover) == discover_agent;
  step_executor(engineer) == engineer_agent;
  step_executor(verify) == verify_agent;
  step_executor(advance) == advance_agent;
  step_executor(should_stop) == stop_agent;
  step_executor(commit) == commit_agent;

  step_name(discover) == "Discover";
  step_instruction(discover) == "Return discover(state) as work.";

  step_name(engineer) == "Engineer";
  step_instruction(engineer) == "Return engineer(state, work) as candidate.";

  step_name(verify) == "Verify";
  step_instruction(verify) == "Return verify(state, candidate) as verification.";

  step_name(advance) == "Advance";
  step_instruction(advance) == "Return advance(state, work, candidate, verification) as next_state.";

  step_name(should_stop) == "Should Stop";
  step_instruction(should_stop) == "Return exactly should_stop(work, verification) as strict Boolean done.";

  step_name(commit) == "Commit Next State";
  step_instruction(commit) == "Return commit(next_state) as state without external side effects.";
}
