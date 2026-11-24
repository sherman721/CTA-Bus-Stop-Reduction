#!/usr/bin/env python3
"""Build the research workbook from current analysis outputs, using the approved layout."""
import csv, json, argparse
from pathlib import Path
from copy import copy
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.chart import Reference
from openpyxl.chart.series import SeriesLabel
ROOT=Path(__file__).resolve().parent
O=ROOT/'outputs'
N={'cta':'CTA Consolidation','proposed':'New Consolidation','original':'Original','anchored':'GTFS-anchored','standalone':'Standalone','field':'Field-informed'}
def read(name):
    with (O/(name+'.csv')).open(newline='') as f: return list(csv.DictReader(f))
def value(x):
    if x in N: return N[x]
    if x in ('True','False'): return x=='True'
    try: return float(x) if any(c in x.lower() for c in '.e') else int(x)
    except (ValueError,TypeError): return x

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,default=O/'CTA_Results.xlsx'); args=p.parse_args()
    cfg=json.loads((ROOT/'config.json').read_text()); w=load_workbook(ROOT/'results_template.xlsx')
    def put(name,rows):
        s=w[name]; rows=list(rows); styles=[[copy(s.cell(i,j)._style) for j in range(1,s.max_column+1)] for i in (5,6)]; heights=[s.row_dimensions[i].height for i in (5,6)]
        if s.max_row>=5: s.delete_rows(5,s.max_row-4)
        for i,row in enumerate(rows,5):
            for j,v in enumerate(row,1):
                c=s.cell(i,j,v); c._style=copy(styles[(i-5)%2][j-1]);
                if isinstance(v,str) and v.startswith('='): c.data_type='s'
            s.row_dimensions[i].height=heights[(i-5)%2]
        if s.auto_filter.ref: s.auto_filter.ref=f'A4:{s.cell(max(4,4+len(rows)),s.max_column).coordinate}'
        return s
    def table(name,file,keys=None,transform=None):
        rr=read(file); rows=[]
        for r in rr:
            row=[value(r[k]) for k in (keys or list(r))]; rows.append(transform(r,row) if transform else row)
        return put(name,rows)
    inv=read('scenario_summary'); base={(r['scope'],r['direction']):float(r['walking_passenger_hours']) for r in inv if r['scenario']=='original'}
    table('Inventory','scenario_summary',transform=lambda r,a:a[:-1]+[float(r['walking_passenger_hours'])-base[r['scope'],r['direction']]])
    table('Schedule','full_schedule_summary'); table('Running time','travel_summary'); table('Passenger time','passenger_impacts'); table('Reinvestment','service_reinvestment'); table('Break even','break_even'); table('Field measurements','field_summary'); table('Field stopping','field_stopping_check'); table('Operations','operating_resources',transform=lambda r,a:a[:-1]+[float(r['fuel_per_mile_reduction_required_pct'])]); table('Walking distribution','walking_distribution');
    keys=['scenario','direction','stop_id','name','boardings','alightings','activity','mean_distance_ft','walking_hours','targets']; table('Affected stops','stop_impacts',keys)
    rr=read('stop_impacts'); scope=rr[0]['scope'] if rr else 'permanent'; rows=[]
    for scenario in dict.fromkeys(r['scenario'] for r in rr):
        a=[r for r in rr if r['scenario']==scenario]; b=sum(float(r['boardings']) for r in a); c=sum(float(r['alightings']) for r in a); h=sum(float(r['walking_hours']) for r in a); rows.append([value(scenario),len(a),b,c,b+c,h,sum(float(r['activity'])*float(r['mean_distance_ft']) for r in a)/(b+c) if b+c else 0])
    put('Walking summary',rows)
    for name in ('Walking summary','Walking distribution','Affected stops'):
        w[name]['A2']=w[name]['A2'].value.replace('Permanent inventory',scope.capitalize()+' inventory')
    pp=read('passenger_impacts'); rr=read('service_reinvestment'); rows=[]
    for r in rr:
        fixed=sum(float(x['net_passenger_hours_saved']) for x in pp if (x['method'],x['scenario'])==(r['method'],r['scenario'])); rows.append([value(r['method']),value(r['scenario']),fixed,float(r['net_hours_saved']),float(r['baseline_cycle_min'])-float(r['fixed_frequency_cycle_min']),float(r['frequency_multiplier'])-1,float(r['walk_hours_added'])])
    s=put('Summary',rows); end=4+len(rows); start=end+2
    for j,v in enumerate(['Method / scenario','Unchanged frequency','Frequency reinvestment'],1):
        c=s.cell(start,j,v); c._style=copy(s.cell(4,j)._style)
    for i,r in enumerate(rows,start+1):
        for j,v in enumerate([r[0]+' / '+('CTA' if r[1]=='CTA Consolidation' else 'New'),r[2],r[3]],1):
            c=s.cell(i,j,v); c._style=copy(s.cell(5,j)._style); c.number_format='0.00' if j>1 else 'General'
    s.cell(start+len(rows)+1,1,'Negative = additional passenger travel time; walking is included in both series.')
    chart=s._charts[0]; chart.series=[]; chart.add_data(Reference(s,min_col=2,max_col=3,min_row=start,max_row=start+len(rows)),titles_from_data=True); chart.set_categories(Reference(s,min_col=1,min_row=start+1,max_row=start+len(rows)))
    for series,color in zip(chart.series,('156082','E97132')):
        series.invertIfNegative=False; series.graphicalProperties.solidFill=color; series.graphicalProperties.line.solidFill=color
    chart.x_axis.tickLblPos='low'; chart.anchor=f'A{start+len(rows)+3}'; s.freeze_panes=None
    # Use live configuration and source register, retaining the approved explanatory notes.
    notes=[list(row) for row in w['Assumptions'].iter_rows(min_row=5,values_only=True) if row[0] not in cfg and row[0] is not None]
    put('Assumptions',[[k,json.dumps(v) if isinstance(v,(dict,list)) else v,'Configured value; see methodology'] for k,v in cfg.items()]+notes)
    put('Sources',[[l.lstrip('- ')] for l in (ROOT/'SOURCES.md').read_text().splitlines() if l.strip() and not l.startswith('#')])
    v=json.loads((O/'phase2_validation.json').read_text()); put('Verification', [['Core analysis outputs','Generated','Workbook stage reached after core scripts'],['Demand conservation','Passed' if v['demand_conservation_passed'] else 'Failed','phase2_validation.json'],['Full-length trips',json.dumps(v['full_trips']),'phase2_validation.json'],['Reinvestment',f'{len(rr)} rows','service_reinvestment.csv'],['Automated validation','Deferred','QA-001 backlog'],['Source audit','Pending','Working source register']])
    for sheet in w:
        sheet['A1']=sheet.title.upper()+'  |  ROUTE '+str(cfg['route'])
    date=datetime.strptime(str(cfg['service_date']),'%Y%m%d').strftime('%B %d, %Y'); w['Schedule']['A2']=f'Scheduled weekday service from {date} GTFS. Full-length trips only; short trips excluded. Periods use departure time. Other combines early and late service. Source: full_schedule_summary.csv.'
    args.output.parent.mkdir(parents=True,exist_ok=True); temp=args.output.with_suffix('.tmp.xlsx'); w.save(temp); temp.replace(args.output); print(f'Workbook generated: {args.output}')
if __name__=='__main__': main()
