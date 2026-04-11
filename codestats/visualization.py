"""Interactive D3.js force-directed graph visualization.

Generates a self-contained HTML file with a dark GitHub-themed
force-directed dependency graph.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def generate_visualization(
    nodes: list[dict],
    edges: list[dict],
    communities: list[dict],
    repo_name: str,
    output_path: str,
    max_nodes: int = 500,
) -> str:
    """Generate interactive D3.js HTML visualization.

    Parameters
    ----------
    nodes : list[dict]
        Graph nodes with keys: path, language, pagerank, is_entry_point,
        is_test, symbol_count, community_id, betweenness.
    edges : list[dict]
        Graph edges with keys: source, target, edge_type.
    communities : list[dict]
        Community data with keys: community_id, name, member_count, cohesion.
    repo_name : str
        Repository name for the title.
    output_path : str
        File path to write the HTML to.
    max_nodes : int
        Maximum nodes to render (top by PageRank).

    Returns
    -------
    str
        The path to the generated HTML file.
    """
    # Sort by pagerank descending and take top N
    sorted_nodes = sorted(nodes, key=lambda n: n.get("pagerank", 0), reverse=True)
    top_nodes = sorted_nodes[:max_nodes]
    top_paths = {n["path"] for n in top_nodes}

    # Determine hotspot paths from betweenness > 75th percentile
    betweenness_values = [n.get("betweenness", 0) for n in top_nodes if n.get("betweenness", 0) > 0]
    hotspot_threshold = 0.0
    if betweenness_values:
        betweenness_values.sort()
        idx = int(len(betweenness_values) * 0.75)
        hotspot_threshold = betweenness_values[min(idx, len(betweenness_values) - 1)]

    # Build community map
    community_map: dict[int, str] = {}
    for c in communities:
        community_map[c["community_id"]] = c.get("name", f"community-{c['community_id']}")

    # Build D3 data
    d3_nodes = []
    max_pr = max((n.get("pagerank", 0) for n in top_nodes), default=1) or 1
    for n in top_nodes:
        pr = n.get("pagerank", 0)
        is_entry = bool(n.get("is_entry_point"))
        is_test = bool(n.get("is_test"))
        is_hotspot = n.get("betweenness", 0) >= hotspot_threshold and hotspot_threshold > 0

        # Determine node type for coloring
        if is_hotspot:
            node_type = "hotspot"
        elif is_entry:
            node_type = "entry"
        elif is_test:
            node_type = "test"
        else:
            node_type = "regular"

        # Node radius proportional to pagerank (5-25 range)
        radius = 5 + (pr / max_pr) * 20

        d3_nodes.append({
            "id": n["path"],
            "label": _short_label(n["path"]),
            "language": n.get("language", ""),
            "pagerank": round(pr, 6),
            "betweenness": round(n.get("betweenness", 0), 6),
            "symbols": n.get("symbol_count", 0),
            "type": node_type,
            "radius": round(radius, 1),
            "community": n.get("community_id"),
            "communityName": community_map.get(n.get("community_id", -1), ""),
            "isEntry": is_entry,
            "isTest": is_test,
        })

    # Filter edges to top nodes
    d3_edges = []
    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src in top_paths and tgt in top_paths:
            d3_edges.append({
                "source": src,
                "target": tgt,
                "type": e.get("edge_type", "IMPORTS_FROM"),
            })

    # Community list for legend
    d3_communities = []
    for c in communities:
        d3_communities.append({
            "id": c["community_id"],
            "name": c.get("name", f"community-{c['community_id']}"),
            "count": c.get("member_count", 0),
            "cohesion": round(c.get("cohesion", 0), 3),
        })

    graph_data = json.dumps({
        "nodes": d3_nodes,
        "links": d3_edges,
        "communities": d3_communities,
        "repoName": repo_name,
    })

    html = _HTML_TEMPLATE.replace("__GRAPH_DATA__", graph_data)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    log.info("Visualization written to %s", output_path)
    return str(out)


def _short_label(path: str) -> str:
    """Shorten path for display as a node label."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 2:
        return path
    return ".../" + "/".join(parts[-2:])


# ---------------------------------------------------------------------------
# HTML template with embedded D3.js
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CodeStats - Dependency Graph</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden}
#toolbar{position:fixed;top:0;left:0;right:0;height:48px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;padding:0 16px;z-index:100;gap:12px}
#toolbar h1{font-size:14px;font-weight:600;color:#f0f6fc;white-space:nowrap}
#search{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;padding:4px 10px;font-size:13px;width:240px}
#search:focus{outline:none;border-color:#58a6ff}
.btn{background:#21262d;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;padding:4px 12px;font-size:12px;cursor:pointer}
.btn:hover{background:#30363d}
.btn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
#legend{position:fixed;bottom:12px;left:12px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;font-size:12px;z-index:100}
#legend .item{display:flex;align-items:center;gap:6px;margin:3px 0}
#legend .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
#detail{position:fixed;top:60px;right:12px;width:300px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;font-size:12px;z-index:100;display:none;max-height:calc(100vh - 80px);overflow-y:auto}
#detail h3{font-size:14px;color:#f0f6fc;margin-bottom:8px;word-break:break-all}
#detail .row{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #21262d}
#detail .label{color:#8b949e}
#detail .value{color:#c9d1d9;text-align:right}
svg{width:100vw;height:100vh;position:fixed;top:48px;left:0}
.link{stroke-opacity:0.3}
.link.imports{stroke:#30363d}
.link.tested{stroke:#3fb950;stroke-dasharray:4,3}
.node-label{font-size:9px;fill:#8b949e;pointer-events:none;text-anchor:middle}
.node-label.highlight{fill:#f0f6fc;font-weight:600}
</style>
</head>
<body>
<div id="toolbar">
  <h1>CodeStats</h1>
  <input id="search" type="text" placeholder="Search files...">
  <button class="btn" id="btnCommunity">Communities</button>
  <button class="btn" id="btnReset">Reset</button>
  <span style="margin-left:auto;font-size:12px;color:#8b949e" id="stats"></span>
</div>
<div id="legend">
  <div class="item"><span class="dot" style="background:#58a6ff"></span> Regular</div>
  <div class="item"><span class="dot" style="background:#d29922"></span> Entry Point</div>
  <div class="item"><span class="dot" style="background:#3fb950"></span> Test</div>
  <div class="item"><span class="dot" style="background:#f85149"></span> Hotspot</div>
  <div style="margin-top:6px;border-top:1px solid #30363d;padding-top:6px">
    <div class="item" style="gap:8px"><svg width="20" height="2"><line x1="0" y1="1" x2="20" y2="1" stroke="#30363d" stroke-width="1.5"/></svg> imports</div>
    <div class="item" style="gap:8px"><svg width="20" height="2"><line x1="0" y1="1" x2="20" y2="1" stroke="#3fb950" stroke-width="1.5" stroke-dasharray="4,3"/></svg> tested_by</div>
  </div>
</div>
<div id="detail">
  <h3 id="detailTitle"></h3>
  <div id="detailBody"></div>
</div>
<svg id="graph"></svg>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA = __GRAPH_DATA__;
const nodeColors = {regular:"#58a6ff", entry:"#d29922", test:"#3fb950", hotspot:"#f85149"};
const communityColors = d3.scaleOrdinal(d3.schemeTableau10);
let useCommunityColor = false;

document.getElementById("stats").textContent =
  DATA.nodes.length + " nodes, " + DATA.links.length + " edges";

const svg = d3.select("#graph");
const width = window.innerWidth;
const height = window.innerHeight - 48;

const g = svg.append("g");

const zoom = d3.zoom()
  .scaleExtent([0.1, 8])
  .on("zoom", (e) => g.attr("transform", e.transform));
svg.call(zoom);

const simulation = d3.forceSimulation(DATA.nodes)
  .force("link", d3.forceLink(DATA.links).id(d => d.id).distance(80))
  .force("charge", d3.forceManyBody().strength(-120))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide().radius(d => d.radius + 2));

const link = g.append("g")
  .selectAll("line")
  .data(DATA.links)
  .join("line")
  .attr("class", d => "link " + (d.type === "TESTED_BY" ? "tested" : "imports"))
  .attr("stroke-width", 1);

const node = g.append("g")
  .selectAll("circle")
  .data(DATA.nodes)
  .join("circle")
  .attr("r", d => d.radius)
  .attr("fill", d => nodeColors[d.type] || "#58a6ff")
  .attr("stroke", "#0d1117")
  .attr("stroke-width", 1)
  .style("cursor", "pointer")
  .call(d3.drag()
    .on("start", dragStart)
    .on("drag", dragging)
    .on("end", dragEnd));

const labels = g.append("g")
  .selectAll("text")
  .data(DATA.nodes)
  .join("text")
  .attr("class", "node-label")
  .attr("dy", d => d.radius + 12)
  .text(d => d.label);

node.on("click", (event, d) => showDetail(d));

simulation.on("tick", () => {
  link
    .attr("x1", d => d.source.x)
    .attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x)
    .attr("y2", d => d.target.y);
  node
    .attr("cx", d => d.x)
    .attr("cy", d => d.y);
  labels
    .attr("x", d => d.x)
    .attr("y", d => d.y);
});

function dragStart(event, d) {
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}
function dragging(event, d) { d.fx = event.x; d.fy = event.y; }
function dragEnd(event, d) {
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null; d.fy = null;
}

// Search
const searchInput = document.getElementById("search");
searchInput.addEventListener("input", () => {
  const q = searchInput.value.toLowerCase();
  if (!q) {
    node.attr("opacity", 1);
    labels.attr("opacity", 1).attr("class", "node-label");
    link.attr("opacity", 1);
    return;
  }
  node.attr("opacity", d => d.id.toLowerCase().includes(q) ? 1 : 0.1);
  labels
    .attr("opacity", d => d.id.toLowerCase().includes(q) ? 1 : 0.05)
    .attr("class", d => d.id.toLowerCase().includes(q) ? "node-label highlight" : "node-label");
  const matchSet = new Set(DATA.nodes.filter(d => d.id.toLowerCase().includes(q)).map(d => d.id));
  link.attr("opacity", d => matchSet.has(d.source.id) || matchSet.has(d.target.id) ? 0.6 : 0.03);
});

// Community toggle
document.getElementById("btnCommunity").addEventListener("click", function() {
  useCommunityColor = !useCommunityColor;
  this.classList.toggle("active", useCommunityColor);
  node.attr("fill", d => useCommunityColor && d.community != null
    ? communityColors(d.community)
    : nodeColors[d.type] || "#58a6ff");
});

// Reset
document.getElementById("btnReset").addEventListener("click", () => {
  searchInput.value = "";
  node.attr("opacity", 1);
  labels.attr("opacity", 1).attr("class", "node-label");
  link.attr("opacity", 1);
  document.getElementById("detail").style.display = "none";
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity);
});

// Detail panel
function showDetail(d) {
  const panel = document.getElementById("detail");
  document.getElementById("detailTitle").textContent = d.id;
  const rows = [
    ["Language", d.language],
    ["Symbols", d.symbols],
    ["PageRank", d.pagerank.toFixed(6)],
    ["Betweenness", d.betweenness.toFixed(6)],
    ["Type", d.type],
    ["Entry Point", d.isEntry ? "Yes" : "No"],
    ["Test File", d.isTest ? "Yes" : "No"],
    ["Community", d.communityName || "N/A"],
  ];
  const inLinks = DATA.links.filter(l => (l.target.id || l.target) === d.id);
  const outLinks = DATA.links.filter(l => (l.source.id || l.source) === d.id);
  rows.push(["Importers", inLinks.length]);
  rows.push(["Dependencies", outLinks.length]);

  document.getElementById("detailBody").innerHTML = rows
    .map(([l, v]) => '<div class="row"><span class="label">' + l + '</span><span class="value">' + v + '</span></div>')
    .join("");
  panel.style.display = "block";
}
</script>
</body>
</html>"""
