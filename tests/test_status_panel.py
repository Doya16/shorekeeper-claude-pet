import pathlib,tempfile,time,unittest
from PySide6.QtWidgets import QApplication
from shorekeeper_pet import pet as module
from shorekeeper_pet.config_io import save_atomic
from shorekeeper_pet.presets import load_defaults
from shorekeeper_pet.quota import quota_from_status,quota_items
from shorekeeper_pet.status_panel import StatusPanel
from shorekeeper_pet.telemetry import sanitize

ALL=dict(five_hour=True,seven_day=True,fable=True,other=True)

def data(**rates):return quota_from_status(sanitize(dict(session_id='x',rate_limits=rates),True))

class QuotaItemTests(unittest.TestCase):
    def test_fable_window_and_hidden_items(self):
        d=data(five_hour={'used_percentage':40},seven_day={'used_percentage':3},seven_day_fable={'used_percentage':84},spend_limit={'used_percentage':10})
        items=quota_items(d,ALL)
        self.assertEqual([(i['key'],i['remaining']) for i in items],[('five_hour',60),('seven_day',97),('fable',16),('spend_limit',90)])
        items=quota_items(d,dict(ALL,seven_day=False,other=False))
        self.assertEqual([i['key'] for i in items],['five_hour','fable'])

    def test_shown_item_without_data_stays_visible_as_pending(self):
        items=quota_items(data(five_hour={'used_percentage':40}),ALL)
        self.assertEqual([(i['key'],i['remaining']) for i in items],[('five_hour',60),('seven_day',None),('fable',None)])

class PanelWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=module.SETTINGS;module.SETTINGS=pathlib.Path(self.tmp.name)/'settings.json'
        cfg=load_defaults(module.ROOT);cfg['appearance']['audio_enabled']=False;save_atomic(module.SETTINGS,cfg)
        self.pet=module.Pet(offline=True);self.pet.timer.stop()
        self.pet.quota_data=data(five_hour={'used_percentage':40},seven_day={'used_percentage':3})
        self.pet.live_status=dict(state='idle',request=dict(state='done',started=time.time()-60,ended=time.time()))
    def tearDown(self):
        self.pet.voice.stop();self.pet.hide()
        if self.pet.preferences:self.pet.preferences.status_preview.timer.stop();self.pet.preferences.hide()
        self.pet.deleteLater();self.app.processEvents();module.SETTINGS=self.old;self.tmp.cleanup()

    def test_toggles_reach_the_pet_and_survive_reload(self):
        p=self.pet;p.update_layout();full=p.quota_rect.height()
        p.set_option('status_show_request',False);p.set_option('quota_show_fable',False)
        request,items=p.status_content()
        self.assertIsNone(request);self.assertEqual([i['key'] for i in items],['five_hour','seven_day'])
        self.assertLess(p.quota_rect.height(),full)
        self.assertFalse(module.load_settings()['appearance']['quota_show_fable'])

    def test_rings_and_bars_both_lay_out_and_paint(self):
        p=self.pet
        for style in ('bars','rings'):
            p.set_option('quota_style',style);self.assertGreater(p.quota_rect.width(),0);p.grab()
        for key in ('status_show_request','quota_show_five_hour','quota_show_seven_day','quota_show_fable','quota_show_other'):
            p.set_option(key,False)
        self.assertTrue(p.status_panel.empty());p.grab()

    def test_preview_follows_options(self):
        p=self.pet;p.open_preferences('status');preview=p.preferences.status_preview
        self.assertEqual(p.preferences.tabs.currentIndex(),p.preferences.tab_names['status'])
        p.preferences.controls['quota_style'].setCurrentIndex(p.preferences.controls['quota_style'].findData('rings'))
        self.assertEqual(p.options['quota_style'],'rings');preview.grab()
        preview.sample=True;self.assertEqual([i['key'] for i in preview.content()[1]],['five_hour','seven_day','fable'])

    def test_pill_counts_every_open_request(self):
        p=self.pet;now=time.time()
        a=dict(state='thinking',started=now-60,ended=0,title='A');b=dict(state='awaiting',started=now-30,ended=0,title='B')
        p.live_status=dict(requests=[a,b],latest=None);self.assertEqual(p.status_content()[0],'需求：2 个处理中')
        self.assertEqual(len(p.request_lines()),2);self.assertIn('等后台任务',p.request_lines()[1])
        p.live_status=dict(requests=[a],latest=dict(state='done',started=now-90,ended=now-5,title='B'))
        self.assertTrue(p.status_content()[0].startswith('需求：处理中'))
        p.live_status=dict(requests=[],latest=dict(state='done',started=now-90,ended=now-5,title='B'))
        self.assertTrue(p.status_content()[0].startswith('需求：已完成'))

    def test_context_menu_keeps_settings_in_one_entry(self):
        menu=self.pet.context_menu()
        names=[a.text() for a in menu.actions() if a.text() and a.isEnabled()]
        self.assertEqual(names,['摸摸头','递一杯奶茶','Claude 与额度','设置…','暂时收起（托盘恢复）','退出守岸人'])
        claude=next(a.menu() for a in menu.actions() if a.text()=='Claude 与额度')
        self.assertEqual([a.text() for a in claude.actions()],['打开 Claude','打开官方用量页面','刷新额度'])
        menu.deleteLater()

    def test_one_settings_window_holds_every_page(self):
        p=self.pet;p.open_preferences();prefs=p.preferences
        self.assertEqual([prefs.tabs.tabText(i) for i in range(prefs.tabs.count())],
                         ['常规','交互工作室','字体与大小','声音','底部状态条','Claude 额度与提醒','保存与迁移','版本与更新'])
        self.assertIsNone(prefs.studio)
        p.open_bindings();self.assertIs(p.binding_editor,prefs.studio);self.assertIs(prefs.studio.parent(),prefs.studio_page)
        self.assertEqual(prefs.tabs.currentIndex(),prefs.tab_names['studio'])
        prefs.quiet.setChecked(True);self.assertTrue(p.quiet);self.assertTrue(module.load_settings()['quiet'])
        prefs.on_top.setChecked(False);self.assertFalse(p.on_top)
        prefs.studio.timer.stop()

    def test_ring_size_follows_font(self):
        options=dict(self.pet.options,quota_style='rings')
        items=quota_items(self.pet.quota_data,ALL)
        small=StatusPanel(dict(options,quota_font_size=12),None,items);large=StatusPanel(dict(options,quota_font_size=30),None,items)
        self.assertGreater(large.width,small.width);self.assertGreater(large.height,small.height)

if __name__=='__main__':unittest.main()
