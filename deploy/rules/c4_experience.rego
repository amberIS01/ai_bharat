# C-4 — Past Experience: at least 3 similar works completed in the last 5
# years, each ≥ Rs. 1,50,00,000 (1.5 Cr).
#
# The Python helper splits the LLM-extracted "works" string into individual
# work records, parses each value into INR, and counts qualifying entries.

package praman.criteria.c4

import data.praman.common
import rego.v1

required_count := 3
work_threshold_inr := 15000000

default result := {
	"verdict": "ABSTAIN",
	"rule_id": "c4.abstain.no_rule_matched",
	"reason": "No rule body matched the input. Defaulting to ABSTAIN.",
	"bindings": {},
}

result := common.abstain_low_confidence("c4", input.fact) if {
	common.is_low_confidence(input.fact)
}

result := common.abstain_missing("c4") if {
	not common.is_low_confidence(input.fact)
	common.is_missing(input.fact)
}

result := {
	"verdict": "PASS",
	"rule_id": "c4.pass.qualifying_works_meet_count",
	"reason": sprintf(
		"Bidder lists %v similar works at or above Rs. %v (required: %v).",
		[input.fact.qualifying_count, work_threshold_inr, required_count],
	),
	"bindings": {
		"qualifying_count": input.fact.qualifying_count,
		"required_count": required_count,
		"work_threshold_inr": work_threshold_inr,
		"work_count_total": input.fact.work_count,
	},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.qualifying_count >= required_count
}

result := {
	"verdict": "FAIL",
	"rule_id": "c4.fail.insufficient_qualifying_works",
	"reason": sprintf(
		"Bidder lists only %v similar works at or above Rs. %v (required: %v).",
		[input.fact.qualifying_count, work_threshold_inr, required_count],
	),
	"bindings": {
		"qualifying_count": input.fact.qualifying_count,
		"required_count": required_count,
		"work_threshold_inr": work_threshold_inr,
		"work_count_total": input.fact.work_count,
	},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.qualifying_count < required_count
}
