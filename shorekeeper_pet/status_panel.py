"""The pet's bottom status: an optional request pill above one merged quota box.

Everything is measured and painted in base units; callers scale the painter.
The same code draws the pet and the live preview on the settings page.
"""
from PySide6.QtCore import Qt,QRectF,QTimer
from PySide6.QtGui import QColor,QPen,QFont,QFontMetrics,QPainter
from PySide6.QtWidgets import QWidget
from .appearance import font

STYLES=(('bars','条形：每项下方一条余量条'),('rings','圆环：标签在左，环中显示剩余百分比'))
CALM=dict(line='#6788b4',fill='#e1f3ff',ink='#4f72a2',track='#b6d3ed',bar='#6e9fd0')
LOW=dict(ink='#744d13',track='#f0d9b5',bar='#d99032')
MISSING='#d6dde8'
GAP=5
# Ring layout: label to the left of each ring, rings sized to fill the box.
PAD,PAD_V,COL_GAP,ROW_GAP,RING_GAP,ONE_ROW=12,8,10,8,6,270

def request_palette(text):
    if '出错' in text: return dict(line='#a67125',fill='#fff4de',ink='#744d13')
    if '处理中' in text: return dict(line='#6788b4',fill='#eef3ff',ink='#3e5f92')  # also "N 个处理中"
    return dict(line='#5f9a6e',fill='#e6f6ea',ink='#356b45')

def value_text(item):
    if item['remaining'] is None: return '待同步'
    return f"{item['remaining']:g}%"+(' *' if item['stale'] else '')

def fitted(base,text,width):
    """Shrink a font so a single line fits; never wrap or elide numbers."""
    result=QFont(base); measured=QFontMetrics(result).horizontalAdvance(text)
    if measured>width>0: result.setPixelSize(max(7,int(result.pixelSize()*width/measured)))
    return result

class StatusPanel:
    def __init__(self,options,request=None,items=()):
        self.style=options.get('quota_style','bars'); self.request=request; self.items=list(items)
        self.font=font(options['font_family'],options['quota_font_size'])
        fm=QFontMetrics(self.font); self.line=fm.height()
        self.pill_height=max(31,self.line+10) if request else 0
        # The pill may make the panel wider; the quota box must use that room.
        floor=min(260,fm.horizontalAdvance(request)+28) if request else 0
        self.width,self.box_height=self.measure_box(floor)
        self.width=max(self.width,floor)
        self.height=self.pill_height+(GAP if request and self.items else 0)+self.box_height

    def empty(self): return not self.request and not self.items

    def measure_box(self,floor=0):
        if not self.items: return (146 if self.request else 0),0
        fm=QFontMetrics(self.font)
        if self.style=='rings': return self.measure_rings(floor)
        need=max(fm.horizontalAdvance(item['label'])+fm.horizontalAdvance(value_text(item)) for item in self.items)+46
        self.row_height=self.line+13
        return min(260,max(160,need)),10+len(self.items)*self.row_height+(len(self.items)-1)*6+8

    def measure_rings(self,floor):
        """One row when it fits, else two columns; rings grow into any spare
        width (up to a limit) instead of leaving it empty."""
        fm=QFontMetrics(self.font); count=len(self.items)
        self.label_width=max(fm.horizontalAdvance(item['short']) for item in self.items)
        smallest,largest=round(max(46,self.line*2.6)),round(max(60,self.line*3.6))
        def span(columns): return PAD*2+columns*(self.label_width+RING_GAP+smallest)+(columns-1)*COL_GAP
        self.columns=count if span(count)<=ONE_ROW else min(2,count)
        rows=-(-count//self.columns)
        width=max(floor,146,span(self.columns))
        self.column=(width-PAD*2-(self.columns-1)*COL_GAP)/self.columns
        self.ring=max(smallest,min(largest,self.column-self.label_width-RING_GAP))
        return width,PAD_V*2+rows*self.ring+(rows-1)*ROW_GAP

    def paint(self,p):
        y=0
        if self.request:
            rect=QRectF(0,0,self.width,self.pill_height); colors=request_palette(self.request)
            self.shell(p,rect,colors['line'],colors['fill'])
            p.setFont(fitted(self.font,self.request,rect.width()-18)); p.setPen(QColor(colors['ink']))
            p.drawText(rect.adjusted(9,1,-9,-1),Qt.AlignmentFlag.AlignCenter,self.request)
            y=self.pill_height+GAP
        if not self.items: return
        box=QRectF(0,y,self.width,self.box_height); self.shell(p,box,CALM['line'],CALM['fill'])
        (self.paint_rings if self.style=='rings' else self.paint_bars)(p,box)

    def shell(self,p,rect,line,fill):
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(20,43,77,50)); p.drawRoundedRect(rect.translated(0,2),11,11)
        p.setPen(QPen(QColor(line),1.6)); p.setBrush(QColor(fill)); p.drawRoundedRect(rect,11,11)

    def palette(self,item):
        return dict(CALM,**LOW) if item['remaining'] is not None and item['remaining']<20 else CALM

    def paint_bars(self,p,box):
        top=box.top()+10
        for item in self.items:
            colors=self.palette(item); row=QRectF(box.left()+12,top,box.width()-24,self.row_height)
            value=value_text(item); value_font=QFont(self.font); value_font.setBold(item['remaining'] is not None)
            value_width=QFontMetrics(value_font).horizontalAdvance(value)
            p.setFont(fitted(self.font,item['label'],row.width()-value_width-10)); p.setPen(QColor(colors['ink']))
            text=QRectF(row.left(),row.top(),row.width(),self.line)
            p.drawText(text,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,item['label'])
            p.setFont(value_font); p.drawText(text,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,value)
            track=QRectF(row.left(),row.bottom()-8,row.width(),5); p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(MISSING if item['remaining'] is None else colors['track'])); p.drawRoundedRect(track,2.5,2.5)
            if item['remaining']:
                p.setBrush(QColor(colors['bar'])); p.drawRoundedRect(QRectF(track.left(),track.top(),track.width()*max(0,min(100,item['remaining']))/100,track.height()),2.5,2.5)
            top+=self.row_height+6

    def ring_slots(self,box):
        """(left, top, label width) per item. A single row spreads its groups
        evenly; a grid keeps columns aligned and centres a short last row."""
        fm=QFontMetrics(self.font); count=len(self.items)
        if self.columns==count:
            widths=[fm.horizontalAdvance(item['short']) for item in self.items]
            spare=(box.width()-sum(w+RING_GAP+self.ring for w in widths))/(count+1)
            left=box.left()+spare; slots=[]
            for width in widths:
                slots.append((left,box.top()+PAD_V,width)); left+=width+RING_GAP+self.ring+spare
            return slots
        group=self.label_width+RING_GAP+self.ring; slots=[]
        for index in range(count):
            row,column=divmod(index,self.columns)
            in_row=min(self.columns,count-row*self.columns)
            left=box.left()+PAD+(self.columns-in_row)*(self.column+COL_GAP)/2+column*(self.column+COL_GAP)+(self.column-group)/2
            slots.append((left,box.top()+PAD_V+row*(self.ring+ROW_GAP),self.label_width))
        return slots

    def paint_rings(self,p,box):
        stroke=max(5,self.ring*.12)
        for item,(left,top,label_width) in zip(self.items,self.ring_slots(box)):
            colors=self.palette(item)
            p.setFont(fitted(self.font,item['short'],label_width)); p.setPen(QColor(colors['ink']))
            p.drawText(QRectF(left,top,label_width,self.ring),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,item['short'])
            ring=QRectF(left+label_width+RING_GAP+stroke/2,top+stroke/2,self.ring-stroke,self.ring-stroke)
            pen=QPen(QColor(MISSING if item['remaining'] is None else colors['track']),stroke); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(ring)
            if item['remaining']:
                pen.setColor(QColor(colors['bar'])); p.setPen(pen)
                # Clockwise from twelve o'clock; Qt angles are 1/16° counter-clockwise.
                p.drawArc(ring,90*16,-round(max(0,min(100,item['remaining']))*3.6*16))
            number='--' if item['remaining'] is None else f"{round(item['remaining'])}%"+('*' if item['stale'] else '')
            inner=self.ring-2*stroke-4; value=QFont(self.font); value.setBold(True)
            value.setPixelSize(max(8,round(min(self.font.pixelSize()*1.4,inner*.46))))
            p.setFont(fitted(value,number,inner)); p.setPen(QColor(colors['ink']))
            p.drawText(ring,Qt.AlignmentFlag.AlignCenter,number)

SAMPLE_REQUEST='需求：已完成 · 14:10'
SAMPLE_ITEMS=[dict(key='five_hour',label='5 小时',short='5 小时',remaining=58,stale=False),
              dict(key='seven_day',label='每周',short='每周',remaining=97,stale=False),
              dict(key='fable',label='Fable 每周',short='Fable',remaining=16,stale=False)]

class StatusPreview(QWidget):
    """Settings-page preview of exactly what the pet will draw."""
    def __init__(self,pet,parent=None):
        super().__init__(parent); self.pet=pet; self.sample=False; self.setMinimumHeight(150)
        # Live data (request time, quota sync) changes on its own.
        self.timer=QTimer(self); self.timer.timeout.connect(lambda:self.isVisible() and self.refresh()); self.timer.start(1000)
    def scale(self):
        options=self.pet.options; return options['pet_size']/230*options['quota_scale']
    def refresh(self):
        panel=StatusPanel(self.pet.options,*self.content())
        self.setMinimumHeight(max(120,min(420,round(panel.height*self.scale())+28))); self.update()
    def content(self):
        if not self.sample: return self.pet.status_content()
        options=self.pet.options; show=self.pet.quota_visibility()
        return (SAMPLE_REQUEST if options['status_show_request'] else None),[item for item in SAMPLE_ITEMS if show[item['key']]]
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor('#d4e0ee'),1)); p.setBrush(QColor('#e9eff8')); p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),10,10)
        request,items=self.content(); panel=StatusPanel(self.pet.options,request,items)
        if panel.empty():
            p.setPen(QColor('#7b8ba2')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'状态条已全部隐藏'); return
        scale=min(self.scale(),(self.width()-24)/panel.width,(self.height()-24)/panel.height)
        p.translate((self.width()-panel.width*scale)/2,(self.height()-panel.height*scale)/2); p.scale(scale,scale)
        panel.paint(p); p.end()
