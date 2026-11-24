"""Add route-specific walking impact overlays to an R Leaflet widget, without touching tiles."""
import argparse,json,html,math,re
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,default=Path('index.html'));p.add_argument('--geojson',type=Path,required=True);a=p.parse_args();features=json.loads(a.geojson.read_text())['features'];raw=a.map.read_text();m=re.search(r'(<script\b[^>]*type="application/json"[^>]*>)(.*?)(</script>)',raw,re.S);assert m,'Widget JSON missing';payload=json.loads(m[2]);calls=payload['x']['calls'];groups={};
    for f in features:
        r=f['properties'];groups.setdefault((r['scenario'],r['route']),[]).append(f)
    names=[];new=[];legends=[];shared={}
    old_calls=list(calls)
    for (scenario,route),fs in groups.items():
        for metric in ['Walking Distance','Affected Ridership','Walking Burden']:
            name=f'{scenario} - Route {route} - {metric}';names.append(name);lat=[];lon=[];radii=[];colors=[];pop=[]
            for f in fs:
                r=f['properties'];x,y=f['geometry']['coordinates'];lat.append(y);lon.append(x);d=r['mean_distance_ft'];activity=r['activity'];hours=r['walking_hours'];color='#eab308' if d<=330 else '#f97316' if d<=660 else '#dc2626';radius=7 if metric=='Walking Distance' else min(35,2*math.sqrt(activity)) if metric=='Affected Ridership' else min(40,9*math.sqrt(hours));radii.append(radius);colors.append(color if metric=='Walking Distance' else '#2563eb' if metric=='Affected Ridership' else '#b91c1c');esc=html.escape
                targets=''.join(f"<li>{esc(t['name'])} ({esc(t['stop_id'])}): {t['share']:.1%}, {t['distance_ft']:.0f} ft, {t['walking_minutes']:.2f} min</li>" for t in r['targets']);pop.append(f"<b>{esc(name)}</b><br>{esc(r['name'])} · {esc(r['direction'])} · {esc(r['stop_id'])}<br>Boardings: {r['boardings']:.1f}; alightings: {r['alightings']:.1f}<br>Mean added walk: {d:.0f} ft<br>Daily walking burden: {hours:.2f} passenger-hours<br>Scope: {esc(r['scope'])}<ul>{targets}</ul>Along-route distance proxy; not pedestrian network distance.")
            new.append(dict(method='addCircleMarkers',args=[lat,lon,radii,None,name,dict(interactive=True,color=colors,fillColor=colors,stroke=True,weight=1,opacity=.85,fill=True,fillOpacity=.28 if metric=='Walking Burden' else .65),None,None,pop,None,None,None,None]))
            if metric=='Walking Distance':
                colors_key=['#eab308','#f97316','#dc2626']; labels=['0–330 ft','Over 330–660 ft','Over 660 ft']
            else:
                values=[10,50,100,306.25] if metric=='Affected Ridership' else [0.25,1,4,1600/81]
                color='#2563eb' if metric=='Affected Ridership' else '#b91c1c'
                opacity=.65 if metric=='Affected Ridership' else .28
                colors_key=['transparent']*len(values);labels=[]
                for i,v in enumerate(values):
                    radius=min(35,2*math.sqrt(v)) if metric=='Affected Ridership' else min(40,9*math.sqrt(v))
                    label=(f'{v:g}' if i<len(values)-1 else ('≥306.25 (size cap)' if metric=='Affected Ridership' else '≥19.75 approx. (size cap)'))
                    labels.append(f'<span style="display:inline-flex;align-items:center;gap:6px;min-height:{2*radius+2:.2f}px"><span style="display:inline-block;box-sizing:border-box;width:{2*radius:.2f}px;height:{2*radius:.2f}px;border-radius:50%;background:{color};background:rgba({37 if metric=="Affected Ridership" else 185},{99 if metric=="Affected Ridership" else 28},{235 if metric=="Affected Ridership" else 28},{opacity});border:1px solid {color}"></span>{label}</span>')
            units={'Walking Distance':'Mean added walk (ft)','Affected Ridership':'Daily boardings + alightings','Walking Burden':'Daily passenger-hours added'}[metric]
            lid='impact-legend:'+metric
            if metric not in shared:
                shared[metric]=[]
                legends.append(dict(method='addLegend',args=[dict(position='bottomleft',type='categorical',colors=colors_key,labels=labels,opacity=.65 if metric=='Walking Distance' else 1,title=html.escape(metric)+'<br>'+units,className='info legend',layerId=lid)]))
            shared[metric].append(name)
    calls[:]=[c for c in calls if not(c['method']=='addLegend' and c['args'][0].get('layerId','').startswith('impact-legend:'))]
    calls[:]=[c for c in calls if not(c['method']=='addCircleMarkers' and c['args'][4] in names)];idx=next(i for i,c in enumerate(calls) if c['method']=='addLayersControl');calls[idx:idx]=new
    for c in calls:
        if c['method']=='addLayersControl':c['args'][1][:]=[g for g in c['args'][1] if g not in names];c['args'][1][1:1]=names
        if c['method']=='hideGroup':c['args'][0][:]=[g for g in c['args'][0] if g not in names]+names
    if not any(c['method']=='hideGroup' for c in calls):calls.append(dict(method='hideGroup',args=[names]))
    # Register legends before hideGroup so initial overlay removal hides them.
    idx=next(i for i,c in enumerate(calls) if c['method']=='hideGroup')
    calls[idx:idx]=legends
    # Remove old per-layer hide calls; shared controls are managed after render.
    calls[:]=[c for c in calls if not(c['method']=='removeControl' and str(c['args'][0]).startswith('impact-legend:'))]
    hook = """function(el,x){
      /* shared-impact-legends */
      var map=this.getMap(), groups=GROUPS;
      var entries=Object.keys(groups).map(function(metric){
        var id='impact-legend:'+metric;
        return {id:id,control:map.controls.get(id),names:groups[metric]};
      });
      function sync(){
        entries.forEach(function(e){
          var visible=e.names.some(function(name){
            var layer=map.layerManager.getLayerGroup(name,false);
            return layer && map.hasLayer(layer);
          });
          if(visible && !map.controls.get(e.id)) map.controls.add(e.control,e.id);
          if(!visible && map.controls.get(e.id)) map.controls.remove(e.id);
        });
      }
      map.on('layeradd layerremove overlayadd overlayremove groupadd groupremove',sync);
      sync();
    }""".replace('GROUPS',json.dumps(shared))
    if not payload.get('jsHooks'): payload['jsHooks']={}
    hooks=payload.setdefault('jsHooks',{}).setdefault('render',[])
    hooks[:]=[h for h in hooks if 'shared-impact-legends' not in h.get('code','')]
    hooks.append(dict(code=hook,data=None))
    # R widget eval paths use positional call indices; rebuild easy-button paths.
    evals=[]
    for path in payload.get('evals',[]):
        match=re.match(r'calls\.(\d+)\.(.*)',path)
        if not match: evals.append(path);continue
        suffix=match[2]
        if suffix.endswith('.onClick') and any(c['method']=='addEasyButton' for c in calls):
            continue
        old=old_calls[int(match[1])]
        if old in calls: evals.append(f'calls.{calls.index(old)}.{suffix}')
    for i,c in enumerate(calls):
        if c['method']=='addEasyButton':
            for j,arg in enumerate(c['args']):
                if isinstance(arg,dict) and isinstance(arg.get('onClick'),str): evals.append(f'calls.{i}.args.{j}.onClick')
    payload['evals']=list(dict.fromkeys(evals))
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/');a.map.write_text(raw[:m.start(2)]+encoded+raw[m.end(2):]);print('Updated',len(names),'layers')
if __name__=='__main__':main()
