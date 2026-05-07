# C-5 — Quality Management Certification (ISO 9001:2015).
#
# OPTIONAL criterion. The semantics differ from mandatory ones:
#   * Present + valid  → PASS (gives the bidder credit).
#   * Absent           → PASS (does not disqualify; the criterion is preferred).
#   * Low confidence   → ABSTAIN (still need certainty before crediting).
#
# A procurement officer reading the demo can immediately see why an absent
# ISO does not penalise Bidder C while an absent mandatory criterion would.

package praman.criteria.c5

import data.praman.common
import rego.v1

default result := {
	"verdict": "ABSTAIN",
	"rule_id": "c5.abstain.no_rule_matched",
	"reason": "No rule body matched the input. Defaulting to ABSTAIN.",
	"bindings": {},
}

result := common.abstain_low_confidence("c5", input.fact) if {
	common.is_low_confidence(input.fact)
}

# OPTIONAL — absent ISO is acceptable, not a FAIL.
result := {
	"verdict": "PASS",
	"rule_id": "c5.pass.optional_absent",
	"reason": "C-5 (ISO 9001) is an optional / preferred criterion. Bidder did not submit but is not disqualified.",
	"bindings": {"iso_present": false},
} if {
	not common.is_low_confidence(input.fact)
	common.is_missing(input.fact)
}

result := {
	"verdict": "PASS",
	"rule_id": "c5.pass.iso_present",
	"reason": "Bidder submitted a valid ISO 9001:2015 certificate.",
	"bindings": {
		"iso_present": true,
		"value": input.fact.value,
	},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.iso_present == true
}

# Submitted something but it doesn't look like an ISO 9001:2015 cert.
result := {
	"verdict": "ABSTAIN",
	"rule_id": "c5.abstain.value_unrecognised",
	"reason": sprintf(
		"Submitted value %q does not look like an ISO 9001:2015 certificate. Manual review required.",
		[input.fact.value],
	),
	"bindings": {"value": input.fact.value, "iso_present": false},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.iso_present == false
}
