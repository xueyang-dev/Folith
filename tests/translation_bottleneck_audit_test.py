"""Audit arithmetic must not confuse overlapping work with elapsed time."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bottleneck_audit", Path(__file__).resolve().parents[1] / "scripts/translation_bottleneck_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTest(unittest.TestCase):
    def test_unordered_overlapping_intervals_and_clipping(self):
        intervals = [(5, 9), (1, 8), (2, 3)]
        result = audit.occupancy(intervals)
        self.assertEqual(result["sum_s"], 12)
        self.assertEqual(result["union_s"], 8)
        self.assertEqual(result["span_s"], 8)
        self.assertEqual(result["peak"], 2)
        clipped = audit.occupancy(intervals, (3, 6))
        self.assertEqual(clipped["sum_s"], 4)
        self.assertEqual(clipped["union_s"], 3)

    def test_collector_does_not_export_document_or_configuration_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps({
                "filename": "PRIVATE_FILENAME", "paras": ["PRIVATE_SOURCE"],
                "pairs": [{"target": "PRIVATE_TARGET"}],
                "pipeline_config": {"api_key": "PRIVATE_SECRET"}}))
            encoded = json.dumps(audit.collect_job(root))
            self.assertNotIn("PRIVATE_", encoded)
            self.assertEqual(audit.collect_job(root)["source_chars"], 14)


if __name__ == "__main__":
    unittest.main()
