"""Launch Reading Room from a source checkout."""
import runpy
import sys
from pathlib import Path
folder=Path(__file__).resolve().parent/'local_tool'
sys.path.insert(0,str(folder))
runpy.run_path(str(folder/'app.py'),run_name='__main__')
