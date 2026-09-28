import pytest
from types import SimpleNamespace
from report_generator.report import DEFAULT_REPORT_MODEL, draft_report, report_model

class _FakeCompletions:
    def __init__(self, content): self.calls = []; self._content = content
    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])

def _client(content):
    completions = _FakeCompletions(content)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions

def test_report_model_defaults_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("GHAST_REPORT_MODEL", raising=False)
    assert report_model() == DEFAULT_REPORT_MODEL

def test_report_model_reads_env_override(monkeypatch) -> None:
    monkeypatch.setenv("GHAST_REPORT_MODEL", "some-future-model")
    assert report_model() == "some-future-model"

@pytest.mark.asyncio
async def test_draft_report_uses_configured_model(monkeypatch) -> None:
    monkeypatch.setenv("GHAST_REPORT_MODEL", "some-future-model")
    client, completions = _client("the report")
    assert await draft_report({"mmsi": 1}, client) == "the report"
    assert completions.calls[0]["model"] == "some-future-model"

@pytest.mark.asyncio
async def test_draft_report_allows_empty_provider_content() -> None:
    client, _ = _client(None)
    assert await draft_report({"mmsi": 1}, client) == ""


@pytest.mark.asyncio
async def test_draft_report_bounds_large_track_history_payload() -> None:
    client, completions = _client("the report")
    incident = {
        "mmsi": 1,
        "hypothesis": "freeze_replay",
        "evidence": {"track_history": {"positions": [{"latitude": i} for i in range(5000)]}},
    }

    await draft_report(incident, client)

    content = completions.calls[0]["messages"][0]["content"]
    assert "GHAST // INCIDENT BRIEF" in content
    assert "```mermaid" in content
    assert '"position_count":5000' in content
    assert '"latitude":4999' in content
    assert '"latitude":1' not in content
