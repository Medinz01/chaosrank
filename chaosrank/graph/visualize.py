import logging
from pathlib import Path
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)


def to_dot(
    G: nx.DiGraph,
    scores: Optional[dict[str, float]] = None,
    highlight_top_n: int = 3,
) -> str:
    """Export the dependency graph as a Graphviz DOT string, optionally highlighting top-n services by blast radius."""
    lines = [
        "digraph chaosrank {",
        '  graph [rankdir=LR, fontname="Helvetica", bgcolor="#fafafa"];',
        '  node  [fontname="Helvetica", fontsize=11, style=filled, '
        'fillcolor="#e8e8e8", shape=box, rounded=true];',
        '  edge  [fontname="Helvetica", fontsize=9, color="#666666"];',
        "",
    ]

    top_services: set[str] = set()
    if scores:
        top_services = set(sorted(scores, key=scores.get, reverse=True)[:highlight_top_n])

    for node in G.nodes():
        score = scores.get(node, 0.0) if scores else None
        attrs = _node_attrs(node, score, node in top_services)
        attr_str = ", ".join(f'{k}="{v}"' for k, v in attrs.items())
        lines.append(f'  "{node}" [{attr_str}];')

    lines.append("")

    for u, v, data in G.edges(data=True):
        weight = data.get("weight", 1)
        penwidth = min(4.0, 1.0 + weight / 50.0)
        lines.append(
            f'  "{u}" -> "{v}" '
            f'[label="{weight}", penwidth="{penwidth:.1f}"];'
        )

    lines.append("}")
    return "\n".join(lines)


def _node_attrs(
    node: str,
    score: Optional[float],
    is_top: bool,
) -> dict[str, str]:
    if is_top:
        return {
            "fillcolor": "#ff6b6b",
            "fontcolor": "white",
            "color":     "#cc0000",
            "penwidth":  "2",
            "tooltip":   f"{node} — high blast radius",
        }
    if score is not None:
        intensity = int(255 - score * 80)
        hex_val = f"{intensity:02x}"
        return {
            "fillcolor": f"#{hex_val}{hex_val}ff",
            "tooltip":   f"{node} — score: {score:.3f}",
        }
    return {"fillcolor": "#e8e8e8"}


def save_dot(dot_str: str, path: Path) -> None:
    """Write a DOT string to disk."""
    path.write_text(dot_str)
    logger.info("DOT file written to %s", path)
