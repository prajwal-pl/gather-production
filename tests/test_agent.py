"""Agent / workspace assistant tests.

The contract under test:

* every fact in an answer comes from a tool observation;
* the offline fixture still produces a useful answer;
* a bad model turn degrades gracefully instead of breaking the chat;
* the agent is read-only (there is no mutation tool to invoke).
"""

from __future__ import annotations

import pytest

from gather.agents.assistant import Assistant
from gather.config import load_settings
from gather.ipc import Handlers, build_router
from gather.llm import BaseLLM, BudgetExceeded, LLMError, ProviderLLM, Usage
from gather.runtime import build_runtime
from gather.secrets import FileSecretStore


class ScriptedLLM(BaseLLM):
    """Returns queued decisions and records the prompts it was given."""

    def __init__(self, decisions: list[dict], *, fail: bool = False) -> None:
        self.decisions = list(decisions)
        self.prompts: list[str] = []
        self.fail = fail
        self.usage = Usage()

    def start_run(self) -> None:
        pass

    def complete(self, system: str, user: str, *, hint: str | None = None) -> str:
        self.prompts.append(user)
        if self.fail:
            raise LLMError("provider is down")
        return "Here is the final answer."

    def json(self, schema, system, user, *, hint=None):  # noqa: ANN001
        self.prompts.append(user)
        if self.fail:
            raise LLMError("provider is down")
        if not self.decisions:
            raise AssertionError("no scripted decisions left")
        return schema.model_validate(self.decisions.pop(0))


@pytest.fixture
def runtime(tmp_path):
    rt = build_runtime(
        load_settings(data_dir=tmp_path),
        log=lambda _message: None,
        force_fixture=True,
        secrets={},
    )
    yield rt
    rt.close()


def seed(runtime):
    task = runtime.store.create_task(
        runtime.org_id,
        title="Send the pricing deck",
        owner_email="priya@acme.test",
        due_date="2026-11-20",
        source_quote="I'll send the updated deck by Friday.",
    )
    runtime.store.add_user(runtime.org_id, "priya@acme.test", "Priya Raman")
    runtime.store.create_proposal(
        runtime.org_id,
        "task_approval",
        {"meeting": "Acme x Northwind", "tasks": [{"title": "Send the pricing deck"}]},
    )
    return task


# --------------------------------------------------------------------------- #
# Model-driven loop
# --------------------------------------------------------------------------- #


def test_agent_calls_a_tool_then_answers(runtime):
    seed(runtime)
    llm = ScriptedLLM(
        [
            {"action": "tool", "tool": "list_tasks", "arguments": {"status": "open"}},
            {"action": "final", "answer": "You have one open task: Send the pricing deck."},
        ]
    )
    runtime.llm = llm

    reply = Assistant(runtime).chat("what tasks are open?")

    assert reply["answer"].startswith("You have one open task")
    assert [call["tool"] for call in reply["tool_calls"]] == ["list_tasks"]
    assert reply["tool_calls"][0]["ok"] is True
    assert reply["steps"] == 2
    # The second planner turn saw the tool result, not just the question.
    assert "Send the pricing deck" in llm.prompts[-1]


def test_agent_unknown_tool_is_an_observation_then_recovers(runtime):
    seed(runtime)
    llm = ScriptedLLM(
        [
            {"action": "tool", "tool": "drop_database", "arguments": {}},
            {"action": "final", "answer": "I can only read data."},
        ]
    )
    runtime.llm = llm

    reply = Assistant(runtime).chat("delete everything")

    assert reply["answer"] == "I can only read data."
    assert reply["tool_calls"][0]["ok"] is False
    assert "Unknown tool" in reply["tool_calls"][0]["error"]
    assert "Available tools" in llm.prompts[-1]


def test_agent_recovers_when_the_model_fails(runtime):
    seed(runtime)
    runtime.llm = ScriptedLLM([], fail=True)

    reply = Assistant(runtime).chat("what is overdue?")

    assert reply["degraded"] is True
    assert "Send the pricing deck" in reply["answer"]
    assert reply["tool_calls"][0]["tool"] == "due_tasks"


def test_agent_never_exposes_a_mutation_tool(runtime):
    names = {tool["name"] for tool in Assistant(runtime).tool_catalog()}
    forbidden = {
        "create_task",
        "update_task",
        "approve_proposal",
        "reject_proposal",
        "send_email",
        "connect",
        "update_settings",
    }
    assert not names & forbidden


def test_agent_tools_are_scoped_to_the_workspace(runtime):
    other_org = runtime.store.ensure_org("Someone Else")
    foreign = runtime.store.create_task(other_org, title="Not your task")
    runtime.llm = ScriptedLLM(
        [
            {"action": "tool", "tool": "get_task", "arguments": {"task_id": foreign["id"]}},
            {"action": "final", "answer": "I could not find that task."},
        ]
    )

    reply = Assistant(runtime).chat("open that task")

    assert reply["tool_calls"][0]["ok"] is False
    assert "not found" in (reply["tool_calls"][0].get("error") or "")


# --------------------------------------------------------------------------- #
# Offline fixture planner
# --------------------------------------------------------------------------- #


def test_fixture_chat_answers_from_local_data(runtime):
    seed(runtime)

    reply = Assistant(runtime).chat("what tasks are open?")

    assert reply["fixture"] is True
    assert reply["degraded"] is False
    assert "Send the pricing deck" in reply["answer"]
    assert reply["tool_calls"][0]["tool"] == "list_tasks"


def test_fixture_setup_checklist_is_actionable(runtime):
    reply = Assistant(runtime).chat("is my workspace set up?")

    call = reply["tool_calls"][0]
    assert call["tool"] == "setup_checklist"
    assert "Setup" in reply["answer"] or "setup" in reply["answer"]
    assert "Settings" in reply["answer"]
    assert "Connections" in reply["answer"]


def test_fixture_meeting_question_does_not_match_latest_as_late(runtime):
    reply = Assistant(runtime).chat("what is the latest meeting?")

    assert reply["tool_calls"][0]["tool"] == "list_meetings"


# --------------------------------------------------------------------------- #
# IPC + budget
# --------------------------------------------------------------------------- #


def test_agent_chat_is_available_over_ipc(runtime, tmp_path):
    seed(runtime)
    handlers = Handlers(
        runtime,
        events=runtime.events,
        secrets=FileSecretStore(tmp_path / "secrets.json"),
        keys={},
        log=lambda _message: None,
    )
    router = build_router(handlers)
    chat = router.route("agent.chat")
    tools = router.route("agent.tools")
    assert chat is not None and tools is not None

    reply = chat.handler({"message": "what tasks are open?"})
    assert "Send the pricing deck" in reply["answer"]
    assert reply["tool_calls"]

    catalog = tools.handler({})
    assert any(tool["name"] == "workspace_overview" for tool in catalog["tools"])

    with pytest.raises(Exception):
        chat.handler({"message": "   "})


def test_provider_budget_is_per_run(monkeypatch):
    from gather.config import ModelSpec

    llm = ProviderLLM(
        (ModelSpec("groq", "test-model"),),
        max_calls=1,
        api_keys={"GROQ_API_KEY": "test-key"},
        log=lambda _message: None,
    )

    class _Reply:
        content = "ok"

    class _Model:
        def invoke(self, _messages):
            return _Reply()

    monkeypatch.setattr(llm, "_model", lambda _spec: _Model())

    llm.start_run()
    assert llm.complete("system", "user") == "ok"
    with pytest.raises(BudgetExceeded):
        llm.complete("system", "user")

    # A new run gets its own budget instead of failing for the process lifetime.
    llm.start_run()
    assert llm.complete("system", "user") == "ok"
