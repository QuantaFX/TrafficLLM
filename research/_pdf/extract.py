import pypdf, warnings, re, sys
warnings.filterwarnings('ignore')
r = pypdf.PdfReader(r'C:/Users/narch/Desktop/TrafficLLM/research/_pdf/traffic_r1.pdf')
t = ''.join((p.extract_text() or '') for p in r.pages)
open('tr1.txt', 'w', encoding='utf-8').write(t)
for kw in ['asynchronous', 'synchron', 'latency', 'Rush', 'interval', 'hub']:
    print('\n==', kw)
    for m in list(re.finditer(kw, t, re.I))[:3]:
        print('  ...', t[max(0, m.start()-400):m.end()+400].replace('\n', ' '))