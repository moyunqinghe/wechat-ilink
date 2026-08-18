import sys
from pathlib import Path

# Make the in-repo package importable when running `pytest channels/wechat/tests`
# directly (without installing wechat-ilink).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
