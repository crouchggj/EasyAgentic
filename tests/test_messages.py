import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime.journal import TaskJournal
from agent_runtime.config import Settings
from agent_runtime.execution import Task, tool_results
from agent_runtime.storage import SessionStore
from agent_runtime.messages import MessageBus
from tools.todo import TodoManager
from tools.registry import setup_registry
from test_agents import message
from types import SimpleNamespace as NS


class MessageTests(unittest.TestCase):
    def test_ids_deduplicate_send_and_reject_mismatched_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            bus.send('lead','worker','same',message_id='stable')
            bus.send('lead','worker','same',message_id='stable')
            self.assertEqual(len(bus.status()),1)
            with self.assertRaises(ValueError):
                bus.send('lead','worker','different',message_id='stable')
            delivery = bus.claim('worker')
            self.assertEqual(len(delivery),1)
            self.assertEqual(bus.claim('worker'),[])
            bus.acknowledge(delivery)
            self.assertEqual(bus.claim('worker'),[])

    def test_crash_before_checkpoint_redelivers_after_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp),lease_seconds=1)
            bus.send('lead','worker','work',message_id='recover')
            delivery = bus.claim('worker')
            with patch('agent_runtime.messages.time.time',return_value=delivery[0]['payload'].get('timestamp',0)+10**12):
                reopened = MessageBus(Path(tmp))
                redelivery = reopened.claim('worker')
            self.assertEqual(redelivery[0]['id'],delivery[0]['id'])
            self.assertEqual(redelivery[0]['attempt'],2)
            bus.acknowledge(delivery)  # Old lease cannot acknowledge a newer claim.
            self.assertEqual(bus.status()[0]['state'],'inflight')
            bus.acknowledge(redelivery)
            self.assertEqual(bus.status()[0]['state'],'acked')

    def test_checkpoint_failure_requeues_and_crash_after_checkpoint_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp),lease_seconds=1)
            bus.send('lead','worker','work',message_id='recover')
            seen = set()
            def fail(data, identifiers):
                raise OSError('disk full')
            with self.assertRaises(OSError):
                bus.receive('worker',seen,fail)
            self.assertEqual(seen,set())
            self.assertEqual(bus.status()[0]['state'],'pending')
            store = SessionStore(Path(tmp)/'worker.json')
            def save(data,identifiers):
                store.save([{'role':'user','content':data}],[],'running',received_ids=identifiers)
            with patch.object(bus,'acknowledge',side_effect=SystemExit('crash')):
                with self.assertRaises(SystemExit):
                    bus.receive('worker',seen,save)
            recovered_seen = set(store.load()['received_ids'])
            invoked = []
            bus.receive('worker',recovered_seen,lambda *args: invoked.append(args))
            self.assertEqual(invoked,[])
            self.assertEqual(bus.status()[0]['state'],'acked')

    def test_attempt_limit_dead_letter_and_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp),max_attempts=2)
            bus.send('lead','worker','work',message_id='poison')
            for _ in range(2):
                bus.reject(bus.claim('worker'),'checkpoint failure')
            self.assertEqual(bus.claim('worker'),[])
            self.assertEqual(bus.status()[0]['state'],'dead')
            bus.retry('poison')
            self.assertEqual(bus.claim('worker')[0]['attempt'],1)

    def test_read_inbox_tool_acknowledges_only_after_durable_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = MessageBus(Path(tmp))
            bus.send('worker','lead','result',message_id='tool-result')
            registry = setup_registry(TodoManager(),bus=bus)
            store = SessionStore(Path(tmp)/'session.json')
            call = NS(id='inbox',function=NS(name='read_inbox',arguments='{}'))
            recorded = []
            def checkpoint(result):
                self.assertEqual(bus.status()[0]['state'],'inflight')
                recorded.append(result)
                store.save(recorded,[],'running',received_ids=registry.received_ids())
            tool_results(message(calls=[call]),registry,Task(Settings(state_dir=tmp)),checkpoint)
            self.assertEqual(bus.status()[0]['state'],'acked')
            self.assertEqual(store.load()['received_ids'],['tool-result'])
            self.assertIn('result',recorded[0]['content'])

    def test_legacy_migration_and_interrupted_task_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'inbox').mkdir()
            (path/'inbox'/'worker.jsonl').write_text(json.dumps({'from':'lead','content':'old','type':'message'})+'\n')
            bus = MessageBus(path)
            self.assertEqual(json.loads(bus.read_inbox('worker'))[0]['content'],'old')
            self.assertTrue((path/'inbox'/'worker.jsonl.migrated').exists())
            self.assertEqual(MessageBus(path).read_inbox('worker'),'[]')
            journal = TaskJournal(path)
            journal.record(Task(Settings(state_dir=tmp)),'running')
            journal.record(Task(Settings(state_dir=tmp)),'completed')
            journal.recover()
            self.assertEqual({r['status'] for r in journal.list()},{'interrupted','completed'})

    def test_completed_outbox_recovery_does_not_rerun_model_or_duplicate_result(self):
        import time
        from easyagentic.agents.teammates import TeammateManager
        from test_agents import Client, message
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bus = MessageBus(path)
            client = Client([message('durable result')])
            manager = TeammateManager(path,client,'model',bus,settings=Settings(state_dir=tmp))
            try:
                with patch.object(manager,'_publish_outbox',side_effect=OSError('delivery unavailable')):
                    manager.spawn('worker','tester','task')
                    deadline = time.monotonic()+3
                    while manager.list_members()[0]['status'] != 'shutdown' and time.monotonic()<deadline:
                        time.sleep(0.01)
                    self.assertEqual(manager.list_members()[0]['outcome'],'delivery_pending')
                    snapshot = manager._store('worker').load()
                    self.assertEqual(snapshot['status'],'completed')
                    self.assertEqual(len(snapshot['outbox']),1)
                    self.assertEqual(bus.status(),[])
                for _ in range(2):
                    manager.resume('worker')
                    manager.wait('worker',2)
                    # The short-lived restore thread must finish before the next resume.
                    manager.teammates['worker'].thread.join(timeout=2)
                self.assertEqual(len(client.requests),1)
                deliveries = bus.claim('lead')
                self.assertEqual(len(deliveries),1)
                self.assertEqual(deliveries[0]['payload']['content'],'durable result')
            finally:
                manager.shutdown_all()

    def test_checkpoint_initialization_failure_is_visible_to_lead(self):
        from easyagentic.agents.teammates import TeammateManager
        from test_agents import Client
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bus = MessageBus(path)
            manager = TeammateManager(path,Client([]),'model',bus,settings=Settings(state_dir=tmp))
            try:
                with patch.object(SessionStore,'load',side_effect=OSError('unavailable')):
                    manager.spawn('worker','tester','task')
                    manager.teammates['worker'].thread.join(timeout=3)
                self.assertFalse(manager.teammates['worker'].thread.is_alive())
                self.assertEqual(manager.list_members()[0]['outcome'],'error')
                deliveries = bus.claim('lead')
                self.assertEqual(deliveries[0]['payload']['type'],'error')
                self.assertIn('infrastructure failed',deliveries[0]['payload']['content'])
            finally:
                manager.shutdown_all()

    def test_process_crash_before_and_after_checkpoint_recovers_without_duplicate(self):
        import subprocess
        import sys
        import time
        script = '''
import os
from pathlib import Path
import sys
from agent_runtime.messages import MessageBus
from agent_runtime.storage import SessionStore
root=Path(sys.argv[1])
bus=MessageBus(root,lease_seconds=1)
def checkpoint(data,identifiers):
    if sys.argv[2]=='after':
        SessionStore(root/'checkpoint.json').save([{'role':'user','content':data}],[],'running',received_ids=identifiers)
    os._exit(23)
bus.receive('worker',set(),checkpoint)
'''
        for phase in ('before','after'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bus = MessageBus(root,lease_seconds=1)
                bus.send('lead','worker','durable work',message_id='crash-message')
                result = subprocess.run([sys.executable,'-c',script,tmp,phase],capture_output=True,text=True,
                                        cwd=Path(__file__).resolve().parents[1],timeout=10)
                self.assertEqual(result.returncode,23,result.stderr)
                self.assertEqual(bus.status()[0]['state'],'inflight')
                saved = SessionStore(root/'checkpoint.json').load()
                seen = set(saved['received_ids']) if saved else set()
                received = []
                with patch('agent_runtime.messages.time.time',return_value=time.time()+100):
                    MessageBus(root).receive('worker',seen,lambda data,ids: received.append(data))
                self.assertEqual(len(received),1 if phase=='before' else 0)
                self.assertEqual(bus.status()[0]['state'],'acked')
