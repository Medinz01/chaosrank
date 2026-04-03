"""
HTTP client for the private ChaosRank engine API.
Provides remote access to risk-scoring, adaptive ranking, and orchestration logic.
"""
from __future__ import annotations

import gzip
import json
import logging
from typing import Any

import networkx as nx
import requests

from chaosrank.engine.serializer import graph_to_payload, incidents_to_payload
from chaosrank.parser.incidents import ServiceIncidents
from chaosrank.orchestration.agent import LocalGraphSnapshot

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30   # seconds


class EngineError(RuntimeError):
    """Raised when the engine returns a non-2xx response."""
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Engine error {status_code}: {detail}")


class EngineClient:
    """
    HTTP client that talks to the private chaosrank-engine API.

    Parameters
    ----------
    url:      Base URL of the engine (e.g. 'http://localhost:8080')
    api_key:  X-ChaosRank-Key header value
    timeout:  Request timeout in seconds (default 30)
    compress: If True, gzip-compress request bodies >10KB (default False)
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: int  = _DEFAULT_TIMEOUT,
        compress: bool = False,
    ) -> None:
        self._base    = url.rstrip("/")
        self._timeout = timeout
        self._compress = compress
        self._session = requests.Session()
        self._session.headers.update({
            "X-ChaosRank-Key": api_key,
            "Content-Type":    "application/json",
        })

    # Health check

    def health(self) -> bool:
        """Return True if the engine is reachable and healthy."""
        try:
            resp = self._session.get(
                f"{self._base}/v1/health", timeout=5
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # Core ranking

    def rank(
        self,
        G: nx.DiGraph,
        service_incidents: dict[str, ServiceIncidents],
        config: dict[str, Any] | None = None,
    ) -> list[dict]:
        """
        Rank services by risk score.

        Parameters
        ----------
        G:                  Dependency graph from graph.builder.build_graph()
        service_incidents:  From parser.incidents or incident_adapters fetch()
        config:             Optional overrides — alpha, beta, decay_lambda, etc.
                            Falls back to engine defaults (matching chaosrank.yaml).

        Returns
        -------
        list[dict] — same schema as the old rank_services() output, sorted by risk desc.
        Each dict has: rank, service, risk, blast_radius, fragility,
                       suggested_fault, confidence
        """
        payload = {
            "graph":     graph_to_payload(G),
            "incidents": incidents_to_payload(service_incidents),
        }
        if config:
            payload["config"] = config

        resp = self._post("/v1/rank", payload)
        return resp["ranked"]

    # Adaptive ranking

    def adaptive_rank(
        self,
        G: nx.DiGraph,
        service_incidents: dict[str, ServiceIncidents],
        config: dict[str, Any] | None = None,
        last_observed: str | None = None,
    ) -> list[dict]:
        """
        Rank with live alpha/beta weights and 95% confidence intervals.
        Weights self-update based on recorded experiment outcomes.

        Returns superset of rank() — adds: alpha_used, beta_used,
        ci_lower, ci_upper, ci_width, low_confidence, confidence_note
        """
        payload = {
            "graph":     graph_to_payload(G),
            "incidents": incidents_to_payload(service_incidents),
        }
        if config:
            payload["config"] = config
        if last_observed:
            payload["last_observed"] = last_observed

        resp = self._post("/v1/adaptive/rank", payload)
        return resp["ranked"]

    def record_outcome(
        self,
        ranked_row: dict,
        outcome: str,
        graph_state_hash: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """
        Record the result of a chaos experiment.

        Parameters
        ----------
        ranked_row: A row from adaptive_rank() output — must contain
                    service, risk, blast_radius, fragility, rank,
                    alpha_used, beta_used
        outcome:    'WEAKNESS_CONFIRMED' | 'WEAKNESS_NOT_FOUND' | 'INCONCLUSIVE'

        Returns
        -------
        dict with new_alpha, new_beta, message
        """
        required = {"service", "risk", "blast_radius", "fragility",
                    "rank", "alpha_used", "beta_used"}
        missing = required - set(ranked_row.keys())
        if missing:
            raise ValueError(
                f"ranked_row missing fields: {missing}. "
                f"Pass a row from adaptive_rank(), not rank()."
            )
        payload = {
            "service":         ranked_row["service"],
            "outcome":         outcome,
            "risk_score":      ranked_row["risk"],
            "blast_radius":    ranked_row["blast_radius"],
            "fragility":       ranked_row["fragility"],
            "alpha_used":      ranked_row["alpha_used"],
            "beta_used":       ranked_row["beta_used"],
            "rank_at_time":    ranked_row["rank"],
        }
        if graph_state_hash:
            payload["graph_state_hash"] = graph_state_hash
        if notes:
            payload["notes"] = notes

        return self._post("/v1/adaptive/outcome", payload)

    def adaptive_summary(self) -> dict:
        """Return current adaptive weight state and outcome statistics."""
        resp = self._session.get(
            f"{self._base}/v1/adaptive/summary",
            timeout=self._timeout,
        )
        self._raise_for_status(resp)
        return resp.json()

    # Multi-agent orchestration

    def merge_snapshots(
        self,
        snapshots: list[LocalGraphSnapshot],
        min_call_frequency: int = 10,
    ) -> dict:
        """
        Send LocalGraphSnapshot objects from regional CollectionAgents
        to the engine merger. Returns the canonical merged graph payload.

        The returned dict has 'graph' (edge list) that can be used
        to build an nx.DiGraph for visualization or passed directly
        to rank() as a pre-merged graph payload.
        """
        payload = {
            "min_call_frequency": min_call_frequency,
            "snapshots": [
                {
                    "agent_id":       s.agent_id,
                    "observed_at":    s.observed_at.isoformat(),
                    "total_spans":    s.total_spans,
                    "scope_metadata": s.scope_metadata,
                    "edges": [
                        {
                            "source":     e.source,
                            "target":     e.target,
                            "weight":     e.weight,
                            "confidence": e.confidence,
                            "edge_type":  e.edge_type,
                            "channel":    e.channel,
                            "topic":      e.topic,
                        }
                        for e in s.edges
                    ],
                }
                for s in snapshots
            ],
        }
        return self._post("/v1/orchestration/merge", payload)

    # Federation

    def federation_rank(
        self,
        domains: list[dict],
        inter_domain_edges: list[dict] | None = None,
        config: dict[str, Any] | None = None,
        window_days: int = 30,
    ) -> list[dict]:
        """
        Rank services across a federated multi-domain graph.

        Parameters
        ----------
        domains:  list of domain dicts, each with:
                  { domain_id, edges: [...], incidents: [...], components: [...] }
        inter_domain_edges:  explicit cross-domain dependencies
        config:   optional scoring config overrides

        Returns
        -------
        list[dict] — same as rank(), services prefixed 'domain_id/service_name'
        """
        payload: dict[str, Any] = {
            "domains":     domains,
            "window_days": window_days,
        }
        if inter_domain_edges:
            payload["inter_domain_edges"] = inter_domain_edges
        if config:
            payload["config"] = config

        resp = self._post("/v1/federation/rank", payload)
        return resp["ranked"]

    # Internal helpers

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, default=str).encode("utf-8")

        headers: dict[str, str] = {}
        if self._compress and len(body) > 10_240:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"

        resp = self._session.post(
            f"{self._base}{path}",
            data=body,
            headers=headers,
            timeout=self._timeout,
        )
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise EngineError(resp.status_code, detail)
