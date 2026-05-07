import re

file = "docker-compose.yml"
with open(file, "r") as f:
    content = f.read()

# Replace `- timescaledb` with proper syntax when under `depends_on:` where other keys are dicts
content = re.sub(
    r"(\s+depends_on:\s*\n)\s+- timescaledb\s*\n(\s+)([a-zA-Z0-9_-]+):",
    r"\1      timescaledb:\n        condition: service_healthy\n\2\3:",
    content
)

with open(file, "w") as f:
    f.write(content)
