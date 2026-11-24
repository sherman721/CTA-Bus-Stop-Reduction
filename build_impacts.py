"""Add route-specific walking impact overlays to an R Leaflet widget, without touching tiles."""
import argparse,json,html,math,re
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,default=Path('index.html'));p.add_argument('--geojson',type=Path,required=True);a=p.parse_args();features=json.loads(a.geojson.read_text())['features'];raw=a.map.read_text();m=re.search(r'(<script\b[^>]*type="application/json"[^>]*>)(.*?)(</script>)',raw,re.S);assert m,'Widget JSON missing';payload=json.loads(m[2]);calls=payload['x']['calls'];groups={};
    for f in features:
        r=f['properties'];groups.setdefault((r['scenario'],r['route']),[]).append(f)
    names=[];new=[]
    for (scenario,route),fs in groups.items():
        for metric in ['Walking Distance','Affected Ridership','Walking Burden']:
            name=f'{scenario} - Route {route} - {metric}';names.append(name);lat=[];lon=[];radii=[];colors=[];pop=[]
            for f in fs:
                r=f['properties'];x,y=f['geometry']['coordinates'];lat.append(y);lon.append(x);d=r['mean_distance_ft'];activity=r['activity'];hours=r['walking_hours'];color='#eab308' if d<=330 else '#f97316' if d<=660 else '#dc2626';radius=7 if metric=='Walking Distance' else min(35,2*math.sqrt(activity)) if metric=='Affected Ridership' else min(40,9*math.sqrt(hours));radii.append(radius);colors.append(color if metric=='Walking Distance' else '#2563eb' if metric=='Affected Ridership' else '#b91c1c');esc=html.escape
                targets=''.join(f"<li>{esc(t['name'])} ({esc(t['stop_id'])}): {t['share']:.1%}, {t['distance_ft']:.0f} ft, {t['walking_minutes']:.2f} min</li>" for t in r['targets']);pop.append(f"<b>{esc(name)}</b><br>{esc(r['name'])} · {esc(r['direction'])} · {esc(r['stop_id'])}<br>Boardings: {r['boardings']:.1f}; alightings: {r['alightings']:.1f}<br>Mean added walk: {d:.0f} ft<br>Daily walking burden: {hours:.2f} passenger-hours<br>Scope: {esc(r['scope'])}<ul>{targets}</ul>Along-route distance proxy; not pedestrian network distance.")
            new.append(dict(method='addCircleMarkers',args=[lat,lon,radii,None,name,dict(interactive=True,color=colors,fillColor=colors,stroke=True,weight=1,opacity=.85,fill=True,fillOpacity=.28 if metric=='Walking Burden' else .65),None,None,pop,None,None,None,None]))
    calls[:]=[c for c in calls if not(c['method']=='addCircleMarkers' and c['args'][4] in names)];idx=next(i for i,c in enumerate(calls) if c['method']=='addLayersControl');calls[idx:idx]=new
    for c in calls:
        if c['method']=='addLayersControl':c['args'][1][:]=[g for g in c['args'][1] if g not in names];c['args'][1][1:1]=names
        if c['method']=='hideGroup':c['args'][0][:]=[g for g in c['args'][0] if g not in names]+names
    if not any(c['method']=='hideGroup' for c in calls):calls.append(dict(method='hideGroup',args=[names]))
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/');a.map.write_text(raw[:m.start(2)]+encoded+raw[m.end(2):]);print('Updated',len(names),'layers')
if __name__=='__main__':main()
