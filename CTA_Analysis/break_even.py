"""Nonlinear stopping sensitivity; preserves primary outputs. Fixed field calibration."""
from travel_time import read,service,motion,components
from prepare_data import ROOT,write
from collections import defaultdict
from functools import lru_cache
import numpy as np, math,json
from scipy.optimize import brentq
c=json.loads((ROOT/'config.json').read_text()); groups=defaultdict(list)
for r in read('scenario_stops'): groups[r['scope'],r['scenario'],r['direction']].append(r)
trips=read('full_trips'); field={(r['kind'],r['period']):float(r['mean_sec']) for r in read('field_summary')}; walks={(r['scope'],r['scenario'],r['direction']):float(r['walking_passenger_hours']) for r in read('scenario_summary')}
v=c['running_speed_mph']*5280/3600;a=c['acceleration_ft_s2'];d=c['deceleration_ft_s2']; road=c['roadway_loss_min_mile'][c['roadway']]*60/5280
waits={}; stats={}; geom={}
for direction in ['North','South']:
 ts=[t for t in trips if t['direction']==direction]; windows=defaultdict(list)
 for t in ts:
  x=float(t['start_sec'])/60;windows[t['period'] if t['period']!='other' else ('early' if x<360 else 'late')].append(x)
 gaps=[y-x for xs in windows.values() for x,y in zip(sorted(xs),sorted(xs)[1:])];waits[direction]=sum(h*h for h in gaps)/(2*sum(gaps));stats[direction]=(len(ts),np.mean([float(t['runtime_min'])*60 for t in ts]),np.mean([t['period'] in ('am_peak','pm_peak') for t in ts]))
for (scope,s,direction),rows in groups.items():
 grid=np.array([float(r['distance_ft']) for r in groups[scope,'original',direction]]);x=np.array([float(r['distance_ft']) for r in rows]);ix=np.searchsorted(grid,x);b=np.array([float(r['boardings']) for r in rows]);al=np.array([float(r['alightings']) for r in rows]);al*=b.sum()/al.sum();load=np.maximum(0,np.cumsum(b-al));loads=load[np.searchsorted(x,grid[:-1],side='right')-1];dl=np.maximum(0,load-b)+.5*(b+al); pairs=[]
 for i in range(len(x)-1):
  for j in range(i+1,len(x)):
   L=x[j]-x[i];u=min(v,math.sqrt(2*L/(1/a+1/d)));da=u*u/(2*a);dd=u*u/(2*d);z=grid[ix[i]:ix[j]+1]-x[i];total=motion(L,v,a,d);cum=np.where(z<=da,np.sqrt(2*z/a),np.where(z>=L-dd,total-np.sqrt(2*np.maximum(0,L-z)/d),u/a+(z-da)/v));seg=np.diff(cum);eligible=i>0 and da>=c['clearance_distance_ft'];pairs.append((i,j,total,float(seg@loads[ix[i]:ix[j]]),eligible,loads[ix[i]]))
 geom[scope,s,direction]=(grid,b,al,dl,loads,pairs)
@lru_cache(maxsize=4096)
def raw(scope,s,direction,alpha,f):
 grid,b,al,dl,loads,pairs=geom[scope,s,direction];n=stats[direction][0]*f;p=-np.expm1(-(b+al)/n);p=p+alpha*(1-p);p[0]=p[-1]=1;move=ride=ce=cr=0.;surv=1.;last=-1
 for i,j,t,r,e,l in pairs:
  if i!=last:surv=1.;last=i
  w=p[i]*surv*p[j];move+=w*t;ride+=w*r;ce+=w*e;cr+=w*e*l;surv*=1-p[j]
 dwell=np.array([c['door_cycle_seconds']*q+service(B/n,A/n,c['boarding_seconds'],c['alighting_seconds']) for q,B,A in zip(p,b,al)]);dwell[0]=dwell[-1]=0
 return move,ride,ce,cr,dwell.sum(),float(dwell@dl),sum(p[1:-1]),b.sum(),float(np.diff(grid)@loads),grid[-1]-grid[0],len(p)-2

def cost(m,scope,s,direction,alpha,f):
 z=raw(scope,s,direction,alpha,f);move,ride,ce,cr,dw,dr,events,b,ld,L,k=z;n,base,peak=stats[direction];scale=1.;extra=0.
 if m=='field':
  ref=raw(scope,'cta',direction,0.,1.);scale=sum(w*field['dwell',p]*ref[6]/ref[4] for w,p in [(peak,'am_peak'),(1-peak,'midday')]);extra=sum(w*field['clearance',p] for w,p in [(peak,'am_peak'),(1-peak,'midday')])-math.sqrt(2*c['clearance_distance_ft']/a)
 return move+ce*extra+dw*scale,ride+cr*extra+dr*scale,z

def evaluate(m,s,alpha,reinvest):
 scope='permanent' if m=='standalone' else 'scheduled';ds=['North','South'];orig={d:cost(m,scope,'original',d,alpha,1.) for d in ds}; residual={d:road if m=='standalone' else (stats[d][1]-orig[d][0])/orig[d][2][9] for d in ds};base=sum(orig[d][0]+residual[d]*orig[d][2][9] for d in ds)/60+2*c["layover_minutes_per_terminal"]
 def calc(f):
  zs={d:cost(m,scope,s,d,alpha,f) for d in ds};cycle=sum(zs[d][0]+residual[d]*zs[d][2][9] for d in ds)/60+2*c["layover_minutes_per_terminal"];return cycle,zs
 f=1.
 if reinvest and calc(1.)[0]<base:f=brentq(lambda f:f*calc(f)[0]-base,1,2,xtol=1e-7)
 cycle,zs=calc(f);ride=sum((orig[d][1]+residual[d]*orig[d][2][8]-zs[d][1]-residual[d]*zs[d][2][8])/3600 for d in ds);walk=sum(walks[scope,s,d]-walks[scope,'original',d] for d in ds);wait=sum(zs[d][2][7]*waits[d]*(1-1/f)/60 for d in ds)
 return dict(method=m,scenario=s,reinvest=reinvest,alpha=alpha,frequency_multiplier=f,round_trip_saved_min=base-cycle,ride_hours_saved=ride,walk_hours_added=walk,wait_hours_saved=wait,net_hours_saved=ride-walk+wait,original_nb_events=orig['North'][2][6],original_sb_events=orig['South'][2][6],original_mean_probability=sum(orig[d][2][6] for d in ds)/sum(orig[d][2][10] for d in ds),resource_error_min=f*cycle-base if reinvest else 0)
if __name__=='__main__':
 out=[]; curves=[]
 for m in ['anchored','standalone','field']:
  for s in ['cta','proposed']:
   for reinvest in [False,True]:
    points=[evaluate(m,s,float(x),reinvest) for x in np.linspace(0,1,11)];curves+=points
    if points[0]['net_hours_saved']>=0:r=points[0];status='already beneficial'
    else:
     bracket=next(((a['alpha'],b['alpha']) for a,b in zip(points,points[1:]) if a['net_hours_saved']<0<=b['net_hours_saved']),None)
     if bracket:r=evaluate(m,s,brentq(lambda x:evaluate(m,s,x,reinvest)['net_hours_saved'],*bracket,xtol=1e-6),reinvest);status='crossing'
     else:r=points[-1];status='no crossing found'
    out.append(dict(status=status,**r));print(m,s,reinvest,status,round(r['alpha'],4),round(r['net_hours_saved'],3),flush=True)
 write(ROOT/'outputs','break_even',out);write(ROOT/'outputs','stopping_sensitivity',curves)
