# Presenza: proposals by shift, not by event — design

Status: approved (2026-10-15). Supersedes the rules in
`presenza.py` as of 0d7c726.

## 1. Goal

The wifi rules are event-driven (morning visit + 30, first afternoon visit from 16:00, later
visits from their own arrival) and every new case needs a branch. Replace them with two shifts, each defined by the same handful of
numbers, so the rules fit in config and every case the employer listed falls out of them
instead of needing its own branch.

## 2. Model

A shift is a window of the day. Within it:

- **start** = the first arrival in the start window, or a fixed default when there is none;
- **end** = the last departure in the shift, plus a tail when that departure is early enough;
- every departure and arrival in between is ignored.

| shift     | start window  | default start | end                  | tail                        |
|-----------|---------------|---------------|----------------------|-----------------------------|
| morning   | 07:30–08:15   | none          | last departure < 12:00 | +30 min if departure ≤ 08:30 |
| afternoon | before 15:45  | 15:45         | last departure       | none                        |

Morning: she arrives, then leaves for the school run. Leaving by 08:30 means she walks the
children to school, so +30 min. If she comes back (e.g. 08:45–09:30, she used the employer's
car) the last departure is later than 08:30 and the tail drops out by itself: no re-connect
rule.

Afternoon: she may or may not pass by the house before picking the children up. An arrival
before 15:45 means she did, and the shift starts there; otherwise she went straight to school
and the shift starts at 15:45. An arrival after 15:45 is the return with the children and
moves nothing. The end is the last departure.

## 3. Exceptions are not rules

Illness, changed school hours, days off: not modelled. tata already only *proposes*; the
employer confirms each punch. A day that doesn't fit the shape is flagged **anomalous** and
corrected by hand:

- no morning arrival in the start window;
- a departure outside every shift;
- a shift whose last stay has no departure (home all day, still home) or, for the morning,
  ends past noon;
- any stay with an unknown end (`lost`), as today.

A day without any wifi event is a day off, not an anomaly.

## 4. Decided

- Afternoon start fixed at 15:45, not "return with the children − 30 min": a stop at the bar
  after school would make the return late and the punch wrong.

## 5. Out of scope

Live automations: proposals stay a batch over the Home Assistant history, so a rule change
re-proposes the past.
