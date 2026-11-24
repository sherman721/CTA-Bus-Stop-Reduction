from break_even import groups,stats
from travel_time import read
from prepare_data import ROOT,write
from collections import defaultdict
import math
lookup={(r['direction'],r['stop_id']):float(r['probability']) for d in ['North','South'] for r in groups['scheduled','cta',d]}
rows=[]; unmatched=[]
for r in read('field_observations'):
 if r['status'] not in ('stopped','skipped') or r['qc']=='boundary':continue
 k=r['direction'],r['stop_id']
 if k not in lookup:unmatched.append(r);continue
 rows.append(dict(trip=r['trip'],direction=k[0],stop_id=k[1],kind=r['kind'],observed=int(r['status']=='stopped'),predicted=lookup[k]))
out=[]
for name,rs in [('all',rows)]+[(d,[r for r in rows if r['direction']==d]) for d in ['North','South']]:
 n=len(rs);o=sum(r['observed'] for r in rs);p=sum(r['predicted'] for r in rs);out.append(dict(direction=name,observations=n,stopped=o,observed_probability=o/n,poisson_probability=p/n,implied_alpha=(o-p)/(n-p)))
write(ROOT/'outputs','field_stopping_check',out);write(ROOT/'outputs','field_stopping_matched',rows)
print(out,'unmatched',len(unmatched))
