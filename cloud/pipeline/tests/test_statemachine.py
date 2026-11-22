"""pipeline.asl.json renders to valid JSON and every path ends where spec 30 §3 says."""
import json
import re
from pathlib import Path

ASL = Path(__file__).resolve().parents[1] / "statemachine" / "pipeline.asl.json"


def render():
    text = ASL.read_text()
    names = set(re.findall(r"\$\{(\w+)\}", text))
    assert names == {"plan_batches_arn", "check_batch_arn", "validate_batch_arn",
                     "quarantine_batch_arn", "transform_batch_arn", "verify_batch_arn",
                     "alert_topic_arn"}
    for n in names:
        text = text.replace("${%s}" % n, f"arn:aws:lambda:us-east-1:123:function:{n}")
    # templatefile() reads every "${" or "%{" as a template expression, so none may be left
    assert "${" not in text and "%{" not in text
    return json.loads(text)


def nexts(state):
    out = []
    if "Next" in state:
        out.append(state["Next"])
    if "Default" in state:
        out.append(state["Default"])
    out += [c["Next"] for c in state.get("Choices", [])]
    out += [c["Next"] for c in state.get("Catch", [])]
    return out


def check_scope(scope):
    states = scope["States"]
    assert scope["StartAt"] in states
    for name, st in states.items():
        for n in nexts(st):
            assert n in states, f"{name} -> {n} missing"
        if st["Type"] in ("Task", "Map", "Pass") and "Next" not in st:
            assert st.get("End"), name
    # every state reachable
    seen, todo = set(), [scope["StartAt"]]
    while todo:
        s = todo.pop()
        if s not in seen:
            seen.add(s)
            todo += nexts(states[s])
    assert seen == set(states), set(states) - seen
    return states


def test_top_level():
    asl = render()
    states = check_scope(asl)
    assert asl["StartAt"] == "PlanBatches"
    assert states["Batches"]["MaxConcurrency"] == 4


def test_map_iteration_ends():
    states = check_scope(render()["States"]["Batches"]["ItemProcessor"])
    ends = {n for n, s in states.items() if s["Type"] in ("Succeed", "Fail")}
    assert ends == {"Published", "Quarantined", "StopAlreadyProcessed", "StopAlreadyQuarantined",
                    "NoData", "InvalidInput", "PipelineFailed"}
    for name in ("CheckBatch", "ValidateBatch", "QuarantineBatch", "TransformBatch", "VerifyBatch"):
        st = states[name]
        assert st["Retry"][0]["MaxAttempts"] == 2
        assert st["Catch"][0]["Next"] == "NotifyFailure"
    choices = {c["StringEquals"]: c["Next"] for c in states["AlreadyProcessed?"]["Choices"]}
    assert choices == {"ALREADY_PROCESSED": "StopAlreadyProcessed",
                       "ALREADY_QUARANTINED": "StopAlreadyQuarantined", "NO_DATA": "NoData",
                       "NEW": "ValidateBatch", "CHANGED": "ValidateBatch"}
    assert states["AlreadyProcessed?"]["Default"] == "InvalidInput"
