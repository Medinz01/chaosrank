import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

SEVERITY_THRESHOLDS = [
    (5.0, "critical"),
    (3.0, "high"),
    (2.0, "medium"),
    (1.5, "low"),
]

INCIDENT_TYPE = "latency"

# Synthetic base timestamp — incidents are spread across a 30-day window
BASE_TIMESTAMP = datetime(2026, 2, 1, 8, 0, 0)


def parse_id_to_service(csv_path: Path) -> dict[str, str]:
    """Extract {col_name: service_name} from CSV headers formatted as '{id}_{service_name}'."""
    with open(csv_path, encoding="utf-8") as f:
        header = f.readline().strip()
    result = {}
    for col in header.split(",")[1:]:
        parts = col.split("_", 1)
        if len(parts) == 2:
            result[col] = parts[1].strip().replace("_", "-")
    return result


def compute_baseline_means(
    baseline_path: Path,
    col_to_service: dict[str, str],
) -> dict[str, float]:
    """Compute mean latency per service column from the no-interference CSV."""
    print(f"Loading baseline: {baseline_path.name} ...")
    sums   = {}
    counts = {}

    with open(baseline_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for col in col_to_service:
                val = row.get(col, "").strip()
                if val:
                    sums[col]   = sums.get(col, 0.0) + float(val)
                    counts[col] = counts.get(col, 0)  + 1

    means = {col: sums[col] / counts[col] for col in col_to_service if counts.get(col, 0) > 0}
    print(f"Baseline computed for {len(means)} services")
    return means


def compute_anomaly_mean(anomaly_path: Path, target_col: str) -> float | None:
    total = 0.0
    count = 0
    with open(anomaly_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            val = row.get(target_col, "").strip()
            if val:
                total += float(val)
                count += 1
    return total / count if count > 0 else None


def severity_from_ratio(ratio: float) -> str | None:
    for threshold, severity in SEVERITY_THRESHOLDS:
        if ratio >= threshold:
            return severity
    return None


def extract_incidents(app_dir: Path, output_path: Path) -> None:
    """Extract latency-degradation incidents from FIRM anomaly CSVs and write ChaosRank-compatible output."""
    baseline_path = app_dir / "no-interference.csv"
    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found", file=sys.stderr)
        sys.exit(1)

    col_to_service = parse_id_to_service(baseline_path)

    anomaly_files = {
        f.stem: f
        for f in sorted(app_dir.glob("*.csv"))
        if f.name != "no-interference.csv" and len(f.stem.split("_", 1)) == 2
    }
    print(f"Found {len(anomaly_files)} anomaly files")

    baseline_means = compute_baseline_means(baseline_path, col_to_service)

    incidents  = []
    day_offset = 0

    for file_stem, afile in sorted(anomaly_files.items()):
        target_col = file_stem

        if target_col not in col_to_service:
            print(f"  SKIP {afile.name}: column '{target_col}' not in baseline")
            continue

        service_name  = col_to_service[target_col]
        baseline_mean = baseline_means.get(target_col)

        if not baseline_mean or baseline_mean == 0:
            print(f"  SKIP {afile.name}: no baseline mean")
            continue

        print(f"  Processing {afile.name} → service: {service_name}")
        anomaly_mean = compute_anomaly_mean(afile, target_col)

        if anomaly_mean is None:
            print(f"    SKIP: no data")
            continue

        ratio    = anomaly_mean / baseline_mean
        severity = severity_from_ratio(ratio)

        if severity is None:
            print(f"    ratio={ratio:.2f}x — below threshold, skipping")
            continue

        ts = BASE_TIMESTAMP + timedelta(days=day_offset % 30, hours=day_offset % 8)
        day_offset += 1

        incidents.append({
            "timestamp":      ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "service":        service_name,
            "type":           INCIDENT_TYPE,
            "severity":       severity,
            "request_volume": 25000,
        })
        print(f"    ratio={ratio:.2f}x → severity={severity}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    chaosrank_cols = ["timestamp", "service", "type", "severity", "request_volume"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=chaosrank_cols)
        writer.writeheader()
        writer.writerows(incidents)

    print(f"\nExtracted {len(incidents)} incidents")
    print(f"Services with incidents: {len(set(i['service'] for i in incidents))}")
    print(f"Output: {output_path}")
    print("\nIncidents per service:")
    for svc, count in Counter(i["service"] for i in incidents).most_common(10):
        print(f"  {svc}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract incidents from DeathStarBench anomaly traces"
    )
    parser.add_argument("--app", required=True,
        choices=["social-network", "media-service", "hotel-reservation", "ticket-booking"])
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app_dir = args.data_dir / args.app
    extract_incidents(
        app_dir,
        args.output or Path(f"benchmarks/real_traces/{args.app}_incidents.csv"),
    )


if __name__ == "__main__":
    main()