import re

file_path = 'app/application/oracle.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
in_execute = False
for line in lines:
    # This is a bit risky but we can try to find ? inside execute strings
    if 'execute(' in line:
        # Replace ? with %s if it's inside a string in this line
        line = re.sub(r'(\".*?)\?(\".*?execute\()', r'\1%s\2', line) # No, this is wrong
        # Let's try simpler: if line has execute( and ?, replace ? with %s
        # BUT only if it's part of a SQL string.
        # Actually, most of these are like: conn.execute("SELECT ... WHERE pair = ?", (pair,))
        # So we can look for " ? " or "= ?" or " ?"
        if '?' in line:
            # Check if it's likely a SQL parameter
            # We search for ? inside quotes
            line = re.sub(r'(["\'])(.*?)\?(.*?)(["\'])', r'\1\2%s\3\4', line)
    
    # Handle multi-line execute
    # Actually, let's just do manual replacement for the ones we found earlier.
    new_lines.append(line)

# Let's just do specific line replacements for accuracy.
targets = [
    (129, '"SELECT pnl_idr FROM trade_history WHERE pair = ? "', '"SELECT pnl_idr FROM trade_history WHERE pair = %s "'),
    (160, '"SELECT ch_5m, ch_15m, ch_1h FROM ticker_history WHERE pair = ?",', '"SELECT ch_5m, ch_15m, ch_1h FROM ticker_history WHERE pair = %s",'),
    (267, '"SELECT history_json FROM ticker_history WHERE pair = ?", (pair,)', '"SELECT history_json FROM ticker_history WHERE pair = %s", (pair,)'),
    (281, '"SELECT price, vol, is_bid FROM market_depth WHERE pair = ?", (pair,)', '"SELECT price, vol, is_bid FROM market_depth WHERE pair = %s", (pair,)'),
    (409, '"SELECT history_json FROM ticker_history WHERE pair = ?",', '"SELECT history_json FROM ticker_history WHERE pair = %s",'),
    (416, '"SELECT price, vol, is_bid FROM market_depth WHERE pair = ?",', '"SELECT price, vol, is_bid FROM market_depth WHERE pair = %s",'),
]

for line_num, old, new in targets:
    idx = line_num - 1
    if old in lines[idx]:
        lines[idx] = lines[idx].replace(old, new)

with open(file_path, 'w') as f:
    f.writelines(lines)
