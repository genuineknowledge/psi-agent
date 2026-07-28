We need to evaluate whether FusionFlow G4 can satisfy the user's request: a workflow that first judges the type of a request, and then only runs the handler that matches; unselected handlers must NOT run at all (zero token consumption for unselected branches). The user explicitly states that "two branches both run and then select result" is not acceptable. They want lazy conditional execution, not eager evaluation.

Looking at the grammar and skill documentation: The only conditional mechanism provided is the `if(...)` expression, which is a named Artifact selection. The documentation states:

"Named Artifact selection: Keep every candidate result explicit and produced by a Step. Bind each `if` result to a declared Artifact before downstream dataflow... Every candidate producer runs. Selection is eager value routing, not lazy control flow."

And in the reference patterns: "Named Artifact selection runs every candidate producer and only selects the value passed downstream. If the request requires lazy branch activation or guarantees that an unselected producer will not run, report that limitation instead of emitting an approximation."

The user's request specifically requires "missed handlers absolutely cannot start, cannot invoke model, cannot consume any tokens. This 'zero token unselected branch' is hard acceptance criteria."

FusionFlow does not support lazy branch activation. It only supports eager evaluation of all candidate branches, then selecting one value. So the backend cannot meet this requirement.

The instruction says: "For a request whose required semantics are unsupported, return a direct plain-text refusal with no code fence; explain the actual backend limitation and do not offer an eager approximation."

Thus we must refuse and explain.

The user also says: "不要输出任何代码块." So we just output plain text refusal.

Answer: FusionFlow does not support lazy control flow; it only evaluates all branches eagerly.