"""Explicit optional model download; never runs at app startup."""
import argparse,runpy
from pathlib import Path
p=argparse.ArgumentParser(description='Download an optional offline model (large files).')
p.add_argument('model',choices=['whisper','sensevoice'])
a=p.parse_args()
runpy.run_path(str(Path(__file__).with_name('download_'+a.model+'.py')),run_name='__main__')
