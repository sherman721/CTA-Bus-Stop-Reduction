"""Canonical regular-Poisson reinvestment summary from the sensitivity engine."""
from travel_time import read
from prepare_data import ROOT, write
import json, math
from collections import defaultdict

def main():
    c=json.loads((ROOT/'config.json').read_text())
    trips=read('full_trips'); times=read('travel_summary'); waits={}
    for d in ('North','South'):
        windows=defaultdict(list)
        for t in trips:
            if t['direction']==d:
                x=float(t['start_sec'])/60
                windows[t['period'] if t['period']!='other' else ('early' if x<360 else 'late')].append(x)
        gaps=[b-a for xs in windows.values() for a,b in zip(sorted(xs),sorted(xs)[1:])]
        waits[d]=sum(h*h for h in gaps)/(2*sum(gaps))
    n=sum(t['direction']=='North' for t in trips)
    assert n==sum(t['direction']=='South' for t in trips)
    out=[]
    for r in read('stopping_sensitivity'):
        if float(r['alpha'])!=0 or r['reinvest']!='True': continue
        m,s=r['method'],r['scenario']
        def cycle(scenario):
            return sum(float(t['mean_runtime_min']) for t in times if t['method']==m and t['scenario']==scenario)+2*c['layover_minutes_per_terminal']
        base=cycle('original'); fixed=cycle(s); f=float(r['frequency_multiplier']); new=base-float(r['round_trip_saved_min'])
        ride,walk,wait,net=(float(r[k]) for k in ('ride_hours_saved','walk_hours_added','wait_hours_saved','net_hours_saved'))
        assert math.isclose(ride-walk+wait,net,abs_tol=1e-8)
        error=f*new-base
        assert abs(error)<1e-4 or (f==1 and new>=base)
        out.append(dict(method=m,scenario=s,baseline_cycle_min=base,fixed_frequency_cycle_min=fixed,reinvested_cycle_min=new,frequency_multiplier=f,additional_round_trip_equivalents=n*(f-1),baseline_nb_wait_min=waits['North'],baseline_sb_wait_min=waits['South'],ride_hours_saved=ride,walk_hours_added=walk,wait_hours_saved=wait,net_hours_saved=net,resource_balance_error_min=error))
    assert len(out)==6 and len({(r['method'],r['scenario']) for r in out})==6
    write(ROOT/'outputs','service_reinvestment',out)
if __name__=='__main__':main()
