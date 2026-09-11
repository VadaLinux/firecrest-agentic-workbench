import sys
from pathlib import Path

# The server and client are top-level modules in firecrest-mcp/, not a package,
# so tests import them by putting that directory on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
