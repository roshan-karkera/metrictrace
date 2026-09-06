# 0001. Grain and timezone

Status: accepted
Date: TODO

## Grain

One row of the fact table is: TODO write the sentence.

## Dimensions kept

TODO

## Dimensions deliberately dropped

TODO, and why.

## Nulls

For each column: is null allowed, and what does it mean when present?

## Timezone

SMARD returns epoch milliseconds. Germany observes daylight saving, so one hour
is duplicated in October and one is missing in March.

Decision: TODO (store UTC and convert at the edge, or store local).
Reason: TODO
Consequence for the duplicated and missing hour: TODO
