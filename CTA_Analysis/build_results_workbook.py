#!/usr/bin/env python3
"""Build the research workbook from current analysis outputs, using the approved layout."""
import csv, json, argparse
from pathlib import Path
from copy import copy
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.chart.series import SeriesLabel
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.legend import LegendEntry
from openpyxl.chart.label import DataLabelList, DataLabel
from openpyxl.chart.marker import DataPoint, Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import Paragraph, ParagraphProperties, CharacterProperties, RichTextProperties
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
    def sensitivity():
        curves=read('stopping_sensitivity'); thresholds=read('break_even')
        methods=['anchored','standalone','field']; combos=[('cta','False'),('cta','True'),('proposed','False'),('proposed','True')]
        lookup={(r['method'],r['scenario'],r['reinvest'],float(r['alpha'])):r for r in curves}
        roots={(r['method'],r['scenario'],r['reinvest']):r for r in thresholds}
        alphas=sorted({float(r['alpha']) for r in curves})
        if len(lookup)!=len(curves) or len(lookup)!=len(methods)*len(combos)*len(alphas) or alphas[0]!=0 or alphas[-1]!=1: raise ValueError('Incomplete or duplicate sensitivity grid')
        sh=w.create_sheet('Break even sensitivity',w.sheetnames.index('Break even')+1)
        sh.merge_cells('A1:G1'); sh.merge_cells('A2:G2'); sh['A2']='Alpha = share of otherwise skipped events served, not a percentage-point increase. Mean probability excludes terminals and uses the original inventory.'; sh['A2'].alignment=Alignment(wrap_text=True,vertical='center'); sh.row_dimensions[2].height=36
        sh.freeze_panes='A4'; sh.sheet_view.showGridLines=False
        for j,width in enumerate([19,24,23,23,23,23,18],1): sh.column_dimensions[get_column_letter(j)].width=width
        labels=['CTA fixed','CTA reinvestment','New fixed','New reinvestment']; colors=['B16A28','B16A28','17618A','17618A']
        def header(row,vals):
            for j,v in enumerate(vals,1):
                cell=sh.cell(row,j,v); cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='156082'); cell.alignment=Alignment(wrap_text=True,vertical='center')
            sh.row_dimensions[row].height=32
        for k,m in enumerate(methods):
            start=5+k*38; sh.cell(start-1,1,N[m]+' — stopping sensitivity').font=Font(bold=True,size=14,color='156082'); header(start,['Alpha','Original mean probability',*labels,'Zero reference'])
            for i,x in enumerate(alphas,start+1):
                rr=[lookup[m,a,b,x] for a,b in combos]; vals=[x,float(rr[0]['original_mean_probability']),*[float(r['net_hours_saved']) for r in rr],0]
                for j,v in enumerate(vals,1): sh.cell(i,j,v).number_format='0.00%' if j<=2 else '0.00'
            end=start+len(alphas)
            chart=ScatterChart(); chart.title=N[m]+' — stopping sensitivity'
            chart.x_axis.title='Otherwise skipped stop events served (α)'
            chart.y_axis.title='Net passenger-hours saved per weekday'
            chart.x_axis.scaling.min=0; chart.x_axis.scaling.max=1; chart.x_axis.majorUnit=0.1
            chart.x_axis.numFmt='0%'; chart.y_axis.numFmt='0'
            def text_style(size):
                return RichText(bodyPr=RichTextProperties(),p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(sz=size*100)),endParaRPr=CharacterProperties(sz=size*100))])
            for axis,pos in ((chart.x_axis,'b'),(chart.y_axis,'l')):
                axis.axPos=pos; axis.delete=False; axis.tickLblPos='low'; axis.majorTickMark='out'; axis.crosses='min'; axis.txPr=text_style(11)
                axis.majorGridlines.spPr=GraphicalProperties(); axis.majorGridlines.spPr.line.solidFill='DDDDDD'
            chart.width=29; chart.height=18; chart.scatterStyle='lineMarker'
            chart.layout=Layout(manualLayout=ManualLayout(layoutTarget='inner',xMode='edge',yMode='edge',wMode='factor',hMode='factor',x=0.15,y=0.12,w=0.80,h=0.60))
            chart.legend.position='b'; chart.legend.overlay=False; chart.legend.txPr=text_style(11)
            chart.legend.layout=Layout(manualLayout=ManualLayout(xMode='edge',yMode='edge',wMode='factor',hMode='factor',x=0.10,y=0.86,w=0.85,h=0.11))
            for j,label in enumerate(labels+['Break even'],3):
                ser=Series(Reference(sh,min_col=j,min_row=start+1,max_row=end),Reference(sh,min_col=1,min_row=start+1,max_row=end),title=label); ser.graphicalProperties.line.solidFill=colors[j-3] if j<7 else '777777'; ser.graphicalProperties.line.prstDash='dash' if j in (3,5) else 'solid'; ser.marker.symbol='none'; chart.series.append(ser)
            h=end+2; header(h,['Scenario / service','Alpha threshold','Base mean probability','Threshold mean probability','Increase (pp)','Net hours saved','Status'])
            for i,((a,b),label,color) in enumerate(zip(combos,labels,colors),h+1):
                r=roots[m,a,b]; base=float(lookup[m,a,b,0]['original_mean_probability']); adj=float(r['original_mean_probability']); valid=r['status']!='no crossing found'; vals=[label,float(r['alpha']) if valid else None,base,adj if valid else None,100*(adj-base) if valid else None,float(r['net_hours_saved']) if valid else None,r['status']]
                for j,v in enumerate(vals,1): sh.cell(i,j,v).number_format='0.00%' if j in (2,3,4) else '0.00' if j in (5,6) else 'General'
                if r['status']=='crossing':
                    idx=i-(h+1); root=float(r['alpha']); points=sorted([(x,float(lookup[m,a,b,x]['net_hours_saved']),False) for x in alphas if abs(x-root)>1e-10]+[(root,0.0,True)])
                    xc=20+2*idx; yc=xc+1
                    sh.cell(start,xc,label+' alpha'); sh.cell(start,yc,label+' net hours')
                    for offset,(x,y,is_root) in enumerate(points):
                        sh.cell(start+1+offset,xc,x); sh.cell(start+1+offset,yc,y)
                        if is_root: root_idx=offset
                    ser=chart.series[idx]
                    ser.xVal.numRef.f=Reference(sh,min_col=xc,min_row=start+1,max_row=start+len(points)).__str__()
                    ser.yVal.numRef.f=Reference(sh,min_col=yc,min_row=start+1,max_row=start+len(points)).__str__()
                    marker=Marker(symbol='circle',size=5); marker.graphicalProperties.solidFill=color; marker.graphicalProperties.line.solidFill=color
                    ser.dPt=[DataPoint(idx=root_idx,marker=marker)]
                    flags=dict(showLegendKey=False,showCatName=False,showVal=False,showSerName=False,showPercent=False,showBubbleSize=False)
                    ser.dLbls=DataLabelList(**flags)
                    ser.dLbls.dLbl=[DataLabel(idx=root_idx,showLegendKey=False,showCatName=True,showVal=False,showSerName=False,showPercent=False,showBubbleSize=False,numFmt='0.00%',dLblPos='t' if (a,b) in (('cta','True'),('proposed','False')) else 'b',txPr=text_style(10))]
            sh.add_chart(chart,f'I{start}')
        sh['A1'].font=Font(bold=True,size=16,color='156082')
    sensitivity()
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
    w['Break even']['A2']='Alpha is the fraction of otherwise skipped events served: adjusted p = p + alpha × (1 − p). Original mean stopping probability is the adjusted mean at the threshold, excluding terminals. See Break even sensitivity for baseline and percentage-point change.'
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
