import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "HealthCompanionAgent"))
sys.path.insert(0, str(ROOT / "tools" / "health_tools"))

# boto3 clients are created at import time; give them a region but no credentials.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_REGION", "us-west-2")
