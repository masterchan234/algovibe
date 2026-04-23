with open("/root/projects/crypto/algotrade/main.py", "r") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if "imbalance_ratio = p.get('imbalance', 0)" in line:
        lines.insert(i, "                log_message(f\"DEBUG: {p['pair']} -> buy={p.get('buy',0)} sell={p.get('sell',0)} bid_depth={p.get('bid_depth',0)} ask_depth={p.get('ask_depth',0)} spread={p.get('spread',0)} imb={p.get('imbalance',0)}\")\n")
        break
with open("/root/projects/crypto/algotrade/main.py", "w") as f:
    f.writelines(lines)
