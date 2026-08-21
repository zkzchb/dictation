import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shared" / "tools" / "verify_wheelhouse.py"
SPEC = importlib.util.spec_from_file_location("verify_wheelhouse", MODULE_PATH)
verify_wheelhouse = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify_wheelhouse)


class V2OfflineDistributionTests(unittest.TestCase):
    def test_frozen_wheelhouse_file_set_and_hashes_are_valid(self):
        count = verify_wheelhouse.verify_wheelhouse(
            ROOT / "v2" / "wheelhouse", check_platform=False
        )
        self.assertEqual(count, 16)

    def test_frozen_wheelhouse_rejects_modified_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            wheelhouse = Path(temp) / "wheelhouse"
            shutil.copytree(ROOT / "v2" / "wheelhouse", wheelhouse)
            target = next(wheelhouse.glob("*.whl"))
            target.write_bytes(target.read_bytes() + b"tampered")
            with self.assertRaises(verify_wheelhouse.WheelhouseError):
                verify_wheelhouse.verify_wheelhouse(
                    wheelhouse, check_platform=False
                )


if __name__ == "__main__":
    unittest.main()

