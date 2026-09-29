"""Run the bridge test-suite inside Blender.

    blender -b --factory-startup --python tests/bridge/run_tests.py [-- -k pattern]
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "bridge"))
sys.path.insert(0, HERE)

import gbtest  # noqa: E402,F401 - bootstraps a throwaway session


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    loader = unittest.TestLoader()
    if "-k" in argv:
        loader.testNamePatterns = ["*" + argv[argv.index("-k") + 1] + "*"]
    suite = loader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)
    sys.stderr.flush()
    sys.stdout.flush()
    os._exit(0 if result.wasSuccessful() else 1)


main()
