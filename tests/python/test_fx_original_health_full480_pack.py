"""Synthetic full480 source archive packaging: never produce fake orders."""
from pathlib import Path
import tempfile,unittest
from test_fx_original_health_full480_portable import fixture
from train_v2.fx_original_health_full480_pack import (
    ExportHold, collect,pack,
)
from train_v2.fx_original_health_full480_portable import archive

class ArchiveTests(unittest.TestCase):
    def test_full_480_archive_is_private_and_strict(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            root=Path(t)/"root"
            out=Path(t)/"private"
            out.mkdir(mode=0o700)
            output=out/"full.tar.gz"
            r=pack(root,output,pins)
            self.assertEqual(r["original_files"],964)
            self.assertEqual(r["original_health_status_files"],480)
            self.assertFalse(r["production_cutover_authorized"])
            self.assertEqual(output.stat().st_mode&0o777,0o600)
            parsed,h=archive(output)
            self.assertEqual(len(parsed),964)
            self.assertEqual(h,r["archive_sha256"])
            self.assertEqual(parsed["plan.json"],docs["plan.json"])
            with self.assertRaisesRegex(ExportHold,"PRIVATE_NEW_ARCHIVE_DESTINATION"):
                pack(root,output,pins)
    def test_archive_cannot_wrap_nonexistent_final(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            (Path(t)/"root"/"final.json").unlink()
            with self.assertRaises(FileNotFoundError):
                collect(Path(t)/"root",pins)
    def test_modified_historical_receipt_refused(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            source=Path(t)/"root"/"cycle-400-receipt.json"
            source.write_bytes(source.read_bytes()+b" ")
            with self.assertRaisesRegex(ExportHold,"FULL_480_RECEIPT_CHAIN_DISAGREES"):
                collect(Path(t)/"root",pins)
    def test_public_original_source_never_exported(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            source=Path(t)/"root"/"cycle-400-status.json"
            source.chmod(0o644)
            with self.assertRaisesRegex(ExportHold,"UNSAFE_ORIGINAL_SOURCE_BYTES"):
                collect(Path(t)/"root",pins)
    def test_changed_original_plan_denied(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            source=Path(t)/"root"/"plan.json"
            source.write_bytes(source.read_bytes()+b" ")
            with self.assertRaisesRegex(ExportHold,"PRESEALED_ORIGINAL_ROOT_CHANGED"):
                collect(Path(t)/"root",pins)
    def test_symlink_historic_quote_status_denied(self):
        with tempfile.TemporaryDirectory() as t:
            docs,pre,full,pins=fixture(t)
            source=Path(t)/"root"/"cycle-400-status.json"
            original=Path(t)/"original-status.json"
            original.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(original)
            with self.assertRaisesRegex(ExportHold,"UNSAFE_ORIGINAL_SOURCE_LINK"):
                collect(Path(t)/"root",pins)

if __name__=="__main__":unittest.main()
