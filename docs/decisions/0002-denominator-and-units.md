# 0002. The generation denominator, and units

Status: accepted, with one open item
Date: 2026-09-07

## Context

`renewable_share` is a ratio, and every argument about a ratio is an argument
about its denominator. Thirteen series are ingested and they are not all the
same kind of thing:

| category     | series                                                                   |
|--------------|--------------------------------------------------------------------------|
| renewable    | wind_onshore, wind_offshore, solar, hydro, biomass, other_renewable       |
| conventional | brown_coal, hard_coal, natural_gas, nuclear, other_conventional           |
| storage      | pumped_storage                                                           |
| load         | total_consumption                                                        |

## Decision 1: the denominator is renewable plus conventional generation

`renewable_share = sum(renewable) / sum(renewable + conventional)`

Three candidate denominators were possible and each gives a different number:

1. **Total generation (chosen).** Answers "what share of the electricity
   Germany generated came from renewables".
2. **Total generation plus pumped storage discharge.** Storage does not generate
   energy, it returns energy generated earlier. Counting it inflates the
   denominator with electricity already counted once when it was first produced.
3. **Total consumption (`total_consumption`).** Answers a different and equally
   valid question: "what share of the electricity Germany *used* was renewable".
   That is a consumption mix, not a generation mix, and it differs because of
   imports and exports.

Option 3 is not wrong. It is a different metric, and if it is ever wanted it gets
its own name (`renewable_share_of_consumption`) rather than quietly replacing
this one. Two definitions with one name is the failure this project exists to
prevent.

## Decision 2: pumped storage is its own category and is excluded

Pumped storage both consumes and returns electricity. It is categorised as
`storage` rather than forced into renewable or conventional, and it is excluded
from the generation denominator for the double counting reason above.

Consequence: `sum(renewable) + sum(conventional)` does not equal total system
supply, and it is not supposed to. Anyone reconciling this against a published
figure that includes storage will get a different number, which is why the
exclusion is stated in the metric definition itself and not only here.

## Decision 3: closed series stay in the denominator for periods they were open

`nuclear` is closed since April 2023. It is excluded from current metrics by
having no facts published, not by being removed from the category list. For any
historical period where nuclear generation existed, it belongs in the
denominator, because it genuinely was part of total generation then.

Removing a series from the category list would silently rewrite history. Letting
the gate withhold its facts leaves history intact and the present correct.

## Open item: the unit is not yet verified

The fact column is named `value`, not `value_mwh`, because the unit SMARD returns
has not been confirmed from their documentation. It is very likely MWh per hour
for generation series, but "very likely" is not a unit.

Until this is verified:

- no chart axis, dashboard tile or metric label may state a unit
- the column keeps the name `value`
- ratios such as `renewable_share` are unaffected, because the unit cancels

Action: confirm from the SMARD download centre documentation, then rename the
column and record the confirmation here. This is deliberately blocking for any
absolute figure and deliberately not blocking for ratios.
