"""Aggregate passenger time, common spatial segments; no OD inference."""
from travel_time import read, service, motion, components
from prepare_data import ROOT,write
from collections import defaultdict
import math,json
import numpy as np

def main():
 c=json.loads((ROOT/'config.json').read_text());out=ROOT/'outputs'; groups=defaultdict(list)
 for r in read('scenario_stops'):groups[r['scope'],r['scenario'],r['direction']].append(r)
 field={(r['kind'],r['period']):float(r['mean_sec']) for r in read('field_summary')}; trips=read('full_trips'); results=[]; segments=[]
 v=c['running_speed_mph']*5280/3600;a=c['acceleration_ft_s2'];d=c['deceleration_ft_s2']; road=c['roadway_loss_min_mile'][c['roadway']]*60/5280
 for method,scope in [('anchored','scheduled'),('standalone','permanent'),('field','scheduled')]:
  for direction in ['North','South']:
   original=groups[scope,'original',direction]; grid=np.array([float(r['distance_ft']) for r in original]); assert np.all(np.diff(grid)>0)
   ts=[t for t in trips if t['direction']==direction]; n=len(ts); ref=components(groups[scope,'cta',direction],c); data={}
   for scenario in ['original','cta','proposed']:
    rows=groups[scope,scenario,direction]; x=np.array([float(r['distance_ft']) for r in rows]); ix=np.searchsorted(grid,x); assert np.allclose(grid[ix],x)
    b=np.array([float(r['boardings']) for r in rows]); al=np.array([float(r['alightings']) for r in rows]); # balance tiny aggregate rounding discrepancy
    al*=b.sum()/al.sum(); load=np.cumsum(b-al); assert min(load)>-1e-6,(direction,scenario,min(load)); load=np.maximum(load,0); segload=load[np.searchsorted(x,grid[:-1],side='right')-1]
    p=np.array([float(r['probability']) for r in rows]);p[0]=p[-1]=1; move=np.zeros(len(grid)-1);clear=np.zeros_like(move);theory=np.zeros_like(move)
    for i in range(len(x)-1):
     surv=1.
     for j in range(i+1,len(x)):
      L=x[j]-x[i];w=p[i]*surv*p[j];u=min(v,math.sqrt(2*L/(1/a+1/d))); da=u*u/(2*a);dd=u*u/(2*d); z=grid[ix[i]:ix[j]+1]-x[i]; total=motion(L,v,a,d)
      cumulative=np.where(z<=da,np.sqrt(2*z/a),np.where(z>=L-dd,total-np.sqrt(2*np.maximum(0,L-z)/d),u/a+(z-da)/v));move[ix[i]:ix[j]]+=w*np.diff(cumulative)
      if i>0 and da>=c['clearance_distance_ft']:
       clear[ix[i]]+=w;theory[ix[i]]+=w*math.sqrt(2*c['clearance_distance_ft']/a)
      surv*=1-p[j]
    dwell=np.array([c['door_cycle_seconds']*p[k]+service(b[k]/n,al[k]/n,c['boarding_seconds'],c['alighting_seconds']) for k in range(len(x))]);dwell[0]=dwell[-1]=0
    # Only through passengers experience full dwell; boarding/alighting passengers half dwell convention.
    dwellload=np.maximum(0,load-b)+.5*(b+al)
    data[scenario]=(move,clear,theory,dwell,dwellload,segload,b,al,ix)
   for scenario,z in data.items():
    move,clear,theory,dwell,dl,loads,b,al,ix=z; total=0; vehicle=0; segment_time=np.zeros_like(move)
    for t in ts:
     proxy='am_peak' if t['period'] in ('am_peak','pm_peak') else 'midday'; scale=field['dwell',proxy]*ref['interior_events']/(ref['door_sec']+ref['passenger_service_sec']) if method=='field' else 1
     def running(z):return z[0]+(z[1]*field['clearance',proxy]-z[2] if method=='field' else 0)
     orig=data['original']; residual=road*np.diff(grid) if method=='standalone' else (float(t['runtime_min'])*60-running(orig).sum()-scale*orig[3].sum())*np.diff(grid)/(grid[-1]-grid[0]); run=running(z)+residual
     assert min(run)>=0; total+=(np.dot(run,loads)+scale*np.dot(dwell,dl))/n/3600;vehicle+=(run.sum()+scale*dwell.sum())/60;segment_time+=run/n
    results.append(dict(method=method,scope=scope,scenario=scenario,direction=direction,boardings=b.sum(),ride_passenger_hours=total,mean_runtime_min=vehicle/n))
    for k,l in enumerate(loads):segments.append(dict(method=method,scenario=scenario,direction=direction,from_ft=grid[k],to_ft=grid[k+1],daily_load=l,mean_motion_background_sec=segment_time[k]))
 summary={(r['scope'],r['scenario'],r['direction']):float(r['walking_passenger_hours']) for r in read('scenario_summary')};base={(r['method'],r['direction']):r for r in results if r['scenario']=='original'}
 for r in results:
  k=r['scope'],r['scenario'],r['direction']; walk=summary[k]-summary[r['scope'],'original',r['direction']]; ride=base[r['method'],r['direction']]['ride_passenger_hours']-r['ride_passenger_hours'];r.update(ride_hours_saved=ride,added_walk_hours=walk,wait_hours_saved=0,net_passenger_hours_saved=ride-walk,net_seconds_saved_per_boarding=(ride-walk)*3600/r['boardings'])
 write(out,'passenger_impacts',results);write(out,'passenger_segments',segments)
 for m in ['anchored','standalone','field']:
  for s in ['cta','proposed']:
   rs=[r for r in results if r['method']==m and r['scenario']==s];print(m,s,{k:round(sum(r[k] for r in rs),3) for k in ['ride_hours_saved','added_walk_hours','net_passenger_hours_saved']})
 # Reconcile vehicle runtime to phase 3 output.
 expected={(r['method'],r['scenario'],r['direction']):float(r['mean_runtime_min']) for r in read('travel_summary')}
 assert all(abs(r['mean_runtime_min']-expected[r['method'],r['scenario'],r['direction']])<1e-5 for r in results)
if __name__=='__main__':main()
