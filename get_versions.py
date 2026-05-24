import tomllib

with open("uv.lock", "rb") as f:
    data = tomllib.load(f)

dependencies = {
    "anyio", "aiohttp", "loguru", "tyro", "croniter", "pyyaml",
    "prompt-toolkit", "rich", "pytest", "pytest-asyncio",
    "pytest-cov", "ruff", "ty"
}

found = {}
for pkg in data.get("package", []):
    if pkg["name"] in dependencies:
        found[pkg["name"]] = pkg["version"]

for name in sorted(found):
    print(f"{name}: {found[name]}")
