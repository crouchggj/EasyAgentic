import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from agent_runtime.client import CancellableClient
from agent_runtime.config import Settings
from agent_runtime.review import ReviewQueue, ReviewRequired
from e2e_support import WORKFLOW_QUERY, build_agent


class EndToEndTests(unittest.TestCase):
    def test_read_delegate_wait_summarize_through_real_sdk(self):
        requests = []
        stages = {'lead':0,'peer':0}
        lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                actor = 'lead' if "named 'lead'" in data['messages'][0]['content'] else 'peer'
                with lock:
                    stages[actor] += 1
                    stage = stages[actor]
                    requests.append((actor,data))
                action = None
                content = None
                if stage == 1:
                    action = ('read_file',{'path':'README.md'})
                elif actor == 'lead' and stage == 2:
                    action = ('spawn',{'name':'reviewer','role':'architecture reviewer','prompt':'Read README.md and summarize architecture.'})
                elif actor == 'lead' and stage == 3:
                    action = ('team_wait',{'name':'reviewer','timeout':2})
                elif actor == 'peer':
                    content = 'ARCHITECTURE_OK: CLI, tools and runtime.'
                else:
                    included = any('ARCHITECTURE_OK' in str(m.get('content','')) for m in data['messages'])
                    content = 'E2E_READY: ARCHITECTURE_OK incorporated.' if included else 'MISSING_TEAM_RESULT'
                message = {'role':'assistant','content':content}
                if action:
                    message['tool_calls'] = [{'id':f'{actor}-{stage}','type':'function','function':{'name':action[0],'arguments':json.dumps(action[1])}}]
                payload = json.dumps({'id':'chatcmpl-e2e','object':'chat.completion','created':0,'model':data['model'],
                    'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if action else 'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        client = CancellableClient(api_key='local-e2e-only',base_url=f'http://127.0.0.1:{server.server_port}/v1',max_retries=0)
        manager = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'README.md').write_text('Architecture: CLI, tools and runtime.\n')
                settings = Settings(workdir=tmp,state_dir=str(root/'state'),team_wait_timeout=2)
                agent,manager = build_agent(client,settings)
                result = agent.run(WORKFLOW_QUERY)
                self.assertIn('E2E_READY',result)
                self.assertIn('ARCHITECTURE_OK',result)
                members = manager.list_members()
                self.assertEqual(len(members),1)
                self.assertEqual(members[0]['outcome'],'completed')
                self.assertEqual(agent.store.load()['status'],'completed')
                self.assertTrue(agent.received_ids)
                self.assertEqual({r['state'] for r in agent.bus.status()},{'acked'})
                events = [json.loads(line) for line in (root/'state'/'events.jsonl').read_text().splitlines()]
                tools = [e['tool'] for e in events if e['event']=='tool_result']
                self.assertEqual(tools.count('read_file'),2)
                self.assertIn('spawn',tools)
                self.assertIn('team_wait',tools)
                self.assertEqual(stages,{'lead':4,'peer':2})
                manager.shutdown_all()
        finally:
            if manager:
                manager.shutdown_all()
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cli_review_commands_do_not_execute_on_approval_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root/'state'
            queue = ReviewQueue(state/'reviews')
            operation = {'tool':'write_file','workdir':tmp,'path':str(root/'new.txt'),
                         'before':'','content':'new\n','existed':False}
            with self.assertRaises(ReviewRequired) as caught:
                queue.require(operation)
            identifier = caught.exception.request_id
            env = dict(os.environ,OPENAI_API_KEY='local-cli-only',EASYAGENTIC_WORKDIR=tmp,
                       EASYAGENTIC_STATE_DIR=str(state),TEAM_DIR=str(state/'team'))
            result = subprocess.run([sys.executable,'main.py'],
                input=f'/review {identifier}\n/approve {identifier}\n/reviews\n/tasks\n/messages\nquit\n',
                env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('+new',result.stdout)
            self.assertIn('approved; use /continue',result.stdout)
            self.assertFalse((root/'new.txt').exists())
