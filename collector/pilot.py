"""Run three full control-instrument cycles separated by five minutes."""
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for i in range(1,4):
    subprocess.run([sys.executable,str(ROOT/'collector/collect.py'),'--mode','pilot','--cycle-label',f'pilot-{i}'],cwd=ROOT,check=True)
    if i<3:
        print('Five-minute pause before next pilot cycle',flush=True)
        time.sleep(300)
