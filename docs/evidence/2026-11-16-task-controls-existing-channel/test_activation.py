"""Offline preservation checks; no real lifecycle, gate, DB, or model operation."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

class Activation(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('task_controls_activation',HERE/'activate.py')
  self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  self.root=Path(tmp.name);self.m.OUT=self.root/'receipts';self.m.OUT.mkdir()

 def test_one_existing_leaf_fixed_processes_and_epochs(self):
  self.assertEqual(self.m.ALLOWED,{'routers/erp_task_controls.py'})
  self.assertEqual(self.m.NEW,set())
  self.assertEqual(self.m.EXPECTED,{self.m.MAIN:(3469940,'78178657'),self.m.QA:(3469960,'78178661')})
  self.assertEqual(self.m.NODE,(2877321,'77927719'))
  source=(HERE/'activate.py').read_text()
  for expected in ["[('open',54)]","closed=gate('close',54)","closed['epoch']==55",
                   "opened=gate('open',55)","opened['epoch']==56","len(after)==75"]:
   self.assertIn(expected,source)

 def test_no_active_producer_is_excluded(self):
  calls=[]
  def query(name,sql,args=()):
   calls.append((name,sql,args));return [(1,)]
  with patch.object(self.m,'query',side_effect=query),patch.object(self.m,'http',return_value={
      'authenticated':True,'target_ready':True,'active_relay_turns':1}):
   counts=self.m.producer_counts()
  self.assertTrue(all(v==1 for v in counts.values()))
  self.assertFalse(any('id!=' in sql or 'NOT IN' in sql or args for _,sql,args in calls))

 def test_exact_failed_and_finished_history_no_duplicate_generation(self):
  rows=[(self.m.INV,'failed',1,'project_manager'),
        ('agov-transport-retry-9729e771769c6effcd5a52080227acaca861f60e6f5ee7f2','execution_finished',1,'project_manager')]
  with patch.object(self.m,'query',return_value=rows):self.m.original()
  for changed in [rows+rows[1:], rows[:1], [(rows[0][0],'running',1,'project_manager')]+rows[1:]]:
   with patch.object(self.m,'query',return_value=changed):
    with self.assertRaisesRegex(RuntimeError,'HISTORY_CHANGED'):self.m.original()

 def test_all_governance_and_provider_tables_are_preserved_not_only_running(self):
  calls=[]
  def query(name,sql,args=()):
   calls.append((name,sql))
   if 'sqlite_master' in sql:return [('history_table',),('control_journal',)]
   if sql.startswith('PRAGMA'):return [(0,'id'),(1,'payload')]
   return [('id',b'private-value')]
  with patch.object(self.m,'query',side_effect=query):result=self.m.originals()
  self.assertIn('agentic-governance.sqlite:control_journal',result)
  self.assertIn('abitdot-primary.sqlite:history_table',result)
  self.assertIn('maintenance-admission/gate.sqlite:lease',result)
  self.assertIn('webui.db:automation_run',result)
  self.assertNotIn('private-value',json.dumps(result))
  self.assertFalse(any('WHERE state' in sql or 'WHERE status' in sql for _,sql in calls))

 def test_install_backs_up_exact_old_bytes_and_preserves_metadata(self):
  target=self.root/'existing.py';target.write_bytes(b'old\n');target.chmod(0o640)
  before=self.m.metadata(target);self.m.install(target,b'new\n','owned.before')
  self.assertEqual(self.m.metadata(target),before)
  self.assertEqual((self.m.OUT/'owned.before').read_bytes(),b'old\n')
  self.assertEqual(target.read_bytes(),b'new\n')

 def test_unknown_service_action_is_never_repeated(self):
  with patch.object(self.m.subprocess,'run',return_value=type('R',(),{'returncode':1,'stdout':b'','stderr':b''})())as run:
   with self.assertRaisesRegex(RuntimeError,'NO_REPEAT'):self.m.lifecycle(self.m.MAIN,'stop')
   self.assertEqual(run.call_count,1)
   with self.assertRaises(FileExistsError):self.m.lifecycle(self.m.MAIN,'stop')
   self.assertEqual(run.call_count,1)

if __name__=='__main__':unittest.main()
