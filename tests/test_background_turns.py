import json,pathlib,tempfile,time,unittest
from datetime import datetime,timezone
from unittest.mock import patch
from shorekeeper_pet.bridge import SessionReader,Monitor,RateClient,turn_is_background
from shorekeeper_pet.telemetry import sanitize
from shorekeeper_pet.quota import quota_rows,quota_brief,source_lines

def stamp(second):return datetime.fromtimestamp(second,timezone.utc).isoformat()

def prompt(second,text,origin='human',**extra):
    row=dict(type='user',uuid=f'u{second}',promptId=f'p{second}',timestamp=stamp(second),message=dict(role='user',content=text),**extra)
    if origin=='human':row.update(turnOrigin='human',origin={'kind':'human'})
    elif origin:row.update(turnOrigin=origin,origin={'kind':origin.replace('_','-')})
    return row

def reply(second,stop='end_turn'):
    return dict(type='assistant',uuid=f'a{second}',timestamp=stamp(second),message=dict(role='assistant',content=[dict(type='text',text='ok')],stop_reason=stop))

class TurnOriginTests(unittest.TestCase):
    def test_desktop_markers_and_legacy_wrappers(self):
        self.assertFalse(turn_is_background(prompt(1,'<system-reminder>context</system-reminder> hi')))
        self.assertTrue(turn_is_background(prompt(1,'<task-notification>…','task_notification')))
        self.assertTrue(turn_is_background(prompt(1,'Task (from the session that started this one): x','sdk')))
        self.assertTrue(turn_is_background(prompt(1,'<task-notification><task-id>x</task-id>',None)))
        self.assertFalse(turn_is_background(prompt(1,'帮我修一下桌宠',None)))

    def test_background_turn_is_marked_and_cannot_hijack_a_running_request(self):
        reader=SessionReader()
        reader.accept(prompt(100,'修好它'))
        reader.accept(prompt(101,'<task-notification>build done','task_notification'))
        self.assertTrue(reader.active);self.assertFalse(reader.background);self.assertEqual(reader.turn_id,'p100')
        reader.accept(reply(102))
        reader.accept(prompt(110,'<task-notification>monitor','task_notification'));reader.accept(reply(111))
        events=reader.drain_events()
        self.assertEqual([e['background'] for e in events],[False,True])
        self.assertEqual(reader.request['turn_id'],'p100');self.assertEqual(reader.request['state'],'done')
        self.assertEqual(reader.request_event,102)

    def test_request_far_before_the_tail_is_still_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'long.jsonl'
            filler=dict(type='attachment',timestamp=stamp(101),data='x'*50_000)
            rows=[prompt(100,'很早发出的需求')]+[filler]*60+[reply(200)]
            path.write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows),'utf8')
            self.assertGreater(path.stat().st_size,2_000_000)
            reader=SessionReader(path)
            for _ in range(3):reader.poll()
            self.assertEqual(reader.request['turn_id'],'p100');self.assertEqual(reader.request['state'],'done')

    def test_title_comes_from_the_session_name(self):
        reader=SessionReader();reader.accept(dict(prompt(100,'x'),cwd='P:/work/projects'))
        self.assertEqual(reader.title,'projects')
        reader.accept(dict(type='custom-title',customTitle='桌宠问题修复',sessionId='s'))
        self.assertEqual(reader.title,'桌宠问题修复')

    def test_hook_prompt_is_classified_without_keeping_its_text(self):
        item=sanitize(dict(session_id='s',hook_event_name='UserPromptSubmit',prompt='<task-notification>secret</task-notification>'))
        self.assertEqual(item['origin'],'background');self.assertNotIn('secret',json.dumps(item))
        self.assertEqual(sanitize(dict(session_id='s',hook_event_name='UserPromptSubmit',prompt='hello'))['origin'],'human')
        reader=SessionReader()
        reader.accept_hook(sanitize(dict(session_id='s',hook_event_name='PreToolUse',tool_name='Bash'),now=100))
        self.assertTrue(reader.background)

class MonitorModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=pathlib.Path(self.tmp.name)
        (self.home/'projects/p').mkdir(parents=True)
        self.monitor=Monitor(self.home,store=self.home/'events');self.monitor.observe_since=0
    def tearDown(self):self.tmp.cleanup()
    def write(self,sid,*rows):
        with (self.home/'projects/p'/(sid+'.jsonl')).open('a',encoding='utf8') as file:
            for row in rows:file.write(json.dumps(row,ensure_ascii=False)+'\n')
    def poll(self,now=200):
        self.monitor.last_discovery=0
        with patch('shorekeeper_pet.bridge.time.time',return_value=now):return self.monitor.poll()

    def test_silent_mode_hides_background_turns_and_keeps_the_request(self):
        self.write('main',prompt(100,'修好它'),reply(120))
        status=self.poll();self.assertEqual([e['thread_id'] for e in status['events']],['main'])
        self.write('main',prompt(150,'<task-notification>x','task_notification'))
        self.write('spawned',prompt(160,'Task (from the session that started this one): y','sdk'))
        status=self.poll()
        self.assertEqual(status['thread_id'],'main');self.assertEqual(status['state'],'idle')
        self.assertFalse(status['active']);self.assertFalse(status.get('background'))
        self.assertEqual(status['background_count'],2);self.assertEqual(status['request']['state'],'done')
        self.write('main',reply(170));self.write('spawned',reply(171))
        self.assertEqual(self.poll()['events'],[])

    def test_animate_and_full_modes(self):
        self.write('main',prompt(100,'修好它'),reply(120),prompt(150,'<task-notification>x','task_notification'))
        self.monitor.background_mode='animate';self.poll()
        status=self.poll()
        self.assertTrue(status['active']);self.assertTrue(status['background']);self.assertEqual(status['request']['turn_id'],'p100')
        self.monitor.background_mode='full';self.write('main',reply(170))
        self.assertEqual([e['background'] for e in self.poll()['events']],[True])

    def test_auto_follow_prefers_a_running_request_over_background_work(self):
        self.write('a',prompt(100,'第一个需求'))
        self.write('b',prompt(90,'旧需求'),reply(95),prompt(140,'<task-notification>x','task_notification'))
        self.assertEqual(self.poll()['thread_id'],'a')

class DesktopQuotaTests(unittest.TestCase):
    def client(self,folder,samples):
        path=pathlib.Path(folder)/'plan-usage-history.json'
        path.write_text(json.dumps(dict(version=2,samples=samples)),'utf8')
        return RateClient(store=pathlib.Path(folder)/'store',desktop=[path])

    def test_latest_desktop_sample_becomes_independent_windows(self):
        now=time.time()
        with tempfile.TemporaryDirectory() as folder:
            data=self.client(folder,[dict(t=(now-60)*1000,org='o',u=dict(fh=39,sd=0)),dict(t=(now-900)*1000,org='o',u=dict(fh=10,sd=5))]).read()
        rows=quota_rows(data,now)
        self.assertEqual([(r.bucket,r.remaining) for r in rows],[('five_hour',61),('seven_day',100)])
        self.assertEqual(data['source'],'desktop');self.assertTrue(data['sources']['desktop']['available'])
        brief=quota_brief(data,now);self.assertEqual(len(brief),1);self.assertIn('桌面版记录',brief[0]);self.assertNotIn('未更新',brief[0])
        self.assertIn('已超过 20 分钟',quota_brief(data,now+1300)[0])
        self.assertNotIn('*',rows[0].label);self.assertIn('*',quota_rows(data,now+1300)[0].label)
        # Five hours later the sample no longer describes the current window.
        self.assertEqual([r.bucket for r in quota_rows(data,now+5*3600)],['seven_day'])

    def test_missing_file_reports_where_to_look(self):
        with tempfile.TemporaryDirectory() as folder:
            data=RateClient(store=pathlib.Path(folder),desktop=[pathlib.Path(folder)/'missing.json']).read()
        self.assertIsNone(quota_rows(data)[0].remaining)
        self.assertTrue(any('未找到' in line for line in source_lines(data)))

if __name__=='__main__':unittest.main()
