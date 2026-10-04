from supportops_agent.models import AgentRun, AgentStep


def test_agent_run_starts_with_empty_steps():
    run = AgentRun(ticket_id="ticket_001")

    assert run.ticket_id == "ticket_001"
    assert run.steps == []
    assert run.run_id


def test_agent_step_has_generated_id():
    step = AgentStep(
        step_number=1,
        step_type="tool_call",
        input={"order_id": "order_001"},
    )

    assert step.step_number == 1
    assert step.step_type == "tool_call"
    assert step.input == {"order_id": "order_001"}
    assert step.step_id

