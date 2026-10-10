import os
import tempfile

# isolate DB/evidence for tests; must be set before the app is imported
os.environ.setdefault("CHAINSIGHT_VAR_DIR", tempfile.mkdtemp(prefix="chainsight-test-"))
os.environ["CHAINSIGHT_MODE"] = "demo"
