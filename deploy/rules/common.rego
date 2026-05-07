# Shared helpers used by every criterion rule.
#
# Pattern: each criterion package imports `data.praman.common` and reuses
# these helpers so the ABSTAIN-on-low-confidence + ABSTAIN-on-missing-value
# semantics stay identical across rules. The criterion-specific rules only
# encode the ACTUAL check (turnover ≥ X, GSTIN pattern matches, etc.).

package praman.common

import rego.v1

# True when the fact's confidence is below the configured threshold.
# Default threshold is 0.85; callers can override via input.confidence_threshold.
is_low_confidence(fact) if {
	fact.ocr_confidence < input.confidence_threshold
}

# True when the LLM didn't find a value or the value is empty.
is_missing(fact) if {
	fact.found == false
}

is_missing(fact) if {
	fact.found == true
	trim(fact.value, " \t\n\r") == ""
}

# Standard ABSTAIN payloads — kept identical across rules so the UI / audit
# log can render them uniformly.
abstain_low_confidence(criterion_code, fact) := {
	"verdict": "ABSTAIN",
	"rule_id": sprintf("%s.abstain.low_confidence", [criterion_code]),
	"reason": sprintf(
		"OCR / extraction confidence %v is below the threshold %v — manual review required.",
		[fact.ocr_confidence, input.confidence_threshold],
	),
	"bindings": {
		"ocr_confidence": fact.ocr_confidence,
		"confidence_threshold": input.confidence_threshold,
		"reason_if_low": fact.reason_if_low,
	},
}

abstain_missing(criterion_code) := {
	"verdict": "ABSTAIN",
	"rule_id": sprintf("%s.abstain.value_missing", [criterion_code]),
	"reason": "Bidder did not submit the value required by this criterion — manual review required.",
	"bindings": {},
}
