import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["API_KEY"] = "test_api_key"
os.environ["API_URL"] = "https://api.deepseek.com/v1"
