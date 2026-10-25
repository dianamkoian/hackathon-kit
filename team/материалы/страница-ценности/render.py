from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from html import escape

OUT=Path(__file__).parent
S=2
W,H=1600,900
im=Image.new('RGB',(W*S,H*S),'#F4F2EC')
d=ImageDraw.Draw(im)
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
regular='/System/Library/Fonts/Supplemental/Arial.ttf'
bold='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
ink='#132C33'; muted='#56676B'; teal='#097467'; line='#D7DED9'

def box(x,y,w,h,fill,r=0,stroke=None):
    coords=[int(v*S) for v in (x,y,x+w,y+h)]
    if r:d.rounded_rectangle(coords,radius=r*S,fill=fill,outline=stroke,width=S)
    else:d.rectangle(coords,fill=fill,outline=stroke,width=S)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"'+(f' stroke="{stroke}"' if stroke else '')+'/>')

def text(x,y,t,size=24,color=ink,weight='normal'):
    f=ImageFont.truetype(bold if weight=='bold' else regular,size*S)
    bounds=d.textbbox((x*S,y*S),t,font=f,anchor='lt')
    assert bounds[2]<=W*S-20, (t,bounds)
    d.text((x*S,y*S),t,font=f,fill=color,anchor='lt')
    svg.append(f'<text x="{x}" y="{y}" dominant-baseline="hanging" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(t)}</text>')

def rule(x,y,w):box(x,y,w,1,line)

box(0,0,W,H,'#F4F2EC')
box(64,57,35,35,teal,10)
text(72,62,'?',25,'#FFFFFF','bold')
text(112,66,'ВТОРОЕ МНЕНИЕ',19,ink,'bold')
text(1167,68,'ПРОВЕРКА СООБЩЕНИЙ',14,muted)
rule(64,119,1472)
text(64,168,'Когда сообщение',61,ink,'bold')
text(64,242,'торопит, нужна пауза.',61,ink,'bold')
text(66,343,'Показываем, что насторожило,',29,muted)
text(66,384,'и предлагаем конкретный шаг',29,muted)
text(66,425,'для проверки.',29,muted)
box(64,501,640,75,'#E4EAE4',14)
text(85,518,'Перед переводом денег, передачей кода',22,ink)
text(85,547,'или переходом по подозрительной ссылке.',22,ink)

# An explicitly labelled editorial illustration, not an actual model result.
box(830,153,706,466,'#FFFFFF',24,line)
text(860,180,'КАК ЭТО ПОМОГАЕТ · ПРИМЕР',15,muted,'bold')
box(858,220,650,157,'#F4F5F1',14)
text(880,241,'Мам, это я. Телефон сломался.',25)
box(875,279,490,34,'#FFE8A4',5)
text(880,282,'Срочно переведи 15 000 руб.',27,ink,'bold')
box(875,321,520,33,'#FFE8A4',5)
text(880,323,'Не звони — потом объясню.',27,ink,'bold')
text(860,399,'Срочность  /  Деньги  /  Отказ от звонка',21,muted)
box(858,448,650,142,teal,15)
text(882,468,'СЛЕДУЮЩИЙ ШАГ',14,'#B9E6D9','bold')
text(882,499,'Позвоните на прежний номер.',28,'#FFFFFF','bold')
text(882,544,'Используйте контакт, который уже знаете.',20,'#DAF0E9')

rule(64,659,1472)
columns=[(64,'01','Понятны основания',['Признаки связаны с цитатами.','Можно увидеть, что вызвало сомнение.']),
         (576,'02','Есть следующий шаг',['Конкретная проверка помогает','не действовать под давлением.']),
         (1088,'03','Неопределённость видна',['Если проверка ответа не пройдена,','система отказывается от вывода.'])]
for x,n,title,lines in columns:
    text(x,690,n,18,teal,'bold')
    text(x,725,title,26,ink,'bold')
    for i,t in enumerate(lines):text(x,768+i*29,t,19,muted)
text(64,858,'Подлинность отправителя и фактов не установлена. Сервис помогает выбрать проверку.',16,muted)
text(1374,858,'ПРОТОТИП / 2026',14,muted)
svg.append('</svg>')
(OUT/'second-opinion-value.svg').write_text('\n'.join(svg))
im.save(OUT/'second-opinion-value.png',optimize=True)
# A lossless image PDF; no JPEG codec or extra package is needed.
import zlib
pixels=zlib.compress(im.tobytes())
content=b'q 1600 0 0 900 0 0 cm /Slide Do Q'
objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
         b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
         b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1600 900] /Resources << /XObject << /Slide 4 0 R >> >> /Contents 5 0 R >>',
         f'<< /Type /XObject /Subtype /Image /Width {W*S} /Height {H*S} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(pixels)} >>\nstream\n'.encode()+pixels+b'\nendstream',
         f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'\nendstream']
pdf=bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'); offsets=[0]
for i,obj in enumerate(objects,1):
    offsets.append(len(pdf)); pdf.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
xref=len(pdf)
pdf.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
for offset in offsets[1:]:pdf.extend(f'{offset:010d} 00000 n \n'.encode())
pdf.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
(OUT/'second-opinion-value.pdf').write_bytes(pdf)
print(OUT.resolve())
