# C-1 — Minimum Annual Turnover.
#
# Pass condition: bidder's highest-year turnover (in INR, pre-parsed by the
# Python policy.parsers helper) is at least Rs. 5,00,00,000 (5 Crore).
#
# Input shape:
#   input.criterion.code           : "C-1"
#   input.criterion.mandatory      : true
#   input.fact.value               : original value string (e.g. "Rs. 6,80,00,000/-")
#   input.fact.value_inr           : pre-parsed integer rupees (e.g. 68000000)
#   input.fact.ocr_confidence      : aggregated confidence 0.0-1.0
#   input.fact.found               : did the LLM locate a value?
#   input.confidence_threshold     : ABSTAIN threshold (default 0.85)
#
# Output: {verdict, rule_id, reason, bindings}.

package praman.criteria.c1

import data.praman.common
import rego.v1

# Threshold encoded as Rego data. A real procurement officer can change this
# value without redeploying Praman code — a primary reason to keep policy
# logic out of Python.
threshold_inr := 50000000

# 1) Default — if no rule body matched, ABSTAIN. This is the "fail closed"
# semantic that protects the demo's never-silently-disqualify guarantee.
default result := {
	"verdict": "ABSTAIN",
	"rule_id": "c1.abstain.no_rule_matched",
	"reason": "No rule body matched the input. Defaulting to ABSTAIN.",
	"bindings": {},
}

# 2) Low confidence dominates everything else.
result := common.abstain_low_confidence("c1", input.fact) if {
	common.is_low_confidence(input.fact)
}

# 3) Missing value (and confidence high enough to trust the missing-ness).
result := common.abstain_missing("c1") if {
	not common.is_low_confidence(input.fact)
	common.is_missing(input.fact)
}

# 4) Pre-parsed amount missing — Python couldn't extract digits.
result := {
	"verdict": "ABSTAIN",
	"rule_id": "c1.abstain.unparseable_value",
	"reason": sprintf("Could not parse a rupee figure from %q. Manual review required.", [input.fact.value]),
	"bindings": {"value": input.fact.value},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	not is_number(input.fact.value_inr)
}

# 5) PASS — turnover meets or exceeds the threshold.
result := {
	"verdict": "PASS",
	"rule_id": "c1.pass.turnover_meets_threshold",
	"reason": sprintf(
		"Annual turnover Rs. %v matches or exceeds the threshold Rs. %v.",
		[input.fact.value_inr, threshold_inr],
	),
	"bindings": {
		"value_inr": input.fact.value_inr,
		"threshold_inr": threshold_inr,
		"value_str": input.fact.value,
	},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	is_number(input.fact.value_inr)
	input.fact.value_inr >= threshold_inr
}

# 6) FAIL — turnover below the threshold.
result := {
	"verdict": "FAIL",
	"rule_id": "c1.fail.turnover_below_threshold",
	"reason": sprintf(
		"Annual turnover Rs. %v is below the required threshold of Rs. %v.",
		[input.fact.value_inr, threshold_inr],
	),
	"bindings": {
		"value_inr": input.fact.value_inr,
		"threshold_inr": threshold_inr,
		"value_str": input.fact.value,
	},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	is_number(input.fact.value_inr)
	input.fact.value_inr < threshold_inr
}
