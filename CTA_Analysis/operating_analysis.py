"""Resource and energy break-even accounting, not a vehicle scheduling optimizer."""
from travel_time import read
from prepare_data import ROOT,write
from collections import defaultdict
import math,json
c=json.loads((ROOT/'config.json').read_text()); trips=read('full_trips'); times=read('travel_times'); lay=c['layover_minutes_per_terminal']; n=len(trips)/2
miles=sum(float(t['distance_ft']) for t in trips)/5280
# Sweep scheduled trip intervals with assumed terminal recovery; not actual fleet assignment.
def peak(rows):
 events=[]
 for start,end in rows: events.extend([(start,1),(end,-1)])
 k=mx=0
 for _,delta in sorted(events):k+=delta;mx=max(mx,k)
 return mx
lookup={t['trip_id']:t for t in trips}; rows=[]
for r in read('stopping_sensitivity'):
 if float(r['alpha'])!=0:continue
 m,s=r['method'],r['scenario']; f=float(r['frequency_multiplier']); saved=float(r['round_trip_saved_min']); reinvest=r['reinvest']=='True'
 base=[t for t in times if t['method']==m and t['scenario']=='original']; cycle=sum(float(t['runtime_min']) for t in base)/n+2*lay
 fixed=[t for t in times if t['method']==m and t['scenario']==s]
 def intervals(ts):return [(float(lookup[t['trip_id']]['start_sec']),float(lookup[t['trip_id']]['start_sec'])+60*(float(t['runtime_min'])+lay)) for t in ts]
 # Minimum concurrent occupancy, not a feasible vehicle block plan.
 rows.append(dict(method=m,scenario=s,reinvest=reinvest,baseline_cycle_min=cycle,new_cycle_min=cycle-saved,round_trip_equivalents=n*f,baseline_daily_miles=miles,new_daily_miles=miles*f,additional_daily_miles=miles*(f-1),running_hours_released=n*saved/60,net_cycle_resource_hours_saved=n*(cycle-f*(cycle-saved))/60,baseline_concurrent_trip_lower_bound=peak(intervals(base)),fixed_departure_concurrent_lower_bound=peak(intervals(fixed)),fuel_per_mile_reduction_required_pct=100*(1-1/f)))
write(ROOT/'outputs','operating_resources',rows)
for r in rows:print(r['method'],r['scenario'],r['reinvest'],round(r['additional_daily_miles'],1),round(r['net_cycle_resource_hours_saved'],2),r['baseline_concurrent_trip_lower_bound'],r['fixed_departure_concurrent_lower_bound'],round(r['fuel_per_mile_reduction_required_pct'],2))
