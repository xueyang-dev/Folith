"""Qt-rendered design studies, not production widgets. Run with QT_QPA_PLATFORM=offscreen."""
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication([])
out = Path(__file__).resolve().parent
scenes = [
    ("01  Untranslated", "Segment 12 · Translation completeness", "The evidence suggests a gradual change.", "No target translation", "No supporting evidence for this item."),
    ("02  Terminology conflict", "Segment 36 · Terminology", "The model predicts long-term outcomes.", "模型预测长期结果。", "Frozen term: model → 模型\nAffected segments: 36, 42, 57"),
    ("03  Automatic repair", "Segment 48 · Automatic repair", "A controlled trial was conducted.", "Before: 开展了一项试验。\nAfter: 开展了一项对照试验。\nCurrent: 开展了一项对照试验。", "Actor: rule · committed\nCandidate: c-248 · verified"),
    ("04  Empty evidence", "Segment 72 · Style suggestion", "These results remain consistent.", "这些结果保持一致。", "No supporting evidence for this item."),
]

def rect(p, x, y, w, h, c, line=None):
    p.fillRect(QRectF(x,y,w,h), QColor(c))
    if line:
        p.setPen(QColor(line)); p.drawRect(QRectF(x+.5,y+.5,w-1,h-1))

def text(p, x, y, w, h, s, size=11, color='#26324a', bold=False):
    f=QFont('Arial', size); f.setBold(bold); p.setFont(f); p.setPen(QColor(color));
    p.drawText(QRectF(x,y,w,h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, s)

def draw(p, ox, oy, direction, scene):
    p.save(); p.translate(ox,oy)
    name,title,source,target,evidence=scene
    rect(p,0,0,700,445,'#f2f4f7','#cbd2dc')
    text(p,12,8,676,20,name,11,'#52627a',True)
    rect(p,0,31,700,40,'#e8ecf2')
    text(p,14,42,660,20,'Needs attention  1     Suggestions  2     Automatic  1       Independent review: complete',9,'#34465f',True)
    if direction=='a':
        q,c,e=155,370,175; qbg,cbg,ebg='#e8ecf2','#ffffff','#eff2f6'; margin=12
    elif direction=='b':
        q,c,e=125,435,140; qbg,cbg,ebg='#f1f2f2','#fffefb','#f1f2f2'; margin=22
    else:
        q,c,e=150,380,170; qbg,cbg,ebg='#eaf0f6','#ffffff','#f5f7fa'; margin=17
    rect(p,0,71,q,374,qbg); rect(p,q,71,c,374,cbg); rect(p,q+c,71,e,374,ebg)
    rect(p,q-1,71,1,374,'#ced5df'); rect(p,q+c,71,1,374,'#ced5df')
    for x,w,label in [(0,q,'DECISION QUEUE'),(q,c,'TRANSLATION'),(q+c,e,'EVIDENCE')]:
        text(p,x+10,82,w-18,20,label,9,'#52627a',True)
        rect(p,x,106,w,1,'#d9dfe8')
    rows=['Attention  ·  1','Segment 12\nUntranslated','Suggestions  ·  2','Segment 36\nTerminology','Segment 72\nStyle','Automatic  ·  1','Segment 48\nRepair audit']
    y=118
    for idx,row in enumerate(rows):
        if idx==({'01':1, '02':3, '03':6, '04':4}.get(name[:2], -1)): rect(p,4,y-3,q-8,36,'#d6e6fb')
        text(p,10,y,q-18,34,row,9,'#27364d',idx in (0,2,5)); y+=39 if idx in (1,3,4,6) else 26
    x=q+margin; w=c-margin*2
    text(p,x,121,w,42,title,13,'#17253a',True)
    text(p,x,165,w,28,'Review the translation before making a decision.',9,'#52627a')
    if direction=='a':
        # A compact, side-by-side comparison, like an IDE diff.
        rect(p,x,197,w,1,'#cbd5e1')
        rect(p,x+w/2,197,1,160,'#d9dfe8')
        text(p,x,205,w/2-12,18,'SOURCE',9,'#64758d',True)
        text(p,x,225,w/2-12,100,source,10)
        text(p,x+w/2+10,205,w/2-10,18,'TARGET',9,'#64758d',True)
        text(p,x+w/2+10,225,w/2-10,140,target,9 if name.startswith('03') else 10)
    else:
        # B reads like a page; C groups the comparison into a quiet inset.
        if direction=='c': rect(p,x-6,194,w+12,176,'#f6f8fb','#e1e7ef')
        text(p,x,205,w,18,'SOURCE',9,'#64758d',True)
        text(p,x,225,w,48,source,11)
        rect(p,x,276,w,1,'#d9dfe8')
        text(p,x,287,w,18,'TARGET',9,'#64758d',True)
        text(p,x,307,w,68,target,9 if name.startswith('03') else 11, '#65748a' if target.startswith('No target') else '#26324a')
    text(p,q+c+10,122,e-20,110,evidence,9,'#43536a')
    rect(p,q,382,c,63,'#eef2f7'); rect(p,q,382,c,1,'#cdd6e2')
    text(p,q+12,394,c-24,16,'Optional note                         N  Next   ·   E  Edit',8,'#64758d')

    if name.startswith('03'):
        text(p,q+12,419,c-24,16,'Revoke automatic handling',8,'#34465f',True)
    else:
        rect(p,q+12,415,90,22,'#2d5cbd'); text(p,q+20,419,80,16,'Edit translation',8,'#ffffff',True)
        text(p,q+113,419,c-130,16,'Keep current     Confirm resolved',8,'#34465f')
    p.restore()

for direction in 'abc':
    image=QImage(1440,930,QImage.Format.Format_ARGB32); image.fill(QColor('#ffffff'))
    painter=QPainter(image); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i,scene in enumerate(scenes): draw(painter, 10+(i%2)*720, 10+(i//2)*455, direction, scene)
    painter.end(); image.save(str(out/f'direction-{direction}.png'))
