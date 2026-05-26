# Saturn Project Context

## Project Name

Saturn

Repository: https://github.com/yz514/saturn

## One-line Description

Saturn is an AI-native autonomous equity research platform designed to coordinate multiple specialized agents to ingest financial data, analyze companies, debate investment theses, generate research reports, and eventually operate as a persistent financial intelligence system.

## Current Stage

This project is currently at the very beginning stage.

The immediate goal is not to over-engineer the system. The current priority is to build a clean foundation:

1. Define the product vision.
2. Create the initial repo/documentation structure.
3. Build a minimal runnable MVP.
4. Practice using Claude CLI / coding agents to help design, code, review, debug, and document the system.
5. Gradually evolve Saturn into a serious AI infra / AI data platform project.

At this stage, assume the repo may be mostly empty or documentation-heavy. Do not assume production architecture already exists.

---

# Why We Are Building Saturn

Saturn is being built for three purposes:

## 1. Personal Financial Research

The system should eventually help the user conduct equity research more efficiently.

It should be able to:

- Analyze public companies.
- Track earnings.
- Summarize SEC filings.
- Read earnings transcripts.
- Monitor news and macro data.
- Generate bull/bear investment theses.
- Compare companies.
- Maintain a watchlist.
- Update prior research when new data arrives.

The focus is mainly equities, macro, and finance. Avoid Web3 as the core direction, except BTC or crypto macro when relevant.

## 2. AI Infra / AI Data Platform Practice

Saturn is also a learning project to develop real AI engineering skills.

The project should train:

- AI agent orchestration
- Long-running workflows
- Data ingestion pipelines
- Retrieval-augmented generation
- Vector memory
- Persistent state
- Observability
- Evaluation
- Workflow automation
- Agent reliability
- Context engineering
- AI-assisted software development

This should be treated as a serious engineering portfolio project, not a toy chatbot.

## 3. Multi-Agent Engineering Workflow

We also want to use this project itself as a practice ground for AI-assisted development.

Claude CLI should help act as:

- Architect
- Coding assistant
- Reviewer
- Debugger
- Documentation writer
- Refactoring assistant
- Test writer
- System design partner

The user wants to practice how multiple AI agents can coordinate work, eventually allowing AI agents to perform longer-running tasks with less human intervention.

---

# Product Vision

Saturn should evolve into an autonomous AI equity research team.

The long-term version should behave like:

> Bloomberg Terminal + junior research analysts + AI portfolio manager + persistent memory.

The system should not merely answer one-off questions. It should eventually operate continuously.

Example future workflow:

User adds `NVDA`, `MSFT`, `TSLA`, or `ASML` to a watchlist.

Saturn automatically:

1. Ingests relevant data.
2. Reads recent filings and transcripts.
3. Tracks price and valuation.
4. Monitors news and macro conditions.
5. Generates a company research memo.
6. Creates bull and bear arguments.
7. Has a critic agent challenge the thesis.
8. Stores the conclusion in memory.
9. Updates the thesis when new information arrives.
10. Produces a clean report or dashboard.

---

# Initial Scope

The first version should be simple.

## MVP Goal

Input:

```text
ticker = "NVDA"
```

Output:

```text
A structured markdown equity research report.
```

The report should include:

* Company overview
* Business segments
* Recent performance
* Key financial metrics
* Recent news
* Bull thesis
* Bear thesis
* Risks
* Valuation discussion
* Final summary
* Sources used

At the beginning, it is acceptable to use simple public APIs, mock data, manually downloaded transcripts, or small sample files. Do not block progress on perfect data access.

---

# Non-Goals

Do not build these in the beginning:

* Real-money trading execution
* High-frequency trading system
* Full Bloomberg replacement
* Complex frontend
* Mobile app
* Multi-user SaaS
* Overly complicated Kubernetes/microservices setup
* Autonomous trading bot
* Web3 research platform

The system should start as a local-first AI research platform.

---

# Engineering Philosophy

## Keep the system runnable

Always prefer a simple working version over an impressive but broken architecture.

## Start local-first

The first version should run locally from the command line.

## Docs-as-code

Markdown documentation is part of the system. Keep important decisions in the repo.

## AI-readable repo

The repo should be structured so AI coding agents can understand the project quickly.

## Avoid premature complexity

Do not introduce Kafka, Kubernetes, Airflow, Temporal, or distributed infra too early unless there is a clear reason.

## Build toward production thinking

Even when building locally, think like a mature engineer:

* Clear modules
* Clear interfaces
* Tests
* Logs
* Config management
* Error handling
* Documentation
* Reproducibility

---

# Repo Structure (current direction)

The repo is modular and AI-readable. The Phase 0 structure that exists today:

```text
saturn/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── docs/
│   ├── vision.md
│   ├── roadmap.md
│   ├── engineering_principles.md
│   └── superpowers/         # design specs + implementation plans
│
├── architecture/
│   └── system_overview.md
│
├── saturn/
│   ├── config.py
│   ├── cli.py
│   ├── models.py
│   ├── llm/                 # base (LLMClient), anthropic_client, mock_client
│   ├── ingestion/           # prices (yfinance + mock fixture)
│   ├── workflows/           # equity_research (analyze -> debate -> run)
│   ├── reports/             # markdown_report (render)
│   └── utils/               # logging
│
├── tests/
├── examples/
│   └── nvda_research_report.md
└── reports/                 # runtime output (gitignored)
```

Modules deferred to later phases (Planner / Macro / Industry / Critic / PM agents,
memory/vector store) are documented in `docs/roadmap.md` and will be added when
they do real work — not stubbed out empty.

---

# Suggested Technical Direction

## Language

Use Python first.

Reason:

* User is familiar with Python.
* Python is best for data engineering, AI workflows, and finance APIs.
* Easy to integrate with LangGraph, LlamaIndex, pandas, vector DBs, and APIs.

## CLI-first

Start with a CLI command like:

```bash
saturn research NVDA
```

Expected output:

```text
reports/NVDA_YYYY-MM-DD.md
```

## Agent Orchestration

Start simple.

Phase 0 is a sequential workflow:

```text
ingest -> analyze -> debate -> render
```

Later, split into specialized agents and migrate to LangGraph for explicit
graph-based orchestration.

## Data Sources

Early phase can use:

* yfinance or other free price data
* SEC EDGAR APIs
* manually saved earnings transcripts
* RSS/news APIs if available
* FRED for macro data
* local markdown/text files as sample inputs

Do not require expensive APIs at the beginning.

## Memory

Early memory can be local files or SQLite.

Later memory can evolve into:

* Postgres
* Qdrant
* Chroma
* LanceDB
* Knowledge graph

## Observability

At first, use structured logging.

Later add:

* Tracing
* Agent step logs
* Prompt/response logs
* Cost tracking
* Token usage
* Evaluation metrics

---

# Agent Roles for the Product

## Planner Agent
Understand the request, break into subtasks, decide which agents to call, define output requirements.

## Research Agent
Gather company information; retrieve filings, transcripts, and news; summarize source material; provide citations.

## Financial Analyst Agent
Analyze revenue, margins, growth, cash flow, debt, valuation, and trends. Avoid unsupported claims.

## Macro Agent
Analyze rates, inflation, liquidity, sector macro, FX, commodities, and market regime. Apply only when relevant.

## Industry Agent
Compare with competitors; identify industry structure, market share, and secular trends.

## Bull / Bear Thesis Agents
Build the strongest positive case and the strongest negative case, respectively.

## Critic Agent
Challenge assumptions, detect weak evidence, flag hallucination risk, ask what data is missing, identify overconfidence.

## PM / Synthesis Agent
Combine outputs, resolve contradictions, produce a balanced final view, assign a confidence level.

## Report Writer Agent
Convert structured analysis into clean, consistent markdown with source references.

---

# Agent Roles for Development Workflow

Claude CLI should also help as development agents: Architect, Implementer,
Reviewer, Debugger, Test Engineer, Documentation Writer, Refactor Agent.

The user wants to practice using AI agents not just to build Saturn, but also to
coordinate the development process itself.

---

# How Claude Should Work in This Repo

When asked to implement something:

1. First inspect the repo.
2. Understand existing structure.
3. Avoid massive rewrites unless necessary.
4. Propose a small plan.
5. Implement the smallest useful change.
6. Add or update tests if applicable.
7. Update docs if behavior or architecture changes.
8. Summarize what changed.
9. Suggest the next practical step.

Do not overcomplicate the system. Do not introduce major dependencies without
explaining why. Do not silently change the project direction. Prioritize clear,
maintainable, beginner-friendly engineering.

---

# Coding Style Preferences

* Prefer readable Python over clever Python.
* Use type hints where helpful.
* Use dataclasses or Pydantic models for structured data.
* Keep functions small. Keep modules focused.
* Add docstrings for important components.
* Use clear names. Avoid hidden global state.
* Use environment variables for secrets. Never commit API keys. Add `.env.example`.

---

# Initial Commands We Eventually Want

```bash
saturn research NVDA
saturn research MSFT --deep
saturn ingest filings NVDA
saturn ingest prices NVDA
saturn watchlist add NVDA
saturn watchlist run
saturn memory search NVDA
```

Implemented so far:

```bash
saturn research NVDA [--mock] [--model ...]
```

---

# Important Project Constraint

Saturn should help with investment research, but it should not present itself as
financial advice. Reports include:

```text
This report is for research and educational purposes only and is not investment advice.
```

---

# North Star

Saturn is not just a financial research script. It is a long-term project for
learning how to build AI-native data platforms and autonomous agent systems.

Every phase should move the project closer to:

* More autonomy
* Better memory
* Better data ingestion
* Better reasoning
* Better observability
* Less human intervention
* More reliable research output
