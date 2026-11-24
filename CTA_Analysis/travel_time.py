"""Expected travel times, exact independent-stop pair probabilities. Seconds and feet."""
from prepare_data import ROOT,write
import csv,json,math
from collections import defaultdict

def read(name):
    with (ROOT/'outputs'/f'{name}.csv').open() as f:return list(csv.DictReader(f))

def poisson(mu):
    # Recurrence; route stop means are small. Tail truncation below 1e-12.
    if mu>100: raise ValueError('Poisson mean exceeds supported range')
    p=math.exp(-mu); out=[p];k=0
    while k<max(20,mu) or 1-sum(out)>1e-12:
        k+=1;p*=mu/k;out.append(p)
    return out

def service(b,a,tb,ta):
    return sum(pb*pa*max(i*tb,j*ta) for i,pb in enumerate(poisson(b)) for j,pa in enumerate(poisson(a)))

def motion(L,v,a,d):
    u=min(v,math.sqrt(2*L/(1/a+1/d)))
    return u/a+u/d+max(0,L-u*u/2*(1/a+1/d))/v

def components(rows,c):
    v=c['running_speed_mph']*5280/3600;a=c['acceleration_ft_s2'];d=c['deceleration_ft_s2'];x=[float(r['distance_ft']) for r in rows];p=[float(r['probability']) for r in rows];p[0]=p[-1]=1
    assert all(y>=z for z,y in zip(x,x[1:]))
    move=0.;clear_events=0.;clear_theory=0.
    for i in range(len(x)-1):
        survive=1.
        for j in range(i+1,len(x)):
            L=x[j]-x[i];w=p[i]*survive*p[j];move+=w*motion(L,v,a,d)
            # Replace initial acceleration over one bus length for interior departures
            # only when the unconstrained acceleration distance reaches that length.
            u=min(v,math.sqrt(2*L/(1/a+1/d)))
            if i>0 and u*u/(2*a)>=c['clearance_distance_ft']:
                clear_events+=w;clear_theory+=w*math.sqrt(2*c['clearance_distance_ft']/a)
            survive*=1-p[j]
    n=float(rows[0]['trips']);svc=[service(float(r['boardings'])/n,float(r['alightings'])/n,c['boarding_seconds'],c['alighting_seconds']) for r in rows]
    # Terminal passenger service outside first-departure/last-arrival runtime.
    count=sum(p[1:-1]);passenger=sum(svc[1:-1]);doors=count*c['door_cycle_seconds'];length=x[-1]-x[0];base=length/v
    assert move>=base-1e-7
    return dict(distance_ft=length,freeflow_sec=base,motion_delay_sec=move-base,door_sec=doors,passenger_service_sec=passenger,interior_events=count,clearance_eligible_events=clear_events,clearance_theory_sec=clear_theory)

def main():
    c=json.loads((ROOT/'config.json').read_text());out=ROOT/'outputs';groups=defaultdict(list)
    for r in read('scenario_stops'):groups[r['scope'],r['scenario'],r['direction']].append(r)
    comp={k:components(sorted(rs,key=lambda r:float(r['distance_ft'])),c) for k,rs in groups.items()};write(out,'travel_components',[dict(scope=k[0],scenario=k[1],direction=k[2],**v) for k,v in comp.items()])
    field={(r['kind'],r['period']):float(r['mean_sec']) for r in read('field_summary')}
    def delay(z,method,period):
        standard=z['motion_delay_sec']+z['door_sec']+z['passenger_service_sec']
        if method!='field':return standard
        proxy='am_peak' if period in ('am_peak','pm_peak') else 'midday'
        # Field conditional mean dwell scaled by literature-predicted conditional dwell ratio
        # relative to observed CTA configuration, preserving demand concentration effects.
        return standard # overridden below
    result=[]
    for t in read('full_trips'):
        d=t['direction'];period=t['period'];base=float(t['runtime_min'])*60
        for method,scope in [('anchored','scheduled'),('standalone','permanent'),('field','scheduled')]:
            reference=comp[scope,'cta',d];proxy='am_peak' if period in ('am_peak','pm_peak') else 'midday'
            def cost(z):
                if method!='field':return delay(z,method,period)
                # Observed dwell includes door cycle as a modeling convention; normalize to CTA predicted total.
                scale=field['dwell',proxy]*reference['interior_events']/(reference['door_sec']+reference['passenger_service_sec'])
                return z['motion_delay_sec']+scale*(z['door_sec']+z['passenger_service_sec'])+z['clearance_eligible_events']*field['clearance',proxy]-z['clearance_theory_sec']
            orig=comp[scope,'original',d]
            for scenario in ('original','cta','proposed'):
                z=comp[scope,scenario,d];delta=cost(orig)-cost(z)
                runtime=(z['freeflow_sec']+cost(z)+z['distance_ft']/5280*c['roadway_loss_min_mile'][c['roadway']]*60) if method=='standalone' else base-delta
                assert runtime>0
                result.append(dict(trip_id=t['trip_id'],direction=d,period=period,method=method,scope=scope,scenario=scenario,runtime_min=runtime/60,saved_min=delta/60))
    write(out,'travel_times',result);g=defaultdict(list)
    for r in result:g[r['method'],r['scenario'],r['direction']].append(r)
    summary=[dict(method=k[0],scenario=k[1],direction=k[2],trips=len(rs),mean_runtime_min=sum(r['runtime_min'] for r in rs)/len(rs),mean_saved_min=sum(r['saved_min'] for r in rs)/len(rs),daily_bus_hours_saved=sum(r['saved_min'] for r in rs)/60) for k,rs in g.items()];write(out,'travel_summary',summary)
if __name__=='__main__':main()
