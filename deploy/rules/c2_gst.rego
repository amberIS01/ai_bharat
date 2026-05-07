# C-2 — Valid GST Registration.
#
# Pass condition: Bidder's submitted GSTIN matches the canonical 15-character
# pattern used by GSTN: 2 digits (state code) + 10 PAN chars + 1 entity digit
# + Z + 1 alphanumeric checksum = `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][Z][0-9A-Z]$`.
#
# Real CRPF procurement teams also check the GSTIN against the live GSTN
# portal — out of scope for the demo, but the Rego layer is where you'd add
# such a side-call (data.gstn.is_active[input.fact.gstin]).

package praman.criteria.c2

import data.praman.common
import rego.v1

gstin_regex := `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][Z][0-9A-Z]$`

default result := {
	"verdict": "ABSTAIN",
	"rule_id": "c2.abstain.no_rule_matched",
	"reason": "No rule body matched the input. Defaulting to ABSTAIN.",
	"bindings": {},
}

result := common.abstain_low_confidence("c2", input.fact) if {
	common.is_low_confidence(input.fact)
}

result := common.abstain_missing("c2") if {
	not common.is_low_confidence(input.fact)
	common.is_missing(input.fact)
}

# Pre-parsed GSTIN missing — the Python helper couldn't find a 15-char token.
result := {
	"verdict": "ABSTAIN",
	"rule_id": "c2.abstain.no_gstin_token",
	"reason": sprintf("Could not isolate a GSTIN token from %q.", [input.fact.value]),
	"bindings": {"value": input.fact.value},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.gstin == ""
}

result := {
	"verdict": "PASS",
	"rule_id": "c2.pass.gstin_pattern_valid",
	"reason": sprintf("GSTIN %q matches the 15-character GSTN pattern.", [input.fact.gstin]),
	"bindings": {"gstin": input.fact.gstin},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.gstin != ""
	regex.match(gstin_regex, input.fact.gstin)
}

result := {
	"verdict": "FAIL",
	"rule_id": "c2.fail.gstin_pattern_invalid",
	"reason": sprintf("GSTIN %q does not match the canonical pattern.", [input.fact.gstin]),
	"bindings": {"gstin": input.fact.gstin, "expected_regex": gstin_regex},
} if {
	not common.is_low_confidence(input.fact)
	not common.is_missing(input.fact)
	input.fact.gstin != ""
	not regex.match(gstin_regex, input.fact.gstin)
}
