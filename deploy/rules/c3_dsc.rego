# C-3 — Valid Class-3 Digital Signature Certificate.
#
# Pass condition: Bidder's submitted DSC is Class-3, issued by a CCA-licensed
# CA, and (best effort) is currently valid. Class-3 has been the only DSC
# class issuable to anyone in India since 2021 (CCA simplification).

package praman.criteria.c3

import data.praman.common
import rego.v1

default result := {
	"verdict": "ABSTAIN",
	"rule_id": "c3.abstain.no_rule_matched",
	"reason": "No rule body matched the input. Defaulting to ABSTAIN.",
	"bindings": {},
}

result := common.abstain_low_confidence("c3", input.fact) if {
	common.is_low_confidence(input.fact)
}

result := common.abstain_missing("c3") if {
	not common.is_low_confidence(input.fact)
	common.is_missing(input.fact)
}

result := {
	"verdict": "PASS",
	"rule_id": "c3.pass.class3_present",
	"reason": sprintf(
		"Bidder's DSC is Class-3 (detected token: %q).",
		[input.fact.dsc_class],
	),
	"bindings": {"dsc_class": input.fact.dsc_class, "value": input.fact.value},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.dsc_class == "Class-3"
}

result := {
	"verdict": "FAIL",
	"rule_id": "c3.fail.not_class3",
	"reason": sprintf(
		"Bidder's DSC is not Class-3 (detected token: %q).",
		[input.fact.dsc_class],
	),
	"bindings": {"dsc_class": input.fact.dsc_class, "value": input.fact.value},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.dsc_class != ""
	input.fact.dsc_class != "Class-3"
}
