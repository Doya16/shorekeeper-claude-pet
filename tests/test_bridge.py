import json,pathlib,tempfile,unittest
from datetime import datetime,timezone
from shorekeeper_pet.bridge import SessionReader,RateClient
from shorekeeper_pet.telemetry import sanitize,receive,phase

class BridgeTests(unittest.TestCase):
    def hook(self,reader,event,when,**extra):
        reader.accept_hook(sanitize(dict(session_id='test',hook_event_name=event,**extra),now=when))

    def test_tool_loop_keeps_one_turn_and_terminal_deduplicates(self):
        reader=SessionReader()
        self.hook(reader,'UserPromptSubmit',100,prompt_id='turn1')
        for index,name in enumerate(('Read','Edit','Bash','WebSearch')):
            self.hook(reader,'PreToolUse',101+index*2,tool_name=name,tool_use_id=name)
            self.assertEqual(reader.state,phase(name))
            self.hook(reader,'PostToolUse',102+index*2,tool_name=name,tool_use_id=name)
            self.assertEqual(reader.turn_id,'turn1')
        self.hook(reader,'Stop',110);self.hook(reader,'Stop',111)
        self.assertEqual(len(reader.drain_events()),1)
        self.assertFalse(reader.active)

    def test_failure_is_recoverable_until_stop_failure(self):
        reader=SessionReader()
        self.hook(reader,'UserPromptSubmit',100)
        self.hook(reader,'PreToolUse',101,tool_name='Edit')
        self.hook(reader,'PostToolUseFailure',102,tool_name='Edit')
        self.assertTrue(reader.active)
        self.hook(reader,'PermissionRequest',103);self.assertEqual(reader.state,'waiting')
        self.hook(reader,'StopFailure',104)
        self.assertEqual(reader.drain_events()[0]['state'],'error')

    def test_private_fields_and_subagents_never_leave_receiver(self):
        raw=dict(session_id='x',hook_event_name='PreToolUse',tool_name='Write',tool_input={'content':'secret'},
                 prompt='secret',transcript_path='secret',cwd='C:/isolated-test')
        self.assertNotIn('secret',json.dumps(sanitize(raw)))
        self.assertIsNone(sanitize(dict(raw,agent_id='child')))

    def test_partial_transcript_and_desktop_wrap_up(self):
        def event(kind,second,**message):
            return dict(type=kind,uuid=str(second),timestamp=datetime.fromtimestamp(second,timezone.utc).isoformat(),message=message)
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'test.jsonl';reader=SessionReader(path)
            raw=(json.dumps(event('user',100,content='private'))+'\n').encode()
            path.write_bytes(raw[:20]);reader.poll();self.assertFalse(reader.active)
            with path.open('ab') as file:file.write(raw[20:])
            reader.poll();self.assertTrue(reader.active)
            reader.accept(event('assistant',101,content=[dict(type='tool_use',id='t',name='mcp__ccd_turn__wrap_up',input={})]))
            self.assertTrue(reader.active)
            reader.accept(event('user',102,content=[dict(type='tool_result',tool_use_id='t',content='private')]))
            self.assertEqual(reader.drain_events()[0]['state'],'done')
            self.assertEqual(reader.public_note,'')

    def test_new_status_without_limits_clears_previous_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            receive(dict(session_id='x',rate_limits={'five_hour':{'used_percentage':20}}),True,folder,now=100)
            client=RateClient(store=folder,desktop=[]);self.assertEqual(len(client.read()['windows']),1)
            receive(dict(session_id='x'),True,folder,now=101)
            self.assertEqual(client.read()['windows'],[])

if __name__=='__main__':unittest.main()
