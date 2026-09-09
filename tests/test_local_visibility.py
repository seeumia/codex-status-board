"""Include the installed Skill's regression suite in the repository CI command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex-status-board/tests'))
from test_visibility import VisibilityTests
