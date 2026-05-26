# Engineering Principles

- **Keep it runnable.** Prefer a simple working version over an impressive but
  broken architecture.
- **Local-first.** The system runs from the command line first.
- **Docs-as-code.** Important decisions live in the repo (specs, plans, RFCs).
- **AI-readable repo.** Clear modules and interfaces so coding agents orient fast.
- **Avoid premature complexity.** No Kafka/Kubernetes/Airflow/Temporal/LangGraph
  until there is a clear reason.
- **Production thinking, even locally.** Clear modules, interfaces, tests, logs,
  config management, error handling, reproducibility.
- **Style.** Readable Python over clever Python; type hints; Pydantic for
  structured data; small focused functions/modules; no hidden global state;
  secrets via env vars; never commit API keys.
