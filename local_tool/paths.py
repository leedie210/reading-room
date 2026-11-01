"""Portable local paths; private state is never part of the repository."""
import os
import sys
from pathlib import Path
PROJECT_ROOT=Path(__file__).resolve().parent.parent
DATA_ROOT=Path(os.environ.get('READING_ROOM_DATA',str(PROJECT_ROOT/'user_data'))).expanduser().resolve()
TOOLS_ROOT=Path(os.environ.get('READING_ROOM_TOOLS',str(PROJECT_ROOT/'tools'))).expanduser().resolve()
VAULT_ROOT=Path(os.environ.get('READING_ROOM_VAULT',str(DATA_ROOT/'Obsidian'))).expanduser().resolve()
DOWNLOADS_ROOT=Path(os.environ.get('READING_ROOM_DOWNLOADS',str(DATA_ROOT/'downloads'))).expanduser().resolve()

# Some Windows virtual environments omit Tcl's runtime search paths even when
# the standard Python installation contains the libraries. Use that installation.
if os.name=='nt':
    for variable,folder in [('TCL_LIBRARY','tcl8.6'),('TK_LIBRARY','tk8.6')]:
        candidate=Path(sys.base_prefix)/'tcl'/folder
        if candidate.is_dir():os.environ.setdefault(variable,str(candidate))
