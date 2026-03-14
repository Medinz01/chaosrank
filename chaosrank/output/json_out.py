import json
import sys
from typing import TextIO


def render_json(
    ranked: list[dict],
    stream: TextIO = sys.stdout,
    async_deps_provided: bool = False,
) -> None:
    output = []
    for row in ranked:
        entry = dict(row)
        entry["reasoning"] = _build_reasoning(row)
        if async_deps_provided:
            entry["blast_radius_notes"] = (
                "Blast radius includes async edges from --async-deps manifest. "
                "Async edges are assigned median trace edge weight — "
                "call frequency data is unavailable for async channels."
            )
        output.append(entry)

    json.dump(output, stream, indent=2)
    stream.write("\n")


def _build_reasoning(row: dict) -> str:
    br    = row["blast_radius"]
    fr    = row["fragility"]
    fault = row["suggested_fault"]
    conf  = row["confidence"]

    br_label = "high" if br >= 0.7 else "moderate" if br >= 0.4 else "low"
    fr_label = "high" if fr >= 0.7 else "moderate" if fr >= 0.4 else "low"

    return (
        f"Blast radius is {br_label} ({br:.3f}) — structural impact if this service fails. "
        f"Fragility is {fr_label} ({fr:.3f}) — based on incident history. "
        f"Suggested fault: {fault} (confidence: {conf})."
    )