# ChaosRank Roadmap

The following features and improvements are planned for the upcoming releases of the ChaosRank SDK and Engine.

### Ingestion & Adapters
- [ ] **OTel Protobuf Support**: Native support for binary-encoded OTLP traces (v1.1).
- [ ] **Extended Incident Adapters**: Native support for Datadog, Sentry, and New Relic.
- [ ] **Cross-Region Topology**: Ability to merge traces from multiple AWS/GCP/Azure regions.

### Scoring & Analytics
- [ ] **AI-Assisted Weight Tuning**: Automatic optimization of `alpha` and `beta` weights based on experiment success rates.
- [ ] **Drift Detection**: Alerts when the system's structural risk profile changes fundamentally between observation windows.
- [ ] **Steady-State Inference**: Automatic derivation of baseline SLOs from trace latency distributions.

### Orchestration
- [ ] **Chaos Mesh Native Provider**: Direct execution of experiments via Chaos Mesh Custom Resources.
- [ ] **Litmus SDK Integration**: Tighter integration with Litmus 3.x control plane.

---

*Note: As an Open Core project, we focus on the public SDK and adapter layer. Proprietary scoring improvements are managed in the ChaosRank Private Engine.*
