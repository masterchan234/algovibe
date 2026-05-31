with open('services/brain_inference/main.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "if detail.startswith(\"error=\"):" in line:
        skip = True
    if skip and "self._trend15_stats = {" in line:
        skip = False
        continue
    if skip and "                \"errors\": 0," in line:
        skip = False
        continue
    if not skip:
        new_lines.append(line)

with open('services/brain_inference/main.py', 'w') as f:
    f.writelines(new_lines)
