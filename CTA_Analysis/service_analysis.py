"""Continuous service-resource equivalent, not a dispatchable timetable."""
from travel_time import read,components
from prepare_data import ROOT,write
from collections import defaultdict
import json,math

def main():
 c=json.loads((ROOT/'config.json').read_text()); groups=defaultdict(list)
 for r in read('scenario_stops'):groups[r['scope'],r['scenario'],r['direction']].append(r)
 trips=read('full_trips'); field={(r['kind'],r['period']):float(r['mean_sec']) for r in read('field_summary')}; impacts=read('passenger_impacts'); times=read('travel_summary'); out=[]
 # Preserve separate early and late windows; interval-weighted random arrival within each window.
 waits={}
 for d in ['North','South']:
  windows=defaultdict(list)
  for t in trips:
   if t['direction']==d:
    x=float(t['start_sec'])/60; windows[t['period'] if t['period']!='other' else ('early' if x<360 else 'late')].append(x)
  gaps=[b-a for xs in windows.values() for a,b in zip(sorted(xs),sorted(xs)[1:])]; waits[d]=sum(h*h for h in gaps)/(2*sum(gaps))
 for m,scope in [('anchored','scheduled'),('standalone','permanent'),('field','scheduled')]:
  base=sum(float(r['mean_runtime_min']) for r in times if r['method']==m and r['scenario']=='original')+2*c['layover_minutes_per_terminal']
  for s in ['cta','proposed']:
   fixed=sum(float(r['mean_runtime_min']) for r in times if r['method']==m and r['scenario']==s)+2*c['layover_minutes_per_terminal']
   def cycle(f):
    total=fixed
    for d in ['North','South']:
     rows=groups[scope,s,d]; new=[dict(r,trips=float(r['trips'])*f,probability=-math.expm1(-(float(r['boardings'])+float(r['alightings']))/(float(r['trips'])*f))) for r in rows]; old=components(rows,c); z=components(new,c); ref=components(groups[scope,'cta',d],c)
     def cost(q,p):
      if m!='field':return q['motion_delay_sec']+q['door_sec']+q['passenger_service_sec']
      scale=field['dwell',p]*ref['interior_events']/(ref['door_sec']+ref['passenger_service_sec']);return q['motion_delay_sec']+scale*(q['door_sec']+q['passenger_service_sec'])+q['clearance_eligible_events']*field['clearance',p]-q['clearance_theory_sec']
     ps=['am_peak' if t['period'] in ('am_peak','pm_peak') else 'midday' for t in trips if t['direction']==d];total+=sum(cost(z,p)-cost(old,p) for p in ps)/len(ps)/60
    return total
   lo,hi=1.,2.
   assert hi*cycle(hi)>base
   for _ in range(32):
    f=(lo+hi)/2
    if f*cycle(f)>base:hi=f
    else:lo=f
   f=(lo+hi)/2; rs=[r for r in impacts if r['method']==m and r['scenario']==s]; wait=sum(float(r['boardings'])*waits[r['direction']]*(1-1/f)/60 for r in rs); net=sum(float(r['net_passenger_hours_saved']) for r in rs)
   out.append(dict(method=m,scenario=s,baseline_cycle_min=base,fixed_frequency_cycle_min=fixed,reinvested_cycle_min=cycle(f),frequency_multiplier=f,additional_round_trip_equivalents=83*(f-1),baseline_nb_wait_min=waits['North'],baseline_sb_wait_min=waits['South'],wait_hours_saved=wait,net_hours_saved_holding_ride_impact_fixed=net+wait,resource_balance_error_min=f*cycle(f)-base))
 write(ROOT/'outputs','service_reinvestment',out)
if __name__=='__main__':main()
