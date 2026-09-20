# agent/orchestrator/

Drives the agent workflow: receives a flagged anomaly, calls the tools in `agent/tools/`, forms a hypothesis (jamming / targeted spoof / equipment fault / benign), and either hands off to the report generator (high confidence) or escalates to a human analyst (low confidence or novel pattern).

Stage 1 implementation: LangGraph, or a custom bounded state machine if LangGraph turns out heavier than needed (MIP section 6).
