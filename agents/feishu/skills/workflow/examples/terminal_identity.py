"""Return the ReAct environment's strict Boolean termination result."""

from __future__ import annotations

import json
import sys


def main() -> None:
    """Validate ``inputs.done`` and write the same JSON Boolean to stdout."""

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("workflow Program input must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("workflow Program input must contain an inputs object")
    done = inputs.get("done")
    if type(done) is not bool:
        raise TypeError("done must be a strict Boolean")
    json.dump(done, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
