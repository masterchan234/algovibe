import os

file = "app/application/oracle.py"
with open(file, "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "conn = conn_ctx.__enter__()" in line:
        # get indentation of previous line
        prev_line = lines[i-1]
        indent = len(prev_line) - len(prev_line.lstrip())
        lines[i] = " " * indent + line.lstrip()
    if "if 'conn_ctx' in locals():" in line:
        prev_line = lines[i-1]
        indent = len(prev_line) - len(prev_line.lstrip())
        lines[i] = " " * indent + line.lstrip()
        lines[i+1] = " " * (indent + 4) + lines[i+1].lstrip()

with open(file, "w") as f:
    f.writelines(lines)
