import copy,datetime,json,pathlib,shutil,threading
from PySide6.QtCore import Qt,QObject,Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QListWidget,QListWidgetItem,QStackedWidget,QScrollArea,QComboBox,QSpinBox,QCheckBox,QPushButton,QLineEdit,QFileDialog,QMessageBox,QGroupBox
from .appearance import stylesheet,load_fonts
from .config_io import export_bundle,import_bundle,save_atomic
from .paths import ROOT,CLAUDE_HOME
from .sizing import SizeControl
from .paths import VERSION,AUDIO_SESSION_NAME
from .presentation_size import PresentationControl
from .quota import quota_label,source_lines
from .status_panel import StatusPreview,STYLES

BACKGROUND_CHOICES=(('silent','静音（推荐）：只提醒你亲自发出的需求'),('animate','仅动画：后台任务切换表情，但不说话、不弹完成'),('full','完整提醒：后台任务也播报开始与完成'))

class Jobs(QObject):
    finished=Signal(str); failed=Signal(str)

class NavTabs(QWidget):
    """Left-hand page list; too many pages for a tab bar to stay readable."""
    def __init__(self):
        super().__init__(); row=QHBoxLayout(self); row.setContentsMargins(0,0,0,0); row.setSpacing(12)
        self.nav=QListWidget(); self.nav.setObjectName('settingsNav'); self.nav.setFixedWidth(176); row.addWidget(self.nav)
        self.stack=QStackedWidget(); row.addWidget(self.stack,1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
    def addTab(self,widget,title):
        self.stack.addWidget(widget); self.nav.addItem(QListWidgetItem(title))
        if self.nav.currentRow()<0: self.nav.setCurrentRow(0)
        return self.stack.count()-1
    def count(self): return self.stack.count()
    def currentIndex(self): return self.stack.currentIndex()
    def setCurrentIndex(self,index): self.nav.setCurrentRow(index)
    def tabText(self,index): return self.nav.item(index).text()
    def widget(self,index): return self.stack.widget(index)

class Preferences(QDialog):
    def __init__(self,pet):
        super().__init__(None); self.pet=pet; self.loading=True; self.controls={}; self.buttons=[]; self.tab_names={}; self.studio=None
        self.setWindowTitle('守岸人 · 设置'); self.setWindowIcon(pet.icon); self.resize(1180,860); self.setMinimumSize(900,560)
        layout=QVBoxLayout(self); layout.setContentsMargins(20,18,20,18)
        title=QLabel('把陪伴调整成你喜欢的样子'); title.setObjectName('settingsTitle'); layout.addWidget(title)
        self.tabs=NavTabs(); layout.addWidget(self.tabs,1)
        general=self.tab('常规','general')
        self.pack=QComboBox(); self.pack.addItem('163 · 小小守岸人','163'); self.pack.addItem('GB0227 · 近景表情','GB0227')
        self.pack.currentIndexChanged.connect(lambda:self.loading or pet.set_pack(self.pack.currentData())); general.addRow('表情风格',self.pack)
        self.on_top=QCheckBox('保持在其他窗口上方'); self.on_top.toggled.connect(lambda value:self.loading or pet.set_top(value)); general.addRow(self.on_top)
        self.quiet=QCheckBox('精简气泡：进度类状态不弹默认气泡'); self.quiet.toggled.connect(lambda value:self.loading or pet.set_quiet(value)); general.addRow(self.quiet)
        note=QLabel('精简气泡只影响文字气泡，不影响语音。开启后，思考、查阅、编辑、执行、待机等进度状态不再弹出默认台词；完成、出错、等你回应、摸头等互动，以及设为“自定义文字”或“音频+字幕”的气泡照常显示。想关掉声音请到“声音”页。'); note.setWordWrap(True); general.addRow(note)
        self.notes=QCheckBox('在进度气泡里显示 Claude 已公开的进度文字'); self.notes.toggled.connect(lambda value:self.loading or pet.set_notes(value)); general.addRow(self.notes)
        note=QLabel('单击摸头 · 拖动移动 · 双击互动 · Ctrl + 滚轮缩放 · 右键打开菜单。'); note.setWordWrap(True); general.addRow(note)
        self.studio_page=QWidget(); QVBoxLayout(self.studio_page).setContentsMargins(0,0,0,0)
        self.tab_names['studio']=self.tabs.addTab(self.studio_page,'交互工作室')
        visual=self.tab('字体与大小','visual')
        self.size_control=SizeControl(pet); visual.addRow('整体缩放 · 实时预览',self.size_control)
        names=list(dict.fromkeys(pet.font_families+['Microsoft YaHei','Segoe UI']+QFontDatabase.families()))
        visual.addRow('面板与额度字体',self.combo('font_family',names))
        visual.addRow('默认气泡字体',self.combo('bubble_font_family',names))
        visual.addRow('气泡文字',self.spin('bubble_font_size',12,40,' px'))
        visual.addRow('设置面板文字',self.spin('ui_font_size',13,28,' px'))
        self.presentation_control=PresentationControl(pet); visual.addRow('气泡与配额 · 实时预览',self.presentation_control)
        visual.addRow('角色基础大小（100%）',self.spin('pet_size',140,420,' px'))
        add=QPushButton('添加本地字体文件…'); add.clicked.connect(self.add_font); visual.addRow(add)
        note=QLabel('已内置站酷快乐体与霞鹜文楷，随迁移包携带，无需安装到系统。单个状态也可以在“交互工作室”单独设置字体与字号。'); note.setWordWrap(True); visual.addRow(note)
        voice=self.tab('声音','voice')
        enabled=QCheckBox('开启本地语音提醒'); enabled.toggled.connect(lambda v:self.change('audio_enabled',v)); self.controls['audio_enabled']=enabled; voice.addRow(enabled)
        voice.addRow('音量',self.spin('volume',0,100,' %'))
        voice.addRow('同一状态最短播报间隔',self.spin('audio_cooldown',0,300,' 秒'))
        row,self.audio_dir=self.path_control('audio_directory','选择音频目录',directory=True); voice.addRow('音频目录',row)
        note=QLabel('自动播报会先说完整句，GIF 切换不会截断声音。多会话完成提醒依次播放；忙时其他交互保留一条待播提醒，优先任务通知；待机／悬停不插队。思考默认每轮只播一次，轮次在实际播放时记账。主动试听或停止声音可以中断。'); note.setWordWrap(True); voice.addRow(note)
        stop=QPushButton('停止当前声音'); stop.clicked.connect(pet.voice.stop); voice.addRow(stop)
        status=self.tab('底部状态条','status')
        self.status_preview=StatusPreview(pet); status.addRow(self.status_preview)
        sample=QCheckBox('用示例数据预览（含 Fable 与低余量配色，不影响桌宠）'); sample.toggled.connect(self.preview_sample); status.addRow(sample)
        style=QComboBox()
        for value,label in STYLES: style.addItem(label,value)
        style.currentIndexChanged.connect(lambda:self.change('quota_style',style.currentData())); self.controls['quota_style']=style; status.addRow('额度显示方式',style)
        status.addRow(self.check('status_show_request','显示“需求完成条”（你亲自发送的最近一条消息：处理中／已完成／出错）'))
        shown=self.group(status,'在额度框中显示')
        for key,label in (('quota_show_five_hour','5 小时额度'),('quota_show_seven_day','每周额度'),('quota_show_fable','Fable 每周额度'),('quota_show_other','其他模型专属或网关支出额度（有数据时）')):
            shown.addRow(self.check(key,label))
        status.addRow('额度文字大小',self.spin('quota_font_size',12,32,' px'))
        note=QLabel('所有改动立即作用到桌宠，上方预览与桌宠底部一致。勾选的额度没有数据时显示“待同步”。Fable 额度目前只有 Claude 返回 Fable 专属窗口时才有数值：桌面版用量记录与官方状态栏现在都只提供 5 小时和每周。状态条整体大小可在“字体与大小”页或拖动桌宠额度框左右边缘调整。'); note.setWordWrap(True); status.addRow(note)
        connect=self.tab('Claude 额度与提醒','connect')
        quota=self.group(connect,'额度同步')
        note=QLabel('使用 Claude 桌面版时无需任何设置：桌宠自动读取桌面版写在本机的用量记录（约每 15 分钟更新一次）。只用终端 Claude Code 时，再勾选下方的状态栏连接。两个来源都有数据时显示较新的一个。'); note.setWordWrap(True); quota.addRow(note)
        self.quota_status=QLabel(); self.quota_status.setWordWrap(True); self.quota_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); quota.addRow(self.quota_status)
        link=QCheckBox('终端 Claude Code：安装任务事件与额度状态栏（可选）')
        link.toggled.connect(lambda value:self.change('claude_link_enabled',value)); self.controls['claude_link_enabled']=link; quota.addRow(link)
        note=QLabel('为当前用户写入 ~/.claude/settings.json。已有 hooks 和自定义状态栏会保留；已有状态栏时仅连接任务事件。取消勾选即移除本桌宠的连接。勾选后请重新打开终端会话。'); note.setWordWrap(True); quota.addRow(note)
        row=QHBoxLayout(); reconnect=QPushButton('立即刷新额度'); reconnect.clicked.connect(pet.refresh_quota); row.addWidget(reconnect)
        usage=QPushButton('打开官方用量页面'); usage.clicked.connect(pet.open_usage); row.addWidget(usage); quota.addRow(row)
        reminders=self.group(connect,'后台任务提醒')
        background=QComboBox()
        for value,label in BACKGROUND_CHOICES: background.addItem(label,value)
        background.currentIndexChanged.connect(lambda:self.change('background_turns',background.currentData())); self.controls['background_turns']=background; reminders.addRow('Claude 自己发起的轮次',background)
        note=QLabel('后台命令或子任务完成后的回报、定时任务、Monitor 事件，以及由其他会话新建的会话，都由 Claude 自己发起。静音时它们不会再反复触发“开始／完成”的语音和动画；底部“需求”一栏只跟踪你亲自发送的最近一条消息：处理中、已完成或出错。'); note.setWordWrap(True); reminders.addRow(note)
        sessions=self.group(connect,'会话与启动')
        self.sessions=QComboBox(); self.sessions.currentIndexChanged.connect(self.select_thread); sessions.addRow('跟随的会话',self.sessions)
        note=QLabel('推荐自动跟随：优先显示正在处理你需求的会话，空闲时停在你最近提问的会话。会话名称与 Claude 侧栏一致。'); note.setWordWrap(True); sessions.addRow(note)
        launch=QCheckBox('随 Claude 启动桌宠'); launch.toggled.connect(lambda value:self.change('launch_with_claude',value)); self.controls['launch_with_claude']=launch; sessions.addRow(launch)
        note=QLabel('勾选后，登录 Windows 时会在后台等待 Claude 打开，再启动桌宠。取消勾选会移除本机的启动联动。'); note.setWordWrap(True); sessions.addRow(note)
        row,self.claude_home=self.path_control('claude_home','选择 Claude 数据目录',True); sessions.addRow('数据目录',row)
        row,self.claude_exe=self.path_control('claude_executable','选择 claude.exe',False); sessions.addRow('Claude 程序',row)
        note=QLabel('数据目录留空使用 CLAUDE_CONFIG_DIR 或当前用户的 .claude。当前 Claude Code 终端与桌面版的 Code 会话都已支持；普通聊天和 Cowork 尚未适配。'); note.setWordWrap(True); sessions.addRow(note)
        self.connection_status=QLabel(); self.connection_status.setWordWrap(True); sessions.addRow(self.connection_status)
        transfer=self.tab('保存与迁移','transfer')
        note=QLabel('修改会自动保存。你也可以立即保存快照，或把 GIF、字体、已绑定音频和配置一起打包。便携包还包含 Windows 运行程序。'); note.setWordWrap(True); transfer.addRow(note)
        self.add_button(transfer,'立即保存全部配置',self.save_now)
        self.add_button(transfer,'保存一份配置快照（JSON）',self.snapshot)
        self.add_button(transfer,'导出配置与素材包（ZIP）',lambda:self.export(False))
        self.add_button(transfer,'导出 Windows 便携完整包（ZIP）',lambda:self.export(True))
        self.add_button(transfer,'导入配置与素材包…',self.import_file)
        tip=QLabel('迁移包会重置当前会话和本机连接路径，新电脑自动寻找当地已登录的 Claude。不会包含账户凭据、聊天记录或额度缓存。'); tip.setWordWrap(True); transfer.addRow(tip)
        updates=self.tab('版本与更新','updates')
        updates.addRow(QLabel(AUDIO_SESSION_NAME+'  ·  当前版本 v'+VERSION))
        automatic=QCheckBox('每次启动时检查 GitHub 更新'); automatic.toggled.connect(lambda value:self.change('check_updates_on_start',value)); self.controls['check_updates_on_start']=automatic; updates.addRow(automatic)
        check=QPushButton('立即检查更新'); check.clicked.connect(lambda:pet.updates.check(True)); check.setEnabled(not pet.updates.busy); pet.updates.busy_changed.connect(lambda busy:check.setEnabled(not busy)); updates.addRow(check)
        self.update_status=QLabel(pet.updates.status); self.update_status.setWordWrap(True); pet.updates.status_changed.connect(self.update_status.setText); updates.addRow(self.update_status)
        restore=QPushButton('恢复已忽略版本的提醒'); restore.clicked.connect(lambda:self.change('ignored_update_version','')); updates.addRow(restore)
        tip=QLabel('有新版时显示提示，检查失败不会打断使用。更新页面中请选择适用于当前版本的程序补丁；使用完整包时，请解压到新目录，再导入自己的配置与素材。'); tip.setWordWrap(True); updates.addRow(tip)
        self.tabs.stack.currentChanged.connect(self.page_changed)
        self.status=QLabel('设置自动保存'); self.status.setWordWrap(True); layout.addWidget(self.status)
        close=QPushButton('完成'); close.clicked.connect(self.hide); layout.addWidget(close)
        self.jobs=Jobs(); self.jobs.finished.connect(self.job_finished); self.jobs.failed.connect(self.job_failed)
        self.refresh_controls(); self.loading=False; self.apply_style()
    def ensure_studio(self):
        # The studio decodes every GIF thumbnail; build it only when first opened.
        if not self.studio:
            self.studio=self.pet.make_studio(); self.studio_page.layout().addWidget(self.studio)
        return self.studio
    def page_changed(self,index):
        if index==self.tab_names.get('studio'): self.ensure_studio()
    def group(self,form,title):
        box=QGroupBox(title); inner=QFormLayout(box); inner.setSpacing(12); inner.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); inner.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow); form.addRow(box); return inner
    def check(self,key,label):
        control=QCheckBox(label); control.toggled.connect(lambda value:self.change(key,value)); self.controls[key]=control; return control
    def preview_sample(self,value):
        self.status_preview.sample=value; self.status_preview.refresh()
    def show_tab(self,name):
        if name in self.tab_names: self.tabs.setCurrentIndex(self.tab_names[name])
        if name=='studio': self.ensure_studio()
    def refresh_quota_status(self):
        data=self.pet.quota_data
        self.quota_status.setText('当前显示：'+quota_label(data)+'\n'+'\n'.join(source_lines(data)))
    def tab(self,title,name):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); body=QWidget(); body.setObjectName('settingsBody'); form=QFormLayout(body); form.setContentsMargins(16,18,16,18); form.setSpacing(16); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow); scroll.setWidget(body); self.tab_names[name]=self.tabs.addTab(scroll,title); return form
    def combo(self,key,names):
        control=QComboBox()
        for name in names: control.addItem(name,name)
        control.currentIndexChanged.connect(lambda:self.change(key,control.currentData())); self.controls[key]=control; return control
    def spin(self,key,low,high,suffix):
        control=QSpinBox(); control.setRange(low,high); control.setSuffix(suffix); control.setKeyboardTracking(False); control.valueChanged.connect(lambda value:self.change(key,value)); self.controls[key]=control; return control
    def path_control(self,key,title,directory):
        row=QHBoxLayout(); control=QLineEdit(); control.setPlaceholderText('留空自动检测' if key.startswith('claude') else 'audio'); self.controls[key]=control; control.editingFinished.connect(lambda:self.change(key,control.text().strip())); row.addWidget(control,1)
        button=QPushButton('浏览…')
        def choose():
            value=QFileDialog.getExistingDirectory(self,title,control.text()) if directory else QFileDialog.getOpenFileName(self,title,control.text(),'程序 (*.exe)')[0]
            if value: control.setText(value); self.change(key,value)
        button.clicked.connect(choose); row.addWidget(button); return row,control
    def add_button(self,form,label,callback):
        button=QPushButton(label); button.clicked.connect(callback); form.addRow(button); self.buttons.append(button)
    def refresh_controls(self):
        self.loading=True
        for key,control in self.controls.items():
            value=self.pet.options[key]
            if isinstance(control,QComboBox):
                idx=control.findData(value)
                if idx<0: control.addItem(value,value); idx=control.count()-1
                control.setCurrentIndex(idx)
            elif isinstance(control,QSpinBox): control.setValue(round(value))
            elif isinstance(control,QCheckBox): control.setChecked(bool(value))
            else: control.setText(value)
        self.pack.setCurrentIndex(max(0,self.pack.findData(self.pet.pack))); self.on_top.setChecked(self.pet.on_top)
        self.quiet.setChecked(self.pet.quiet); self.notes.setChecked(self.pet.show_notes)
        self.refresh_threads(); self.size_control.refresh(); self.refresh_quota_status(); self.status_preview.refresh()
        self.connection_status.setText('当前数据目录：'+str(self.pet.monitor.home)+'\n'+getattr(self.pet,'connection_note','')); self.loading=False
    def refresh_threads(self):
        selected=self.pet.monitor.selected; self.sessions.blockSignals(True); self.sessions.clear(); self.sessions.addItem('自动跟随最近活动（推荐）','auto')
        for row in self.pet.thread_list: self.sessions.addItem(row['title'][:40],row['id'])
        if self.sessions.findData(selected)<0: self.sessions.addItem('固定会话（目前未在列表中）',selected)
        self.sessions.setCurrentIndex(self.sessions.findData(selected)); self.sessions.blockSignals(False)
    def select_thread(self):
        if self.loading: return
        ok=self.pet.select_thread(self.sessions.currentData() or 'auto'); self.status.setText('跟随设置已保存' if ok else '跟随设置保存失败')
    def change(self,key,value):
        if self.loading: return
        try:
            ok=self.pet.set_option(key,value); self.status_preview.refresh(); self.status.setText((getattr(self.pet,'connection_note','') if key=='claude_link_enabled' else '') or ('已自动保存' if ok else '保存失败，请检查目录权限。'))
        except (OSError,ValueError,RuntimeError) as error:
            self.refresh_controls(); self.status.setText('设置未更改：'+str(error))
    def apply_style(self):
        self.setStyleSheet(stylesheet(self.pet.options)+'QLabel#settingsTitle{font-size:25px;font-weight:600;color:#2e5280;}'
                           'QListWidget#settingsNav::item{padding:11px 12px;margin:2px 0;} QListWidget#settingsNav::item:selected{background:#cddffa;color:#244a82;font-weight:600;}')
        if self.studio: self.studio.apply_style()
    def save_now(self): self.status.setText('全部配置已保存' if self.pet.save_settings() else '保存失败')
    def snapshot(self):
        path,_=QFileDialog.getSaveFileName(self,'保存配置快照',str(ROOT/'exports'/('settings-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')),'JSON (*.json)')
        if not path: return
        try: save_atomic(path,self.pet.settings,backup=False); self.status.setText('快照已保存：'+path)
        except OSError as error: self.status.setText('保存失败：'+str(error))
    def export(self,portable):
        name=('Shorekeeper-Windows-' if portable else 'Shorekeeper-Profile-')+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip'
        path,_=QFileDialog.getSaveFileName(self,'导出完整便携包' if portable else '导出配置与素材',str(ROOT/'exports'/name),'ZIP (*.zip)')
        if not path: return
        self.pet.save_settings(); data=copy.deepcopy(self.pet.settings); self.set_busy(True); self.status.setText('正在打包，请稍候…')
        def work():
            try:
                warnings=export_bundle(path,data,ROOT,portable); self.jobs.finished.emit('已导出：'+path+('\n'+'\n'.join(warnings) if warnings else ''))
            except Exception as error: self.jobs.failed.emit('导出失败：'+str(error))
        threading.Thread(target=work,daemon=True).start()
    def set_busy(self,value):
        for button in self.buttons: button.setEnabled(not value)
    def job_finished(self,text): self.set_busy(False); self.status.setText(text)
    def job_failed(self,text): self.set_busy(False); self.status.setText(text)
    def import_file(self):
        path,_=QFileDialog.getOpenFileName(self,'导入配置',str(ROOT/'exports'),'守岸人配置 (*.zip *.json)')
        if not path: return
        try:
            self.pet.voice.stop()
            stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S'); save_atomic(ROOT/'backups'/('before-import-'+stamp+'.json'),self.pet.settings,backup=False)
            data=json.loads(pathlib.Path(path).read_text('utf8')) if path.lower().endswith('.json') else import_bundle(path,ROOT)
            if not isinstance(data,dict): raise ValueError('配置格式不正确')
            self.pet.apply_settings(data); self.refresh_controls(); self.status.setText('配置已导入并应用。原配置已保存在 backups。')
        except Exception as error: self.status.setText('导入失败：'+str(error))
    def add_font(self):
        path,_=QFileDialog.getOpenFileName(self,'添加本地字体','','字体 (*.ttf *.otf *.ttc)')
        if not path: return
        target=ROOT/'fonts'/pathlib.Path(path).name
        if pathlib.Path(path).resolve()!=target.resolve(): shutil.copy2(path,target)
        self.pet.font_families=load_fonts(ROOT)
        for key in ('font_family','bubble_font_family'):
            control=self.controls[key]
            for family in self.pet.font_families:
                if control.findData(family)<0: control.addItem(family,family)
        self.status.setText('字体已加入桌宠目录，可从字体列表选择。')
