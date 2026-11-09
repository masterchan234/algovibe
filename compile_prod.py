import os
import subprocess
import sys
from pathlib import Path

def main():
    failed = False
    
    # We target core directories that contain our proprietary code
    targets = ['app', 'services', 'shared', 'utils']
    
    for target in targets:
        target_path = Path(target)
        if not target_path.exists():
            continue
            
        for p in target_path.glob('**/*.py'):
            # Skip package indicator and migrations
            if p.name == '__init__.py' or p.name == 'main.py' or 'alembic' in p.parts or 'alembic_backtest' in p.parts:
                continue
                
            print(f'[Compiler] Cythonizing: {p}')
            # Run cythonize command in-place using Python 3 rules
            res = subprocess.run(['cythonize', '-i', '-3', str(p)])
            if res.returncode != 0:
                print(f'[Compiler] ❌ Failed to compile: {p}')
                failed = True
                
    if failed:
        sys.exit(1)

if __name__ == '__main__':
    main()
