"""Optional user-input-only valuations; never invoked by the core pipeline."""
import argparse,csv,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
FIELDS={
 'labor':('paid_hours_saved_per_day','marginal_cost_per_paid_hour'),
 'vehicle_maintenance':('marginal_nonenergy_maintenance_cost_per_mile',),
 'energy':('baseline_units_per_mile','scenario_units_per_mile','price_per_unit'),
 'stop_maintenance':('net_annual_stop_maintenance_savings',),
 'capital':('one_time_removal_cost','one_time_amenity_cost')}
def number(d,k):
 v=d.get(k)
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0: raise ValueError(f'{k}: supply a finite nonnegative number')
 return v
def calculate(r,d):
 days=number(d,'annual_equivalent_weekdays'); currency=d.get('currency'); source=d.get('source')
 if days<=0 or not isinstance(currency,str) or not currency.strip() or not isinstance(source,str) or not source.strip(): raise ValueError('Positive annual_equivalent_weekdays, currency and source required')
 out={k:r[k] for k in ('method','scenario','reinvest')}; out.update(currency=currency,source=source,annual_equivalent_weekdays=days)
 for module,keys in FIELDS.items():
  x=d.get(module)
  if x is None: out[module+'_status']='not evaluated'; continue
  if not isinstance(x,dict): raise ValueError(f'{module}: expected object or null')
  v=[number(x,k) for k in keys]; out[module+'_status']='evaluated'
  if module=='labor':
   if v[0]>max(0,float(r['net_cycle_resource_hours_saved']))+1e-5: raise ValueError('Paid hours saved exceed modeled net resource hours; cannot credit reinvested hours')
   out['annual_labor_savings']=v[0]*v[1]*days
  elif module=='vehicle_maintenance': out['annual_vehicle_maintenance_savings']=-float(r['additional_daily_miles'])*v[0]*days
  elif module=='energy':
   unit=x.get('unit')
   if not isinstance(unit,str) or not unit.strip(): raise ValueError('energy.unit required (e.g. gallons or kWh)')
   b=float(r['baseline_daily_miles'])*v[0]; n=float(r['new_daily_miles'])*v[1]; out.update(energy_unit=unit,baseline_daily_energy=b,scenario_daily_energy=n,annual_energy_savings=(b-n)*days,annual_energy_cost_savings=(b-n)*v[2]*days)
  elif module=='stop_maintenance': out['annual_stop_maintenance_savings']=v[0]
  elif module=='capital': out['one_time_capital_cost']=sum(v)
 return out
def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--inputs',required=True,type=Path); p.add_argument('--output',required=True,type=Path); a=p.parse_args()
 try:
  d=json.loads(a.inputs.read_text()); rows=list(csv.DictReader((ROOT/'outputs/operating_resources.csv').open())); matches=[r for r in rows if all(r[k]==str(d.get(k)) for k in ('method','scenario','reinvest'))]
  if len(matches)!=1: raise ValueError('Select exactly one method, scenario and reinvest (JSON boolean)')
  if not any(d.get(k) is not None for k in FIELDS): raise ValueError('Enable at least one module with explicit inputs')
  result=calculate(matches[0],d)
 except (ValueError,TypeError,KeyError,OSError) as e: p.error(str(e))
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+'\n'); print(a.output)
if __name__=='__main__': main()
