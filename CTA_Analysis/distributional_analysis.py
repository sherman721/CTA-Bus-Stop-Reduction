"""Export permanent or scheduled walking impacts from existing redistribution results."""
import argparse,csv,json,math
from pathlib import Path
from collections import defaultdict

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent);p.add_argument('--scope',choices=['permanent','scheduled'],default='permanent');a=p.parse_args();out=a.root/'outputs';cfg=json.loads((a.root/'config.json').read_text());speed=cfg['walking_speed_m_s'];route=str(cfg['route'])
    def read(n):return list(csv.DictReader((out/(n+'.csv')).open()))
    def write(n,rows):
        with (out/(n+'.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    inv={(r['direction'],r['stop_id']):r for r in read('reconciled_inventory')};alloc=read('redistribution');stops=read('scenario_stops');summary=read('scenario_summary');features=[];rows=[];dist=[]
    for scenario,label in [('cta','CTA Consolidation'),('proposed','New Consolidation')]:
        groups=defaultdict(list)
        for r in alloc:
            if r['scope']==a.scope and r['scenario']==scenario:groups[r['direction'],r['source']].append(r)
        baseline=sum(float(r['boardings'])+float(r['alightings']) for r in stops if r['scope']==a.scope and r['scenario']=='original');samples=[];total=0
        for (direction,source),rs in groups.items():
            s=inv[direction,source];targets=[];b=al=ft=0
            for r in rs:
                x,y,d=map(float,(r['boardings'],r['alightings'],r['distance_ft']));b+=x;al+=y;ft+=(x+y)*d;samples.append((d,x+y));targets.append(dict(stop_id=r['target'],name=inv[direction,r['target']]['name'],share=float(r['weight']),distance_ft=d,walking_minutes=d*.3048/speed/60))
            assert math.isclose(sum(t['share'] for t in targets),1,abs_tol=1e-8)
            hours=ft*.3048/speed/3600;total+=hours
            props=dict(route=route,scope=a.scope,scenario=label,direction=direction,stop_id=source,name=s['name'],boardings=b,alightings=al,activity=b+al,mean_distance_ft=ft/(b+al) if b+al else 0,walking_hours=hours,targets=targets)
            features.append(dict(type='Feature',geometry=dict(type='Point',coordinates=[float(s['lon']),float(s['lat'])]),properties=props));rows.append({**props,'targets':json.dumps(targets)})
        affected=sum(w for d,w in samples);expected=sum(float(r['walking_passenger_hours']) for r in summary if r['scope']==a.scope and r['scenario']==scenario);assert math.isclose(total,expected,abs_tol=1e-7),(total,expected)
        for lo,hi in [(0,330),(330,660),(660,990),(990,1320),(1320,math.inf)]:
            n=sum(w for d,w in samples if lo<d<=hi);dist.append(dict(route=route,scope=a.scope,scenario=label,category=f'{lo}–{hi}',activity=n,share_all=n/baseline,share_affected=n/affected if affected else 0))
        dist.append(dict(route=route,scope=a.scope,scenario=label,category='None',activity=baseline-affected,share_all=(baseline-affected)/baseline,share_affected=0))
        print(label,len(groups),'affected activity',round(affected,2),'walking hours',round(total,4))
    write('stop_impacts',rows);write('walking_distribution',dist);(out/'distributional_impacts.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=features),allow_nan=False))
if __name__=='__main__':main()
