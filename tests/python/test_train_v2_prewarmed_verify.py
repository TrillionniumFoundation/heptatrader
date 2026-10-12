"""No-product-trust, original source signature/provenance negative tests."""
import copy,hashlib,tempfile,unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from train_v2.contracts import ContractError
from train_v2.prewarmed_source_worker import DOMAIN,SCHEMA
from train_v2.prewarmed_source_verify import (
 SIGNED_FIELDS,source_prefix,verify_signed_packet
)
from historical_training.common import canonical

class ImmutablePrefixTests(unittest.TestCase):
 def test_complete_earlier_prefix_can_be_replayed_after_append(self):
  original=b'{"first":1}\n{"second":2}\n'
  later=original+b'{"third":3}\n'
  self.assertEqual(source_prefix(later,hashlib.sha256(original).hexdigest()),original)
 def test_partial_ended_prefix_is_not_valid(self):
  source=b'{"first":1}\n{"second":2}'
  with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_PREFIX_NOT"):
   source_prefix(source,hashlib.sha256(source).hexdigest())
 def test_changed_old_prefix_is_not_reclassified(self):
  original=b'{"first":1}\n'
  modified=b'{"first":2}\n{"other":3}\n'
  with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_PREFIX_NOT"):
   source_prefix(modified,hashlib.sha256(original).hexdigest())
 def test_forged_large_original_source_is_bounded(self):
  with self.assertRaisesRegex(ContractError,"PROVENANCE_FILE_TOO_LARGE"):
   source_prefix(b"x"*(64*1024*1024+1),"0"*64)

class PublicResearchSignatureTests(unittest.TestCase):
 def setUp(self):
  self.key=Ed25519PrivateKey.generate()
  pub=self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
  self.packet={
   "schema":SCHEMA,"plan_id":"a"*64,"event_id":"b"*64,
   "feature_id":"c"*64,"dataset_id":"d"*64,
   "original_source_path":"/frozen/input/segment-123/quotes.jsonl",
   "original_source_prefix_sha256":"e"*64,
   "original_quote_receipt":"f"*64,
   "original_event_at_ms":1791789327675,
   "original_available_at_ms":1791789328138,
   "original_expires_at_ms":1791789332675,
   "decision_at_ms":1791789328688,
   "source_event_at_ms":1791789328138,
   "source_worker_source_sha256":"0"*64,
   "no_action_or_model_inference":True,
   "not_original_llm_policy":True,
   "test_signature_only":True,
   "broker_orders_sent":0
  }
  assert set(self.packet)==set(SIGNED_FIELDS)
  message=DOMAIN+hashlib.sha256(canonical(self.packet)).digest()
  sig=self.key.sign(message)
  self.proof={**self.packet,
   "test_only_mac_public_key_hex":pub.hex(),
   "test_only_signature_hex":sig.hex(),
   "signed_message_hex":message.hex(),
   "signature_sha256":hashlib.sha256(sig).hexdigest(),
   "public_key_sha256":hashlib.sha256(pub).hexdigest(),
   "test_signature_independently_replayable_without_private_key":True,
   "trade_authority":False,"native_ledger_writer_ack":False
  }
 def test_genuine_testonly_signature_passes_without_private_key(self):
  self.assertEqual(verify_signed_packet(self.proof),self.packet)
 def test_changed_unprotected_field_cannot_gain_authority(self):
  tampered=copy.deepcopy(self.proof)
  tampered["trade_authority"]=True
  with self.assertRaisesRegex(ContractError,"FALSE_PRODUCT_AUTHORITY"):
   verify_signed_packet(tampered)
 def test_rehashed_decision_time_does_not_change_signed_message(self):
  tampered=copy.deepcopy(self.proof)
  tampered["decision_at_ms"]+=1000
  with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_MESSAGE_DIGEST_CHANGED"):
   verify_signed_packet(tampered)
 def test_substituted_public_key_fails(self):
  tampered=copy.deepcopy(self.proof)
  bad=Ed25519PrivateKey.generate().public_key().public_bytes(
       Encoding.Raw,PublicFormat.Raw)
  tampered["test_only_mac_public_key_hex"]=bad.hex()
  tampered["public_key_sha256"]=hashlib.sha256(bad).hexdigest()
  with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_PUBLIC_SIGNATURE_INVALID"):
   verify_signed_packet(tampered)
 def test_mac_test_signature_tamper_fails_even_if_sha_updated(self):
  tampered=copy.deepcopy(self.proof)
  changed=bytearray(bytes.fromhex(tampered["test_only_signature_hex"]))
  changed[0]^=1
  tampered["test_only_signature_hex"]=bytes(changed).hex()
  tampered["signature_sha256"]=hashlib.sha256(changed).hexdigest()
  with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_PUBLIC_SIGNATURE_INVALID"):
   verify_signed_packet(tampered)

class FrozenResearchLedgerTests(unittest.TestCase):
 def setUp(self):
  import sqlite3
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.file=Path(self.tmp.name)/"trial.sqlite"
  with sqlite3.connect(self.file) as db:
   db.execute("CREATE TABLE events (event_id TEXT,body TEXT)")
   db.execute("CREATE TABLE samples (feature_id TEXT,state TEXT,source_event_id TEXT)")
   db.execute("CREATE TABLE review_batches (fingerprint TEXT)")
   db.execute("INSERT INTO events VALUES (?,?)",("id","{}"))
   db.execute("INSERT INTO samples VALUES (?,?,?)",("feature","AWAITING_OUTCOME","id"))
   db.commit()
  self.file.chmod(0o600)

 def test_readonly_immutable_sqlite_creates_no_journal_or_shm_sidecars(self):
  from train_v2.prewarmed_source_verify import frozen_ledger_rows
  import hashlib
  original=self.file.read_bytes()
  mtime=self.file.stat().st_mtime_ns
  wal=Path(str(self.file)+"-wal")
  shm=Path(str(self.file)+"-shm")
  self.assertFalse(wal.exists())
  self.assertFalse(shm.exists())
  records,rows,batches=frozen_ledger_rows(self.file)
  self.assertEqual(records,[("id","{}")])
  self.assertEqual(rows,[("feature","AWAITING_OUTCOME","id")])
  self.assertEqual(batches,0)
  self.assertEqual(self.file.read_bytes(),original)
  self.assertEqual(self.file.stat().st_mtime_ns,mtime)
  self.assertFalse(wal.exists())
  self.assertFalse(shm.exists())

 def test_uncheckpointed_wal_is_not_relabelled_durable(self):
  from train_v2.prewarmed_source_verify import frozen_ledger_rows
  wal=Path(str(self.file)+"-wal")
  wal.write_bytes(b"UNCOMMITTED_JOURNAL_DATA")
  wal.chmod(0o600)
  with self.assertRaisesRegex(ContractError,"RESEARCH_DB_WAL_NOT_CHECKPOINTED"):
   frozen_ledger_rows(self.file)

 def test_symlinked_ledger_db_rejected(self):
  from train_v2.prewarmed_source_verify import frozen_ledger_rows
  link=Path(self.tmp.name)/"shadow.sqlite"
  link.symlink_to(self.file)
  with self.assertRaisesRegex(ContractError,"FROZEN_RESEARCH_DB_UNSAFE"):
   frozen_ledger_rows(link)
