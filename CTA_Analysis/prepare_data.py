"""Phase 1: normalize original inputs without editing them."""
import csv, io, json, zipfile, datetime as dt, statistics as st, math
from pathlib import Path
from collections import defaultdict, Counter
import openpyxl
ROOT=Path(__file__).resolve().parent

def read_zip(path, name):
    with zipfile.ZipFile(path) as z:
        matches=[n for n in z.namelist() if n.split('/')[-1]==name and not n.startswith('__MACOSX/')]
        if len(matches)!=1: raise ValueError((name,matches))
        return z.read(matches[0])
def table(path,name): return list(csv.DictReader(io.StringIO(read_zip(path,name).decode('utf-8-sig'))))
def write(out,name,rows):
    if not rows:
        (out/(name+'.csv')).write_text('')
        return
    with (out/(name+'.csv')).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def seconds(t):
    h,m,s=map(int,t.split(':'));return h*3600+m*60+s
def period(t,c):
    for k,(a,b) in c['periods'].items():
        if a*3600<=t<b*3600:return k
    return 'other'
def sid(v): return str(int(v)) if isinstance(v,(int,float)) else str(v)
def main():
    c=json.loads((ROOT/'config.json').read_text());raw=ROOT/'data/raw';out=ROOT/'outputs';out.mkdir(exist_ok=True)
    w=openpyxl.load_workbook(io.BytesIO(read_zip(raw/'01_Consolidation.zip','route50_optimized.xlsx')),read_only=True,data_only=True)
    it=iter(w['Individual Stops'].values);headers=next(it); stops=[]
    for row in it:
        r=dict(zip(headers,row))
        if str(r['ROUTE'])!=c['route']:continue
        stops.append(dict(direction=r['DIRECTION'],stop_id=sid(r['CTASTOPID']),name=r['STOP_NAME'],lat=r['LAT'],lon=r['LON'],boardings=r['AVG_BOARDINGS'],alightings=r['AVG_ALIGHTINGS'],decision=r['STOP_DECISION']))
    keys={(r['direction'],r['stop_id']):r for r in stops};assert len(keys)==len(stops)
    z=raw/'03_GTFS.zip';date=c['service_date'];day=dt.datetime.strptime(date,'%Y%m%d').strftime('%A').lower()
    active={r['service_id'] for r in table(z,'calendar.txt') if r['start_date']<=date<=r['end_date'] and r[day]=='1'}
    for r in table(z,'calendar_dates.txt'):
        if r['date']==date:
            if r['exception_type']=='1':active.add(r['service_id'])
            else:active.discard(r['service_id'])
    trips={r['trip_id']:r for r in table(z,'trips.txt') if r['service_id'] in active and r['route_id']==c['route']}; assert trips
    seq=defaultdict(list)
    for r in table(z,'stop_times.txt'):
        if r['trip_id'] in trips:seq[r['trip_id']].append(r)
    triprows=[];events=[];patterns=defaultdict(Counter); pattern_examples={}
    for tid,t in trips.items():
        a=sorted(seq[tid],key=lambda r:int(r['stop_sequence']));start=seconds(a[0]['departure_time']);end=seconds(a[-1]['arrival_time']);d=t['direction'];p=period(start,c)
        signature=tuple(r['stop_id'] for r in a);patterns[d][signature]+=1;pattern_examples[d,signature]=a
        triprows.append(dict(trip_id=tid,direction=d,block_id=t['block_id'],period=p,start_sec=start,end_sec=end,runtime_min=(end-start)/60,stop_count=len(a),distance_ft=float(a[-1]['shape_dist_traveled'])-float(a[0]['shape_dist_traveled'])))
        for r in a:events.append(dict(trip_id=tid,direction=d,stop_id=r['stop_id'],sequence=r['stop_sequence'],arrival_sec=seconds(r['arrival_time']),departure_sec=seconds(r['departure_time']),distance_ft=r['shape_dist_traveled'],ridership_match=(d,r['stop_id']) in keys))
    gaps=[];canonical=[]
    for d,counts in patterns.items():
        sig,n=counts.most_common(1)[0];a=pattern_examples[d,sig]
        for i,r in enumerate(a):
            k=(d,r['stop_id']);dist=float(r['shape_dist_traveled']);gap=dist-float(a[i-1]['shape_dist_traveled']) if i else 0
            canonical.append(dict(direction=d,stop_id=k[1],sequence=i+1,distance_ft=dist,pattern_trips=n,ridership_match=k in keys))
            if gap>c['barrier_review_gap_ft']:gaps.append(dict(direction=d,from_stop=a[i-1]['stop_id'],to_stop=k[1],gap_ft=gap,status='REVIEW_NOT_CONFIRMED'))
    obs=[];fw=openpyxl.load_workbook(raw/'02_Field_Observations.xlsx',read_only=True,data_only=True)
    for sheet in fw:
        rows=list(sheet.values)
        if not rows or len(rows[0])<7 or rows[0][4]!='Arrival Time' or rows[0][5] not in ('Dwell Time','Clearance Time','Clearance') or 'Logging Sheet' in sheet.title:continue
        a=[(i,r) for i,r in enumerate(rows[1:],2) if len(r)>=7 and isinstance(r[1],(int,float))]
        observed=[i for i,r in a if isinstance(r[4],dt.time) or isinstance(r[5],(int,float)) or 'got off' in str(r[6]).lower()]
        if not observed:continue
        first,last=min(observed),max(observed);start=next(r[4] for i,r in a if isinstance(r[4],dt.time));p=period(start.hour*3600+start.minute*60,c);kind=rows[0][5].split()[0].lower()
        for i,r in a:
            d,stop,street,cross,arrival,val,note=r[:7];note=str(note or '');n=note.lower();numeric=isinstance(val,(int,float)) and val>=0
            flag='valid' if numeric else 'unknown';state='stopped' if numeric else 'unknown'
            if i<first or i>last:flag='outside_coverage';state='not_observed'
            elif 'eliminated' in n and 'driver' not in n:flag='removed';state='not_served'
            elif i==first or i==last or 'got on' in n or 'got off' in n or 'final stop' in n:flag='boundary'
            elif 'waited for truck' in n:flag='truck_delay'
            elif 'break' in n and '818' in sheet.title:flag='driver_break'
            elif 'break' in n:flag='copied_note_valid' if numeric else 'unknown'
            elif 'driver told' in n:flag='transition_delay'
            elif not numeric and isinstance(arrival,dt.time) and not note:flag='skipped';state='skipped'
            obs.append(dict(trip=sheet.title,source_row=i,direction=d,stop_id=sid(stop),name=f'{street} & {cross}',kind=kind,period=p,arrival=str(arrival or ''),raw_measurement=str(val if val is not None else ''),seconds=val if numeric else '',status=state,qc=flag,include=numeric and flag in ('valid','copied_note_valid'),note=note))
    groups=defaultdict(list)
    for r in obs:
        if r['include']:groups[r['kind'],r['period']].append(r['seconds'])
    stats=[dict(kind=k,period=p,n=len(v),mean_sec=st.mean(v),median_sec=st.median(v),sd_sec=st.stdev(v) if len(v)>1 else '',min_sec=min(v),max_sec=max(v)) for (k,p),v in sorted(groups.items())]
    summary=[]
    for d,p in sorted({(r['direction'],r['period']) for r in triprows}):
        a=[r for r in triprows if r['direction']==d and r['period']==p]; starts=sorted({r['start_sec'] for r in a});headways=[(b-a)/60 for a,b in zip(starts,starts[1:])];summary.append(dict(direction=d,period=p,trips=len(a),distinct_departures=len(starts),mean_runtime_min=st.mean(r['runtime_min'] for r in a),mean_headway_min=st.mean(headways) if headways else ''))
    for name,rows in [('stops',stops),('trips',triprows),('stop_events',events),('canonical_sequence',canonical),('barrier_review',gaps),('field_observations',obs),('field_summary',stats),('schedule_summary',summary)]:write(out,name,rows)
    unmatched=[r for r in stops if (r['direction'],r['stop_id']) not in {(e['direction'],e['stop_id']) for e in events}];write(out,'unmatched_ridership',unmatched)
    report=dict(service_date=date,active_services=sorted(active),stops=len(stops),removed=sum(r['decision']=='REMOVED' for r in stops),trips=len(trips),directions=dict(Counter(r['direction'] for r in triprows)),unmatched_ridership=len(unmatched),unmatched_gtfs_keys=sorted({(r['direction'],r['stop_id']) for r in events if not r['ridership_match']}),field_logs=len({r['trip'] for r in obs}),field_qc=dict(Counter(r['qc'] for r in obs)),barrier_candidates=len(gaps),field_summary=stats)
    (out/'validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
