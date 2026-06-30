package refund.governance

# Reference policy-engine mapping.
# The Python runtime uses src/skills/policy_as_code.py for readable deterministic rules.

default auto_refund_prepare_allowed := false

manual_review_required if input.risk.bereavement_or_vulnerability
manual_review_required if input.risk.prior_misinformation_flag
manual_review_required if input.risk.prompt_injection_detected
manual_review_required if input.risk.complaint_or_legal_signal
manual_review_required if input.booking.travel_status == "completed"
manual_review_required if input.booking.travel_status == "partially_flown"
manual_review_required if input.booking.booking_channel == "third_party"
manual_review_required if input.refund.estimated_refund_amount >= 500
manual_review_required if input.policy_assessment.manual_review_required

auto_refund_prepare_allowed if {
  input.booking.customer_verified
  input.booking.booking_channel == "direct"
  input.booking.travel_status == "not_started"
  input.booking.fare_type == "flexible"
  input.booking.payment_method == "card"
  input.refund.estimated_refund_amount > 0
  input.refund.estimated_refund_amount < 500
  not manual_review_required
}

deny[msg] if {
  input.requested_action == "execute_refund"
  msg := "Agent cannot execute refund without required approval."
}

deny[msg] if {
  manual_review_required
  msg := "Manual review trigger blocks automation."
}
