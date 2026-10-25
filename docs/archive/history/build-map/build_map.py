import csv, json, os
D = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(D, 'mapping.py')).read())

issues = {}
for n, s, m, t in csv.reader(open(os.path.join(D, 'issues.tsv')), delimiter='\t'):
    if s != 'moved':
        issues[int(n)] = {"t": t, "m": m, "s": s}

boxes = [dict(id=b[0], col=b[1], row=b[2], name=b[3], desc=b[4], build=b[5], design=b[6], explain=b[7], ext=EXT.get(b[0], [])) for b in BOXES]
data = dict(issues=issues, boxes=boxes, containerDesign=CONTAINER_DESIGN, whole=WHOLE_DESIGN, releases=RELEASES, cmeNext=open(os.path.join(D, 'cme_next.tsv')).read().strip().split('\t'))

html = open(os.path.join(D, 'map_template.html')).read().replace('__DATA__', json.dumps(data, ensure_ascii=False))
os.makedirs(os.path.join(D, 'out'), exist_ok=True)
open(os.path.join(D, 'out', 'Laddrix-Build-Map.html'), 'w').write(html)
print('ok', len(html))
