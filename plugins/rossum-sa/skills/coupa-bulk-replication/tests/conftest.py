"""Puts the skill dir on sys.path so tests import coupa_bulk_import directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
