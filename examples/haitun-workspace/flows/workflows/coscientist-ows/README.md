# Co-scientist OWS workflow

This directory is part of the `coscientist-ows` workspace overlay produced by
CI. Extract the archive into the root of a compatible Haitun workspace, keeping
the included `flows/` and `skills/` paths unchanged.

Invoke the saved workflow with this exact chat command:

```text
/workflow:coscientist-ows
```

The session asks for the seven values declared by `input_workflow`. The
`coscientist-ows.inputs.example.json` file is a sanitized input example for
copying values; it is not loaded automatically by `/workflow:coscientist-ows`.

## Host requirements

- A current psi-agent Haitun workspace with the FusionFlow runner.
- Python with `aiohttp`.
- `data/knowledge-base/knowledge_base_for_agent.json`.
- `data/laboratory-limitations/laboratory_limitations_for_agent.json`.
- The complete `data/chem-skills/` directory used by route-design agents.
- A working MatterGen/MatterSim installation, model files, and CUDA GPU.
- Network access and `LLM_PROOF_API_KEY` for the catalytic-performance proof
  step. Optional overrides are `LLM_PROOF_BASE_URL` and `LLM_PROOF_MODEL`.

API keys, model files, runtime histories, caches, and generated results are not
included in the CI artifact.
