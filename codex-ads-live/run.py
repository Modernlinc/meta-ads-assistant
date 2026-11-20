"""Portable package-local entrypoint; no environment or global install needed."""
from pathlib import Path
import sys
if sys.version_info < (3,12):
    print('{"status":"REJECTED","error_code":"PYTHON_3_12_OR_NEWER_REQUIRED"}')
    sys.exit(2)
sys.path.insert(0,str(Path(__file__).resolve().parent/"src"))
from codex_ads_live.__main__ import main

if __name__=="__main__": sys.exit(main())
