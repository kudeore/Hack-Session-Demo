from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "configs"
DATA_DIR = ROOT / "data"
IAM_POLICY_FILE = CONFIG_DIR / "iam_policy.yaml"


class AccessDenied(Exception):
    """Raised when a principal is not allowed to call the secure data API."""


@dataclass
class IamDecision:
    """Small, auditable IAM decision object returned by the API boundary."""

    allowed: bool
    principal: str
    action: str
    resource: str
    purpose: str
    reason: str
    allowed_fields: List[str]
    denied_fields: List[str]
    iam_policy_id: str
    iam_policy_version: str
    runtime_attested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "principal": self.principal,
            "action": self.action,
            "resource": self.resource,
            "purpose": self.purpose,
            "reason": self.reason,
            "allowed_fields": self.allowed_fields,
            "denied_fields": self.denied_fields,
            "iam_policy_id": self.iam_policy_id,
            "iam_policy_version": self.iam_policy_version,
            "runtime_attested": self.runtime_attested,
        }


class SecureCustomerDataAPI:
    """
    Secure customer data boundary used by approved skills.

    The agent has no direct data entitlement. Only named and runtime-attested
    skill service accounts can call customer-data APIs. The API checks principal,
    action, resource, purpose, and data contract, then returns only the minimum
    required fields.

    In production, this boundary would usually sit behind an API gateway, IAM
    service, policy engine, data access layer, and immutable audit logging.
    """

    def __init__(self) -> None:
        policy_file = yaml.safe_load(IAM_POLICY_FILE.read_text(encoding="utf-8"))
        self.policy = policy_file["iam_policy"]

    def authorize(self, principal: str, action: str, resource: str, purpose: str, contract: str, runtime_attested: bool = False) -> IamDecision:
        principal_policy = self.policy.get("principals", {}).get(principal)
        contract_policy = self.policy.get("data_contracts", {}).get(contract, {})

        allowed_fields = list(contract_policy.get("allowed_fields", []))
        denied_fields = list(contract_policy.get("denied_fields", []))

        allowed = True
        reason = "allowed_by_principal_purpose_and_data_contract"

        if principal_policy is None:
            allowed = False
            reason = "unknown_principal"
        elif action not in principal_policy.get("allowed_actions", []):
            allowed = False
            reason = "action_not_allowed_for_principal"
        elif resource not in principal_policy.get("resources", []):
            allowed = False
            reason = "resource_not_allowed_for_principal"
        elif purpose not in principal_policy.get("purpose_binding", []):
            allowed = False
            reason = "purpose_not_allowed_for_principal"
        elif not runtime_attested and self.policy.get("api_controls", {}).get("require_runtime_attestation", False):
            allowed = False
            reason = "missing_runtime_attestation"
        elif not allowed_fields:
            allowed = False
            reason = "data_contract_not_found_or_empty"

        return IamDecision(
            allowed=allowed,
            principal=principal,
            action=action,
            resource=resource,
            purpose=purpose,
            reason=reason,
            allowed_fields=allowed_fields if allowed else [],
            denied_fields=denied_fields,
            iam_policy_id=self.policy["id"],
            iam_policy_version=self.policy["version"],
            runtime_attested=runtime_attested,
        )

    def read_booking_minimum(self, principal: str, customer_id: str, purpose: str, runtime_attested: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return a minimum booking view after IAM and field-level filtering."""

        decision = self.authorize(
            principal=principal,
            action="customer.read_booking_minimum",
            resource="booking",
            purpose=purpose,
            contract="booking.minimum_fields",
            runtime_attested=runtime_attested,
        )
        if not decision.allowed:
            raise AccessDenied(json.dumps(decision.to_dict(), ensure_ascii=False))

        raw_booking = self._read_booking_raw(customer_id)
        filtered_booking = self._filter_fields(raw_booking, decision.allowed_fields)
        return filtered_booking, decision.to_dict()


    def read_booking_minimum_by_booking_id(
        self,
        principal: str,
        booking_id: str,
        purpose: str,
        runtime_attested: bool = False,
        customer_name_hint: str | None = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Return a minimum booking view after IAM and field-level filtering using booking_id.

        This mirrors a real customer support flow where the user starts with a
        booking reference and message. The API may use the provided name for
        verification internally, but it does not return customer_name to the agent
        or LLM. It returns only customer_name_match as a non-sensitive verification
        signal when that field is in the approved data contract.
        """

        decision = self.authorize(
            principal=principal,
            action="customer.read_booking_minimum_by_booking_id",
            resource="booking",
            purpose=purpose,
            contract="booking.minimum_fields",
            runtime_attested=runtime_attested,
        )
        if not decision.allowed:
            raise AccessDenied(json.dumps(decision.to_dict(), ensure_ascii=False))

        raw_booking = self._read_booking_raw_by_booking_id(booking_id)
        raw_booking = dict(raw_booking)
        if customer_name_hint:
            raw_booking["customer_name_match"] = self._normalise_name(customer_name_hint) == self._normalise_name(raw_booking.get("customer_name", ""))
        else:
            raw_booking["customer_name_match"] = None

        filtered_booking = self._filter_fields(raw_booking, decision.allowed_fields)
        return filtered_booking, decision.to_dict()

    def read_relevant_chat_summary(
        self,
        principal: str,
        customer_id: str,
        booking_id: str,
        purpose: str,
        runtime_attested: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Return risk-signal summaries from chat logs, not raw transcripts."""

        decision = self.authorize(
            principal=principal,
            action="customer.read_relevant_chat_summary",
            resource="chat_history",
            purpose=purpose,
            contract="chat_history.relevant_summary",
            runtime_attested=runtime_attested,
        )
        if not decision.allowed:
            raise AccessDenied(json.dumps(decision.to_dict(), ensure_ascii=False))

        chat_summary = self._build_chat_summary(customer_id=customer_id, booking_id=booking_id)
        return chat_summary, decision.to_dict()

    @staticmethod
    def _read_booking_raw(customer_id: str) -> Dict[str, Any]:
        with (DATA_DIR / "bookings.csv").open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["customer_id"] == customer_id:
                    row["ticket_amount"] = float(row["ticket_amount"])
                    row["refund_amount_estimate"] = float(row["refund_amount_estimate"])
                    row["customer_verified"] = row["customer_verified"].lower() == "true"
                    return row
        raise ValueError(f"No booking found for customer_id={customer_id}")

    @staticmethod
    def _read_booking_raw_by_booking_id(booking_id: str) -> Dict[str, Any]:
        with (DATA_DIR / "bookings.csv").open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["booking_id"].strip().lower() == str(booking_id).strip().lower():
                    row["ticket_amount"] = float(row["ticket_amount"])
                    row["refund_amount_estimate"] = float(row["refund_amount_estimate"])
                    row["customer_verified"] = row["customer_verified"].lower() == "true"
                    return row
        raise ValueError(f"No booking found for booking_id={booking_id}")

    @staticmethod
    def _normalise_name(value: str) -> str:
        return " ".join(str(value).strip().lower().split())

    @staticmethod
    def _filter_fields(raw: Dict[str, Any], allowed_fields: Iterable[str]) -> Dict[str, Any]:
        return {field: raw[field] for field in allowed_fields if field in raw}

    @staticmethod
    def _build_chat_summary(customer_id: str, booking_id: str) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        with (DATA_DIR / "chat_history.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                if item["customer_id"] == customer_id and item["booking_id"] == booking_id:
                    message = item["message"].lower()
                    risk_signal = (
                        "prior_misinformation"
                        if "within 90 days after travel" in message
                        else "none"
                    )
                    summary.append(
                        {
                            "customer_id": item["customer_id"],
                            "booking_id": item["booking_id"],
                            "channel": item["channel"],
                            "timestamp": item["timestamp"],
                            "evidence_type": "official_channel_chat_summary",
                            "risk_signal": risk_signal,
                        }
                    )
        return summary


def create_llm_safe_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce the exact fact view that may be passed to LLM/SLM skills.

    Keep deterministic skills rich enough to operate, but keep LLM context
    minimised so data minimisation is enforced before the model receives
    context.
    """

    booking = facts.get("booking", {})
    refund_estimate = float(booking.get("refund_amount_estimate", 0))

    return {
        "booking": {
            "booking_id": booking.get("booking_id"),
            "booking_channel": booking.get("booking_channel"),
            "travel_status": booking.get("travel_status"),
            "fare_type": booking.get("fare_type"),
            "customer_verified": booking.get("customer_verified"),
            "refund_amount_band": (
                "below_threshold" if refund_estimate < 500 else "above_or_equal_threshold"
            ),
        },
        "prior_misinformation_flag_from_logs": facts.get("prior_misinformation_flag_from_logs", False),
        "relevant_chat_history_summary": facts.get("relevant_chat_history_summary", []),
        "data_minimization_note": (
            "LLM/SLM sees only this minimized view. Raw customer records and raw chat "
            "messages remain behind the secure customer data API."
        ),
    }
