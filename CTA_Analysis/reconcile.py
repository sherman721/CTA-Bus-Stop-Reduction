"""Phase 2: full-length service, stop reconciliation and conserved demand."""
from prepare_data import *

def main():
    c=json.loads((ROOT/'config.json').read_text()); out=ROOT/'outputs'
    def read(n): return list(csv.DictReader((out/(n+'.csv')).open()))
    raw=read('stops'); trips=read('trips'); events=read('stop_events'); canon=read('canonical_sequence')
    aliases={('North','8970'):'18836',('South','8615'):'18834'}
    gtfs={r['stop_id']:r for r in table(ROOT/'data/raw/03_GTFS.zip','stops.txt')}
    inventory={}
    for r in raw:
        d=r['direction']; old=r['stop_id']; new=aliases.get((d,old),old); r.update(historical_id=old,stop_id=new)
        if new in gtfs:r.update(lat=gtfs[new]['stop_lat'],lon=gtfs[new]['stop_lon'])
        inventory[d,new]=r
    patterns=defaultdict(list)
    for e in events:patterns[e['trip_id']].append(e)
    signatures={d:tuple(r['stop_id'] for r in canon if r['direction']==d) for d in {r['direction'] for r in canon}}
    full=[t for t in trips if tuple(e['stop_id'] for e in patterns[t['trip_id']])==signatures[t['direction']]]
    ids={t['trip_id'] for t in full}; write(out,'full_trips',full);write(out,'excluded_trips',[t for t in trips if t['trip_id'] not in ids]);write(out,'full_stop_events',[dict(e,ridership_match=True) for e in events if e['trip_id'] in ids])
    counts=Counter(t['direction'] for t in full); removed_cta={('North',x) for x in ['8752','8898','8899','8914','8916']}|{('South',x) for x in ['8755','8882','8881','8862','8864','8876','8890']}
    rows=[];alloc=[];summary=[];schedule=[]
    for d,n in counts.items():
        a=[dict(inventory[d,r['stop_id']],distance_ft=float(r['distance_ft']),temporary_inactive=False) for r in canon if r['direction']==d and (d,r['stop_id'])!=('North','8901')]
        assert len(a)==len(signatures[d])-(d=='North'); permanent=[dict(r) for r in a]
        if d=='North':
            # Interpolate along the Ashland segment using latitude fraction.
            i=next(i for i,r in enumerate(permanent) if r['stop_id']=='8622'); left,right=permanent[i:i+2]; r=dict(inventory[d,'8623']); f=(float(r['lat'])-float(left['lat']))/(float(right['lat'])-float(left['lat'])); assert 0<f<1
            r.update(distance_ft=left['distance_ft']+f*(right['distance_ft']-left['distance_ft']),temporary_inactive=True);permanent.insert(i+1,r)
        for scope,base in [('scheduled',a),('permanent',permanent)]:
            # Scheduled baseline allocates historical Catalpa demand to active neighbors first.
            source=permanent
            for scenario in ['original','cta','proposed']:
                kept=[r for r in base if not (scenario=='cta' and ((d,r['stop_id']) in removed_cta or (d,r['stop_id'])==('North','8901')) or scenario=='proposed' and r['decision']=='REMOVED')]
                demand={r['stop_id']:[0.,0.] for r in kept}; walking=0
                for r in source:
                    b,al=float(r['boardings']),float(r['alightings']); x=r['distance_ft']; key=r['stop_id']
                    if key in demand: targets=[(r,1.)]
                    else:
                        left=[s for s in kept if s['distance_ft']<=x];right=[s for s in kept if s['distance_ft']>=x]; candidates=([left[-1]] if left else [])+([right[0]] if right else []); eligible=[s for s in candidates if abs(s['distance_ft']-x)<=c['barrier_review_gap_ft']]; chosen=eligible or [min(candidates,key=lambda s:abs(s['distance_ft']-x))]; weights=[1/max(abs(s['distance_ft']-x),1e-9) for s in chosen];targets=list(zip(chosen,[w/sum(weights) for w in weights]))
                    for s,w in targets:
                        k=s['stop_id']; dist=abs(s['distance_ft']-x);demand[k][0]+=b*w;demand[k][1]+=al*w;walking+=(b+al)*w*dist
                        if key!=k:alloc.append(dict(scope=scope,scenario=scenario,direction=d,source=key,target=k,weight=w,distance_ft=dist,boardings=b*w,alightings=al*w,review=dist>c['barrier_review_gap_ft']))
                for r in kept:
                    b,al=demand[r['stop_id']];p=-math.expm1(-(b+al)/n);rows.append(dict(scope=scope,scenario=scenario,direction=d,stop_id=r['stop_id'],historical_id=r['historical_id'],name=r['name'],distance_ft=r['distance_ft'],boardings=b,alightings=al,trips=n,probability=p,temporary_inactive=r['temporary_inactive']))
                for j,k in enumerate(['boardings','alightings']):assert math.isclose(sum(v[j] for v in demand.values()),sum(float(r[k]) for r in source),abs_tol=1e-7)
                summary.append(dict(scope=scope,scenario=scenario,direction=d,stops=len(kept),trips=n,boardings=sum(v[0] for v in demand.values()),alightings=sum(v[1] for v in demand.values()),expected_interior_stops_per_trip=sum(-math.expm1(-sum(demand[r['stop_id']])/n) for r in kept[1:-1]),expected_total_stops_per_trip=2+sum(-math.expm1(-sum(demand[r['stop_id']])/n) for r in kept[1:-1]),walking_passenger_hours=walking*.3048/c['walking_speed_m_s']/3600))
        for p in ['am_peak','midday','pm_peak','other']:
            t=[t for t in full if t['direction']==d and t['period']==p]; starts=sorted({int(t['start_sec']) for t in t}); h=[(y-x)/60 for x,y in zip(starts,starts[1:])];schedule.append(dict(direction=d,period=p,trips=len(t),mean_runtime_min=st.mean(float(t['runtime_min']) for t in t),mean_headway_min=st.mean(h) if h else ''))
    for name,data in [('reconciled_inventory',list(inventory.values())),('scenario_stops',rows),('redistribution',alloc),('scenario_summary',summary),('full_schedule_summary',schedule)]:write(out,name,data)
    report=dict(full_trips=counts,excluded=len(trips)-len(full),summary=summary, demand_conservation_passed=True)
    (out/'phase2_validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
