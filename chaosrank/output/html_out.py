"""HTML dashboard output renderer for ChaosRank."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


def render_html(
    ranked: list[dict],
    G: "nx.DiGraph | None" = None,
    top_n: int | None = None,
    alpha: float = 0.6,
    beta: float = 0.4,
) -> str:
    """Render ranked services as a self-contained HTML dashboard.

    Returns the full HTML string. Caller is responsible for writing to a file
    or printing to stdout.
    """
    rows = ranked[:top_n] if top_n else ranked

    # Build node list from ranked data
    nodes_data = [
        {
            "id":    r["service"],
            "risk":  r["risk"],
            "br":    r["blast_radius"],
            "fr":    r["fragility"],
            "rank":  r["rank"],
            "fault": r["suggested_fault"],
            "conf":  r["confidence"],
            "ghost": r["service"].startswith("ghost-"),
        }
        for r in rows
    ]

    # Build edge list from graph if provided
    edges_data: list[dict] = []
    if G is not None:
        service_ids = {r["service"] for r in rows}
        for u, v, data in G.edges(data=True):
            if u in service_ids and v in service_ids:
                edges_data.append({
                    "source": u,
                    "target": v,
                    "type":   data.get("edge_type", "sync"),
                    "weight": round(data.get("weight", 1.0), 4),
                })

    # Top service info for the experiment card
    top = rows[0] if rows else {}
    top2 = rows[1] if len(rows) > 1 else {}

    nodes_json = json.dumps(nodes_data, indent=2)
    edges_json = json.dumps(edges_data, indent=2)
    alpha_str  = f"{alpha:.1f}"
    beta_str   = f"{beta:.1f}"
    total      = len(ranked)
    shown      = len(rows)
    node_count = shown
    edge_count = len(edges_data)

    top_service  = top.get("service", "N/A")
    top_risk     = f"{top.get('risk', 0):.3f}"
    top_br       = f"{top.get('blast_radius', 0):.3f}"
    top_fr       = f"{top.get('fragility', 0):.3f}"
    top_fault    = top.get("suggested_fault", "N/A")
    top_conf     = top.get("confidence", "N/A")
    top2_service = top2.get("service", "N/A")
    top2_risk    = f"{top2.get('risk', 0):.3f}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChaosRank — {top_service} · Risk Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow:wght@300;400;600;700&family=Barlow+Condensed:wght@700;800&display=swap');
  :root {{
    --bg:#0a0c0f;--bg2:#0f1318;--bg3:#151b22;--border:#1e2a35;
    --accent:#00e5ff;--danger:#ff3b3b;--warn:#ffaa00;--ok:#00e676;
    --muted:#3a4a58;--text:#c8d8e8;--text-dim:#5a7080;
    --mono:'Share Tech Mono',monospace;
    --sans:'Barlow',sans-serif;
    --condensed:'Barlow Condensed',sans-serif;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;overflow-x:hidden}}
  body::before{{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.08) 2px,rgba(0,0,0,.08) 4px);pointer-events:none;z-index:1000}}
  header{{display:flex;align-items:center;justify-content:space-between;padding:18px 32px;border-bottom:1px solid var(--border);background:var(--bg2);position:sticky;top:0;z-index:100}}
  .logo{{display:flex;align-items:baseline;gap:12px}}
  .logo-text{{font-family:var(--condensed);font-size:28px;font-weight:800;letter-spacing:3px;color:#fff;text-transform:uppercase}}
  .logo-text span{{color:var(--accent)}}
  .logo-sub{{font-family:var(--mono);font-size:10px;color:var(--text-dim);letter-spacing:2px;text-transform:uppercase}}
  .header-right{{display:flex;align-items:center;gap:24px}}
  .status-pill{{display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;letter-spacing:1px;color:var(--ok);text-transform:uppercase}}
  .pulse{{width:8px;height:8px;border-radius:50%;background:var(--ok);animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(.8)}}}}
  .ts{{font-family:var(--mono);font-size:11px;color:var(--text-dim)}}
  .main{{display:grid;grid-template-columns:1fr 360px;grid-template-rows:auto auto;gap:1px;background:var(--border);min-height:calc(100vh - 65px)}}
  .panel{{background:var(--bg);padding:24px;animation:fadeIn .4s ease both}}
  .panel:nth-child(2){{animation-delay:.1s}}.panel:nth-child(3){{animation-delay:.2s}}
  @keyframes fadeIn{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
  .panel-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}}
  .panel-title{{font-family:var(--condensed);font-size:13px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--text-dim)}}
  .panel-badge{{font-family:var(--mono);font-size:10px;padding:3px 8px;border:1px solid var(--accent);color:var(--accent);letter-spacing:1px}}
  .graph-panel{{grid-column:1;grid-row:1;min-height:460px}}
  #graph-svg{{width:100%;height:420px;background:var(--bg2);border:1px solid var(--border)}}
  .node-real circle{{stroke-width:2;cursor:pointer}}
  .node-ghost circle{{stroke-dasharray:4,3;stroke-width:1.5;cursor:pointer;opacity:.6}}
  .node-label{{font-family:var(--mono);font-size:9px;fill:var(--text);text-anchor:middle;pointer-events:none}}
  .link{{stroke:var(--muted);stroke-opacity:.4;stroke-width:1;fill:none;marker-end:url(#arrow)}}
  .link.active{{stroke:var(--accent);stroke-opacity:.9;stroke-width:2}}
  .link.async-edge{{stroke-dasharray:4,3}}
  .rank-panel{{grid-column:2;grid-row:1/3;border-left:1px solid var(--border);overflow-y:auto;max-height:calc(100vh - 65px)}}
  .rank-table{{width:100%;border-collapse:collapse}}
  .rank-table th{{font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--text-dim);padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);position:sticky;top:49px;background:var(--bg);z-index:10}}
  .rank-row{{cursor:pointer;border-bottom:1px solid var(--border);transition:background .15s}}
  .rank-row:hover{{background:var(--bg3)}}
  .rank-row.selected{{background:rgba(0,229,255,.06)}}
  .rank-row.top{{background:rgba(255,59,59,.06)}}
  .rank-row.top:hover{{background:rgba(255,59,59,.1)}}
  .rank-row td{{padding:10px 12px;font-size:12px;vertical-align:middle}}
  .rank-num{{font-family:var(--mono);font-size:13px;font-weight:bold;width:28px}}
  .rank-num.r1{{color:var(--danger)}}.rank-num.r2{{color:var(--warn)}}.rank-num.r3{{color:#ffcc44}}
  .svc-name{{font-family:var(--mono);font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .svc-name.ghost{{color:var(--text-dim)}}
  .bar-cell{{width:80px}}
  .bar-track{{height:4px;background:var(--bg3);border-radius:2px;overflow:hidden}}
  .bar-fill{{height:100%;border-radius:2px;transition:width .8s cubic-bezier(.4,0,.2,1)}}
  .score-val{{font-family:var(--mono);font-size:11px;text-align:right}}
  .info-panel{{grid-column:1;grid-row:2;border-top:1px solid var(--border)}}
  .info-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}}
  .metric{{background:var(--bg2);border:1px solid var(--border);padding:14px 18px}}
  .metric-label{{font-family:var(--mono);font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--text-dim);margin-bottom:6px}}
  .metric-val{{font-family:var(--condensed);font-size:28px;font-weight:700;line-height:1}}
  .metric-val.red{{color:var(--danger)}}.metric-val.amber{{color:var(--warn)}}.metric-val.green{{color:var(--ok)}}.metric-val.cyan{{color:var(--accent)}}
  .metric-sub{{font-size:10px;color:var(--text-dim);margin-top:4px}}
  .top-card{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--danger);padding:16px 20px;margin-bottom:16px}}
  .top-card-label{{font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--text-dim);margin-bottom:8px}}
  .top-card-content{{display:grid;grid-template-columns:auto 1fr;gap:0 24px;align-items:start}}
  .top-svc{{font-family:var(--condensed);font-size:32px;font-weight:800;color:var(--danger);line-height:1;grid-row:1/3}}
  .top-detail{{font-family:var(--mono);font-size:11px;color:var(--text-dim);line-height:2}}
  .top-detail span{{color:var(--text)}}
  .legend{{display:flex;gap:20px;margin-top:10px;flex-wrap:wrap}}
  .legend-item{{display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10px;color:var(--text-dim)}}
  .legend-dot{{width:10px;height:10px;border-radius:50%}}
  .legend-ghost{{width:10px;height:10px;border-radius:50%;border:1.5px dashed var(--muted);background:transparent}}
  .tooltip{{position:absolute;background:var(--bg3);border:1px solid var(--accent);padding:10px 14px;font-family:var(--mono);font-size:11px;pointer-events:none;opacity:0;transition:opacity .15s;z-index:500;line-height:1.8;min-width:190px}}
  .tooltip.visible{{opacity:1}}
  .tooltip-title{{color:var(--accent);font-size:12px;margin-bottom:4px}}
  .tooltip-row{{color:var(--text-dim)}}.tooltip-row span{{color:var(--text)}}
  ::-webkit-scrollbar{{width:4px}}::-webkit-scrollbar-track{{background:var(--bg)}}::-webkit-scrollbar-thumb{{background:var(--border)}}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-text">Chaos<span>Rank</span></div>
    <div class="logo-sub">Risk-Driven Experiment Scheduler · v0.3.1</div>
  </div>
  <div class="header-right">
    <div class="status-pill"><div class="pulse"></div>Report Generated</div>
    <div class="ts" id="ts"></div>
  </div>
</header>
<div class="main">

  <div class="panel graph-panel">
    <div class="panel-header">
      <div class="panel-title">Dependency Graph — Risk Overlay</div>
      <div class="panel-badge">{node_count} nodes · {edge_count} edges</div>
    </div>
    <svg id="graph-svg"></svg>
    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:var(--danger)"></div>Critical ≥0.85</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--warn)"></div>High ≥0.70</div>
      <div class="legend-item"><div class="legend-dot" style="background:#4fc3f7"></div>Medium ≥0.45</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--ok)"></div>Low</div>
      <div class="legend-item"><div class="legend-ghost"></div>Ghost (synthetic)</div>
    </div>
  </div>

  <div class="panel rank-panel">
    <div class="panel-header">
      <div class="panel-title">Ranked Services</div>
      <div class="panel-badge">α={alpha_str} β={beta_str}</div>
    </div>
    <table class="rank-table">
      <thead><tr><th>#</th><th>Service</th><th>Risk</th><th>Bar</th></tr></thead>
      <tbody id="rank-tbody"></tbody>
    </table>
  </div>

  <div class="panel info-panel">
    <div class="panel-header">
      <div class="panel-title">Top Target — Experiment Recommendation</div>
      <div class="panel-badge" style="color:var(--danger);border-color:var(--danger)">PRIORITY #1</div>
    </div>
    <div class="top-card">
      <div class="top-card-label">Highest Risk Service</div>
      <div class="top-card-content">
        <div class="top-svc">{top_service}</div>
        <div class="top-detail">
          Risk Score: <span style="color:var(--danger)">{top_risk}</span> &nbsp;·&nbsp;
          Fault: <span>{top_fault}</span> &nbsp;·&nbsp;
          Confidence: <span>{top_conf}</span>
        </div>
        <div class="top-detail">
          Blast Radius: <span>{top_br}</span> &nbsp;·&nbsp;
          Fragility: <span>{top_fr}</span> &nbsp;·&nbsp;
          Runner-up: <span>{top2_service} ({top2_risk})</span>
        </div>
      </div>
    </div>
    <div class="info-grid">
      <div class="metric"><div class="metric-label">Total Services</div><div class="metric-val cyan">{total}</div><div class="metric-sub">in dependency graph</div></div>
      <div class="metric"><div class="metric-label">Showing</div><div class="metric-val cyan">{shown}</div><div class="metric-sub">ranked services</div></div>
      <div class="metric"><div class="metric-label">Graph Edges</div><div class="metric-val cyan">{edge_count}</div><div class="metric-sub">dependency calls</div></div>
      <div class="metric"><div class="metric-label">Alpha (α)</div><div class="metric-val amber">{alpha_str}</div><div class="metric-sub">blast radius weight</div></div>
      <div class="metric"><div class="metric-label">Beta (β)</div><div class="metric-val amber">{beta_str}</div><div class="metric-sub">fragility weight</div></div>
      <div class="metric"><div class="metric-label">Top Risk</div><div class="metric-val red">{top_risk}</div><div class="metric-sub">{top_service}</div></div>
    </div>
  </div>

</div>
<div class="tooltip" id="tooltip"></div>
<script>
const NODES = {nodes_json};
const EDGES = {edges_json};

function riskColor(r) {{
  if (r >= 0.85) return "#ff3b3b";
  if (r >= 0.70) return "#ffaa00";
  if (r >= 0.45) return "#4fc3f7";
  return "#00e676";
}}
function riskBar(r) {{
  if (r >= 0.85) return "linear-gradient(90deg,#ff3b3b,#ff6b6b)";
  if (r >= 0.70) return "linear-gradient(90deg,#ffaa00,#ffcc44)";
  if (r >= 0.45) return "linear-gradient(90deg,#4fc3f7,#81d4fa)";
  return "linear-gradient(90deg,#00e676,#69f0ae)";
}}
function shortName(id) {{ return id.replace("ghost-","g-").replace("service","svc"); }}

// timestamp
function tick() {{ document.getElementById("ts").textContent = new Date().toISOString().replace("T"," ").slice(0,19)+" UTC"; }}
tick(); setInterval(tick, 1000);

// rank table
const tbody = document.getElementById("rank-tbody");
NODES.forEach((s,i) => {{
  const tr = document.createElement("tr");
  tr.className = "rank-row"+(i===0?" top":"");
  tr.dataset.id = s.id;
  const rc = i===0?"r1":i===1?"r2":i===2?"r3":"";
  tr.innerHTML = `
    <td class="rank-num ${{rc}}">${{s.rank}}</td>
    <td><div class="svc-name ${{s.ghost?'ghost':''}}" title="${{s.id}}">${{shortName(s.id)}}</div></td>
    <td class="score-val" style="color:${{riskColor(s.risk)}}">${{s.risk.toFixed(3)}}</td>
    <td class="bar-cell"><div class="bar-track"><div class="bar-fill" style="width:0%;background:${{riskBar(s.risk)}}" data-w="${{s.risk*100}}"></div></div></td>`;
  tr.addEventListener("click", () => highlight(s.id));
  tbody.appendChild(tr);
}});
setTimeout(() => document.querySelectorAll(".bar-fill").forEach(el => el.style.width=el.dataset.w+"%"), 200);

// graph
const svg = d3.select("#graph-svg");
const el  = document.getElementById("graph-svg");
const W   = el.getBoundingClientRect().width || 700;
const H   = 420;
svg.attr("viewBox",`0 0 ${{W}} ${{H}}`);
svg.append("defs").append("marker").attr("id","arrow").attr("viewBox","0 -4 8 8").attr("refX",18).attr("refY",0).attr("markerWidth",6).attr("markerHeight",6).attr("orient","auto").append("path").attr("d","M0,-4L8,0L0,4").attr("fill","#3a4a58");

const nodeMap = {{}};
const nodes = NODES.map(s => {{nodeMap[s.id]=s; return {{...s}}}});
const links = EDGES.map(e => {{
  const si = nodes.findIndex(n=>n.id===e.source);
  const ti = nodes.findIndex(n=>n.id===e.target);
  return si>=0&&ti>=0 ? {{source:si,target:ti,type:e.type}} : null;
}}).filter(Boolean);

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).distance(65).strength(0.4))
  .force("charge", d3.forceManyBody().strength(-200))
  .force("center", d3.forceCenter(W/2,H/2))
  .force("collision", d3.forceCollide(24))
  .force("x", d3.forceX(W/2).strength(0.03))
  .force("y", d3.forceY(H/2).strength(0.04));

const g = svg.append("g");
svg.call(d3.zoom().scaleExtent([0.3,2.5]).on("zoom",e=>g.attr("transform",e.transform)));

const linkSel = g.append("g").selectAll("line").data(links).join("line")
  .attr("class", d => "link"+(d.type==="async"?" async-edge":""));

const tooltip = document.getElementById("tooltip");
const nodeSel = g.append("g").selectAll("g").data(nodes).join("g")
  .attr("class", d => d.ghost?"node-ghost":"node-real")
  .call(d3.drag()
    .on("start",(e,d)=>{{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;}})
    .on("drag", (e,d)=>{{d.fx=e.x;d.fy=e.y;}})
    .on("end",  (e,d)=>{{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}));

nodeSel.append("circle")
  .attr("r", d => d.ghost ? 7 : 6+d.risk*12)
  .attr("fill", d => d.ghost?"transparent":riskColor(d.risk)+"22")
  .attr("stroke", d => d.ghost?"#3a4a58":riskColor(d.risk));

nodeSel.append("text").attr("class","node-label")
  .attr("dy", d => -(9+(d.ghost?7:6+d.risk*12)))
  .text(d => shortName(d.id));

nodeSel
  .on("mouseover",(e,d)=>{{
    tooltip.innerHTML=`<div class="tooltip-title">${{d.id}}</div><div class="tooltip-row">Risk: <span style="color:${{riskColor(d.risk)}}">${{d.risk.toFixed(3)}}</span></div><div class="tooltip-row">Blast Radius: <span>${{d.br.toFixed(3)}}</span></div><div class="tooltip-row">Fragility: <span>${{d.fr.toFixed(3)}}</span></div><div class="tooltip-row">Fault: <span>${{d.fault}}</span></div><div class="tooltip-row">Confidence: <span>${{d.conf}}</span></div><div class="tooltip-row">Type: <span>${{d.ghost?'Ghost (synthetic)':'Real service'}}</span></div>`;
    tooltip.classList.add("visible");
  }})
  .on("mousemove",e=>{{tooltip.style.left=(e.pageX+14)+"px";tooltip.style.top=(e.pageY-10)+"px";}})
  .on("mouseout",()=>tooltip.classList.remove("visible"))
  .on("click",(e,d)=>highlight(d.id));

sim.on("tick",()=>{{
  linkSel.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  nodeSel.attr("transform",d=>`translate(${{Math.max(20,Math.min(W-20,d.x))}},${{Math.max(20,Math.min(H-20,d.y))}})`);}});

function highlight(id) {{
  linkSel.classed("active", d => {{
    const s = typeof d.source==='object'?d.source.id:nodes[d.source].id;
    const t = typeof d.target==='object'?d.target.id:nodes[d.target].id;
    return s===id||t===id;
  }});
  document.querySelectorAll(".rank-row").forEach(r=>r.classList.toggle("selected",r.dataset.id===id));
  const row = document.querySelector(`.rank-row[data-id="${{id}}"]`);
  if(row) row.scrollIntoView({{block:"nearest",behavior:"smooth"}});
}}
</script>
</body>
</html>"""