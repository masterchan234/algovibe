import re

with open('dashboard/server.py', 'r') as f:
    content = f.read()

old_block = """        # Configure buckets based on timeframe
        if tf == 'live':
            interval = '-2 hours'
            bucket = '1 minute'
        elif tf == '1h':
            interval = '-1 hours'
            bucket = '1 minute'
        elif tf == '24h':
            interval = '-24 hours'
            bucket = '15 minutes'
        elif tf == '7d':
            interval = '-7 days'
            bucket = '4 hours'
        else:
            interval = '-24 hours'
            bucket = '15 minutes'"""

new_block = """        # Configure buckets based on timeframe
        if tf == 'live':
            interval = '-2 hours'
            bucket = '1 minute'
        elif tf == '1h':
            interval = '-1 hours'
            bucket = '1 minute'
        elif tf == '1d' or tf == '24h':
            interval = '-24 hours'
            bucket = '15 minutes'
        elif tf == '1w' or tf == '7d':
            interval = '-7 days'
            bucket = '2 hours'
        elif tf == '1m' or tf == '30d':
            interval = '-30 days'
            bucket = '12 hours'
        elif tf == 'all':
            interval = '-3650 days'
            bucket = '1 day'
        else:
            interval = '-24 hours'
            bucket = '15 minutes'"""

content = content.replace(old_block, new_block)

# Fix time formatting for new timeframes
old_fmt = 'time_str = dt.strftime("%M:%S") if tf in (\'live\', \'1h\') else dt.strftime("%H:%M")'
new_fmt = 'time_str = dt.strftime("%M:%S") if tf in (\'live\', \'1h\') else (dt.strftime("%d %b") if tf in (\'1w\', \'7d\', \'1m\', \'30d\', \'all\') else dt.strftime("%H:%M"))'
content = content.replace(old_fmt, new_fmt)

with open('dashboard/server.py', 'w') as f:
    f.write(content)
