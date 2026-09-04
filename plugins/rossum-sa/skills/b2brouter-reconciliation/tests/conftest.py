"""Puts the skill dir on sys.path so tests import discovery/match/rossum/
b2brouter/recon directly, as flat sibling modules (not a package)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
