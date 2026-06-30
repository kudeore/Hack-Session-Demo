# Request traversal through the code

This is the end-to-end path for the governed use case.

## Entry point

```text
src/app.py
```

`main()` receives:

```text
customer_id
user_message
requested_action
```

It calls:

```text
run_case(customer_id, user_message, requested_action)
```

## Graph orchestration

```text
src/graph.py
```

`run_case()` builds the LangGraph workflow and creates initial state:

```text
customer_id
user_message
requested_action
audit
```

## Step-by-step traversal

| Step | Code | What happens | Governance point |
|---|---|---|---|
| 1 | `load_registries_node` | Loads use case, agent card, and skill registry | Agent is defined by approved metadata |
| 2 | `IntentRiskClassifierSkill` | Classifies intent and risk | LLM/SLM used for judgement, not final action |
| 3 | `AdversarialInputGuardSkill` | Checks prompt injection, tool escalation, data exfiltration, prompt extraction, and obfuscation | High-confidence attacks are stopped before customer data access |
| 4 | `CustomerFactsRetrieverSkill` | Calls secure data API as runtime-attested skill principal | Agent cannot directly access customer data |
| 5 | `SecureCustomerDataAPI` | Applies IAM, purpose, resource, action, and field filtering | Data API enforces the boundary |
| 6 | `PolicyRetrieverSkill` | Retrieves approved policy chunks | LLM gets grounded policy context |
| 7 | `PolicyReasonerSkill` | Reasons over policy and LLM-safe facts only | Raw customer data is not passed to LLM |
| 8 | `RefundCalculatorSkill` | Calculates amount deterministically | No LLM refund amount estimation |
| 9 | `PolicyAsCodeEvaluatorSkill` | Applies versioned deterministic rules | Policy decision is traceable by rule ID |
| 10 | `ToolFirewallSkill` | Blocks unsafe action | Agent cannot execute blocked tools |
| 11 | `HandoffManagerSkill` | Creates manual review case | Sensitive cases are routed to humans |
| 12 | `CustomerResponseWriterSkill` | Drafts empathetic response | LLM writes communication, not decision |
| 13 | `OutputVerifierSkill` | Checks false promises and unsafe wording | Final response is verified before send |

## Suspicious-input branch

If the adversarial guard returns `block_before_data_access`, the graph routes to:

```text
security_handoff_node
  -> response_writer_skill
  -> output_verifier_skill
  -> END
```

The following steps are deliberately skipped:

```text
customer_facts_skill
policy_retriever_skill
policy_reasoner_skill
refund_calculator_skill
policy_as_code_skill
tool_firewall_skill
normal handoff_skill
```

Suspicious input does not reach customer data, retrieval, policy reasoning, or tools.

## Audit

Every skill runs through:

```text
src/skill_runtime.py
```

The wrapper appends structured audit records using:

```text
src/audit.py
```

Audit evidence captures:

```text
Who acted?
What data was accessed?
Which policy version was used?
Which rule triggered?
Which tool was blocked?
Which human team received the handoff?
What final response was approved?
```
