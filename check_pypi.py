import urllib.request
import json

deps = [
    "anyio", "aiohttp", "loguru", "tyro", "croniter", "pyyaml",
    "prompt-toolkit", "rich", "pytest", "pytest-asyncio",
    "pytest-cov", "ruff", "ty"
]

for dep in deps:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{dep}/json") as f:
            data = json.load(f)
            print(f"{dep}: {data['info']['version']}")
    except Exception as e:
        print(f"{dep}: Error {e}")
