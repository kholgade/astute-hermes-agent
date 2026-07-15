import pytest
import sys
from pathlib import Path

# Ensure parent conftest fixtures are available
parent_conftest = Path(__file__).parent.parent / "conftest.py"
if parent_conftest.exists():
    # Import fixtures from parent
    import importlib.util
    spec = importlib.util.spec_from_file_location("parent_conftest", parent_conftest)
    parent_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent_module)
