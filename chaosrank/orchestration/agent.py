"""
chaosrank/orchestration/agent.py

Regional collection agent.

Each agent runs within a single observation scope (region, cluster, namespace)
and is responsible for:
  1. Parsing trace data from its assigned source
  2. Constructing a local partial dependency graph
  3. Computing per-edge confidence based on observed span count
  4. Producing a LocalGraphSnapshot for the central merger

Agents are stateless between snapshots — each call to observe() produces
a fresh snapshot. State is maintained by the merger, not the agent.

In production, agents would run as long-lived processes streaming snapshots
to the merger. For offline/batch use, they can be called once per analysis run.

See: invention_disclosure_v2.md, Claim 1, Section 1.2 Component A
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import networkx as nx

logger = logging.getLogger(__name__)


@dataclass
class EdgeObservation:
    """
    A single edge observation from an agent.

    weight:      raw call frequency observed by this agent
    confidence:  fraction of total spans that involved this edge
                 = span_count(u,v) / total_spans_in_scope
                 Used by the merger's weighted confidence protocol.
    edge_type:   sync | async
    """
    source:     str
    target:     str
    weight:     float
    confidence: float
    edge_type:  str = "sync"
    channel:    str | None = None
    topic:      str | None = None


@dataclass
class LocalGraphSnapshot:
    """
    Output of a single agent observation run.

    agent_id:       unique identifier for this agent (e.g. 'us-east-1', 'cluster-a')
    observed_at:    timestamp when observation was taken
    total_spans:    total span count in this observation window
                    used to compute per-edge confidence
    edges:          all observed edges with weights and confidences
    scope_metadata: optional dict of scope information (region, cluster, namespace)
    """
    agent_id:       str
    observed_at:    datetime
    total_spans:    int
    edges:          list[EdgeObservation]
    scope_metadata: dict = field(default_factory=dict)

    def to_graph(self) -> nx.DiGraph:
        """Convert snapshot to a local nx.DiGraph for inspection."""
        G = nx.DiGraph()
        for obs in self.edges:
            G.add_edge(
                obs.source, obs.target,
                weight=obs.weight,
                confidence=obs.confidence,
                edge_type=obs.edge_type,
                channel=obs.channel,
                topic=obs.topic,
            )
        return G


class CollectionAgent:
    """
    Regional collection agent that builds a LocalGraphSnapshot from trace data.

    Each agent has:
      - An agent_id identifying its observation scope
      - A trace source (file path or callable)
      - A min_call_frequency filter (same semantics as builder.py)

    Usage:
      agent = CollectionAgent(agent_id="us-east-1", traces_path=Path("traces.json"))
      snapshot = agent.observe()
      # snapshot is ready for CentralMerger.ingest()
    """

    def __init__(
        self,
        agent_id:           str,
        traces_path:        Path | None = None,
        trace_format:       str         = "jaeger",
        min_call_frequency: int         = 10,
        scope_metadata:     dict        = None,
    ) -> None:
        self.agent_id           = agent_id
        self.traces_path        = traces_path
        self.trace_format       = trace_format
        self.min_call_frequency = min_call_frequency
        self.scope_metadata     = scope_metadata or {}

    def observe(self, traces_path: Path | None = None) -> LocalGraphSnapshot:
        """
        Parse traces and produce a LocalGraphSnapshot.

        Parameters
        ----------
        traces_path: override the path set at init (useful for batch runs)

        Returns
        -------
        LocalGraphSnapshot ready for CentralMerger.ingest()
        """
        path = traces_path or self.traces_path
        if path is None:
            raise ValueError(
                f"Agent '{self.agent_id}': no traces_path provided. "
                f"Pass traces_path to observe() or set it at init."
            )

        logger.info(
            "Agent '%s': parsing traces from %s (format=%s)",
            self.agent_id, path, self.trace_format,
        )

        edges_raw, total_spans = self._parse_traces(path)
        observations           = self._build_observations(edges_raw, total_spans)

        snapshot = LocalGraphSnapshot(
            agent_id=self.agent_id,
            observed_at=datetime.utcnow(),
            total_spans=total_spans,
            edges=observations,
            scope_metadata=self.scope_metadata,
        )

        logger.info(
            "Agent '%s': snapshot produced — %d edges, %d total spans",
            self.agent_id, len(observations), total_spans,
        )
        return snapshot

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _parse_traces(self, path: Path) -> tuple[dict[tuple[str, str], int], int]:
        """
        Parse traces and return:
          edges:       dict[(caller, callee) → span_count]
          total_spans: total number of spans observed

        Returns raw counts before min_call_frequency filtering so that
        confidence can be computed relative to total observed traffic.
        """
        if self.trace_format == "jaeger":
            # Parse without frequency filter to get raw counts for confidence
            raw_edges = parse_traces_raw(path, fmt="jaeger")
        elif self.trace_format == "otlp":
            raw_edges = parse_traces_raw(path, fmt="otlp")
        else:
            raise ValueError(
                f"Unknown trace format: {self.trace_format!r}. "
                f"Supported: jaeger, otlp"
            )

        total_spans = sum(raw_edges.values())
        return raw_edges, total_spans

    def _build_observations(
        self,
        raw_edges:    dict[tuple[str, str], int],
        total_spans:  int,
    ) -> list[EdgeObservation]:
        """
        Convert raw edge counts to EdgeObservations with confidence scores.

        Edges below min_call_frequency are included but marked with low
        confidence — the merger can decide whether to include them based
        on cross-agent corroboration.
        """
        observations = []
        for (source, target), count in raw_edges.items():
            confidence = count / total_spans if total_spans > 0 else 0.0
            observations.append(EdgeObservation(
                source=source,
                target=target,
                weight=float(count),
                confidence=confidence,
                edge_type="sync",  # default; async edges come from async_deps
            ))
        return observations


def parse_traces_raw(
    path: Path,
    fmt:  str = "jaeger",
) -> dict[tuple[str, str], int]:
    """
    Parse traces without frequency filtering.

    Returns all observed (caller, callee) → span_count pairs including
    low-frequency edges. Used by agents to compute accurate confidence scores.
    """
    if fmt == "jaeger":
        from chaosrank.parser.jaeger import parse_traces
        # parse_traces applies min_call_frequency — pass 0 to get all edges
        return parse_traces(path, min_call_frequency=0)
    elif fmt == "otlp":
        from chaosrank.parser.otlp import parse_otlp
        return parse_otlp(path, min_call_frequency=0)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")