# LLMRouter Multiline Upstream Arguments Design

## Goal

Replace the current single JSON-array `--upstream` value with one tyro
`list[str]` argument whose elements are independent JSON objects. This makes
PowerShell commands readable: one candidate model per continued command line.

## CLI Contract

The Router accepts one `--upstream` option followed by one or more values:

```powershell
--upstream `
  '{\"addr\":\"http://127.0.0.1:8101\",\"model\":\"qwen-plus\",\"description\":\"General tasks\"}' `
  '{\"addr\":\"http://127.0.0.1:8102\",\"model\":\"deepseek-reasoner\",\"description\":\"Complex reasoning\"}'
```

The inner quotes are written as `\"` because Windows PowerShell may remove
unescaped JSON quotes when it passes arguments through native `uv.exe` to
Python.

`AiRouter.upstream` becomes `list[str]` with an empty-list default factory.
The following forms are deliberately unsupported:

- one value containing an outer JSON array;
- newline splitting inside one string;
- repeating the `--upstream` option once per candidate.

Supporting only one representation keeps tyro parsing and validation
unambiguous.

## Parsing and Validation

`parse_upstreams` accepts `list[str]`. It parses every element independently
with `json.loads` and requires the decoded value to be an object.

Each object must contain exactly these fields:

- `addr`: non-empty TCP URL, socket address, or named-pipe address;
- `model`: non-empty candidate model name;
- `description`: non-empty text supplied to LLMRouter.

Unknown fields remain errors, model names must remain unique, and at least one
candidate is required. Candidate order is preserved because the first item is
the implicit fallback when `--default-model` is absent.

Errors identify the array element. A malformed second element, for example,
reports `upstream[1]`, the JSON decoder message, line, and column. When an
object-shaped value contains no double quotes, the error also explains the
Windows PowerShell `\"` escaping requirement. Error messages do not echo the
whole input.

## Router Behavior

Only configuration ingestion changes. The following behavior is unchanged:

- an explicitly requested candidate model bypasses LLMRouter;
- the serialized conversation includes bounded context and omits tool bodies;
- LLMRouter routes are validated and majority-voted;
- ties select the earliest valid route;
- recoverable errors and timeouts use the explicit default candidate;
- without an explicit default, fallback uses `upstream[0]`;
- only the selected upstream receives the OpenAI-compatible request;
- `routing` metadata is removed while normal request fields and SSE chunks are
  preserved.

## Testing

Unit tests will cover:

- two independently encoded JSON objects parse in order;
- an empty list is rejected;
- an outer JSON array element is rejected as a non-object;
- malformed JSON identifies its element index, line, and column;
- the PowerShell quote-loss hint remains available;
- missing, unknown, empty, and duplicate fields retain strict validation.

Router and CLI tests will construct `AiRouter` with `list[str]` and verify that
the documented multiline command shape is accepted. Existing routing, fallback,
request passthrough, and SSE tests remain unchanged except for their setup.

## Documentation Impact

The AI-layer documentation and all active Router startup examples must replace
the old outer JSON array form with the multiline list form. Historical design
documents remain historical and are not rewritten, but this specification
explicitly supersedes their upstream-input contract.
