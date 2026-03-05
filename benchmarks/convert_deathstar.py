import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def parse_id_to_service(csv_path: Path) -> dict[str, str]:
    """Extract {id: service_name} from CSV column headers formatted as '{id}_{service_name}'."""
    with open(csv_path, encoding="utf-8") as f:
        header = f.readline().strip()

    id_to_service = {}
    for col in header.split(",")[1:]:
        parts = col.split("_", 1)
        if len(parts) == 2:
            id_to_service[parts[0].strip()] = parts[1].strip().replace("_", "-")

    return id_to_service


def parse_execution_paths(paths_file: Path) -> list[list[str]]:
    """Parse execution_paths.txt into lists of service ID chains."""
    chains = []
    with open(paths_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ids = [x.strip() for x in line.split("->")]
            if len(ids) >= 2:
                chains.append(ids)
    return chains


def build_edges(
    chains: list[list[str]],
    id_to_service: dict[str, str],
) -> dict[tuple[str, str], int]:
    """Count directed edges across all execution paths; weight = path frequency."""
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)

    for chain in chains:
        for i in range(len(chain) - 1):
            parent = id_to_service.get(chain[i])
            child  = id_to_service.get(chain[i + 1])
            if parent and child and parent != child:
                edge_counts[(parent, child)] += 1

    return dict(edge_counts)


def edges_to_jaeger(
    edges: dict[tuple[str, str], int],
    app_name: str,
    scale: int = 100,
) -> dict:
    """Convert an edge-weight map to a synthetic Jaeger JSON export consumable by parse_traces()."""
    services = {s for pair in edges for s in pair}

    processes = {
        f"p{i}": {"serviceName": svc}
        for i, svc in enumerate(sorted(services))
    }
    svc_to_pid = {v["serviceName"]: k for k, v in processes.items()}

    spans = []
    span_id_counter = [0]

    in_edges = {callee for _, callee in edges}
    roots = [s for s in services if s not in in_edges]
    root = roots[0] if roots else sorted(services)[0]

    root_span_id = "root-0000"
    spans.append({"spanID": root_span_id, "processID": svc_to_pid[root], "references": []})

    parent_span_ids: dict[str, str] = {root: root_span_id}
    visited_edges = set()
    queue = [root]
    seen  = {root}

    while queue:
        current = queue.pop(0)
        current_span_id = parent_span_ids.get(current, root_span_id)

        for (caller, callee), weight in sorted(edges.items()):
            if caller != current or (caller, callee) in visited_edges:
                continue
            visited_edges.add((caller, callee))

            if callee not in parent_span_ids:
                callee_span_id = f"span-{len(spans):04d}"
                spans.append({
                    "spanID":     callee_span_id,
                    "processID":  svc_to_pid[callee],
                    "references": [{"refType": "CHILD_OF", "spanID": current_span_id}],
                })
                parent_span_ids[callee] = callee_span_id

            for _ in range(max(0, weight * scale - 1)):
                span_id_counter[0] += 1
                spans.append({
                    "spanID":     f"s{span_id_counter[0]:06d}",
                    "processID":  svc_to_pid[callee],
                    "references": [{"refType": "CHILD_OF", "spanID": current_span_id}],
                })

            if callee not in seen:
                seen.add(callee)
                queue.append(callee)

    return {
        "data": [
            {
                "traceID":   f"deathstar-{app_name}",
                "spans":     spans,
                "processes": processes,
            }
        ],
        "metadata": {
            "source":      "UIUC/FIRM DeathStarBench dataset",
            "application": app_name,
            "citation":    (
                "Qiu et al., FIRM: An Intelligent Fine-grained Resource Management "
                "Framework for SLO-oriented Microservices, OSDI 2020. "
                "https://doi.org/10.13012/B2IDB-6738796_V1"
            ),
            "services": sorted(services),
            "edges":    len(edges),
        },
    }


def convert(app_dir: Path, output_path: Path, scale: int = 100) -> None:
    """Run the full conversion pipeline for one DeathStarBench application directory."""
    paths_file = app_dir / "execution_paths.txt"
    if not paths_file.exists():
        print(f"ERROR: {paths_file} not found", file=sys.stderr)
        sys.exit(1)

    csv_files = sorted(app_dir.glob("*.csv"))
    if not csv_files:
        print(f"ERROR: No CSV files found in {app_dir}", file=sys.stderr)
        sys.exit(1)

    ref_csv = next((f for f in csv_files if "no-interference" in f.name), csv_files[0])

    print(f"Extracting service map from: {ref_csv.name}")
    id_to_service = parse_id_to_service(ref_csv)
    print(f"Found {len(id_to_service)} services: {sorted(id_to_service.values())}")

    print(f"Parsing execution paths from: {paths_file.name}")
    chains = parse_execution_paths(paths_file)
    print(f"Found {len(chains)} execution paths")

    edges = build_edges(chains, id_to_service)
    print(f"Extracted {len(edges)} unique edges")
    for (caller, callee), weight in sorted(edges.items(), key=lambda x: -x[1]):
        print(f"  {caller} -> {callee}  (weight={weight})")

    jaeger = edges_to_jaeger(edges, app_dir.name, scale=scale)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(jaeger, f, indent=2)

    print(f"\nOutput: {output_path}")
    print(f"Spans generated: {len(jaeger['data'][0]['spans'])}")
    print(f"Services: {len(id_to_service)}")
    print(f"Citation: {jaeger['metadata']['citation']}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert DeathStarBench traces to ChaosRank Jaeger JSON"
    )
    parser.add_argument("--app", required=True,
        choices=["social-network", "media-service", "hotel-reservation", "ticket-booking"])
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scale", type=int, default=100,
        help="Edge weight multiplier (default: 100)")
    args = parser.parse_args()

    app_dir = args.data_dir / args.app
    if not app_dir.exists():
        print(f"ERROR: {app_dir} not found", file=sys.stderr)
        sys.exit(1)

    convert(app_dir, args.output or Path(f"benchmarks/real_traces/{args.app}.json"), scale=args.scale)


if __name__ == "__main__":
    main()