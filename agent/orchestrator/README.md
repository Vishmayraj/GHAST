# agent/orchestrator/

Drives the agent workflow: receives a flagged anomaly, calls the tools in `agent/tools/`, forms a hypothesis (jamming / targeted spoof / freeze replay / equipment fault / benign / unresolved), and either hands off to the report generator (high confidence) or escalates to a human analyst (low confidence or novel pattern).

Implemented as a custom straight-line function, not LangGraph. See `docs/agent.md` for the actual rules.
