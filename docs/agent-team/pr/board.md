# Board: after-the-yes counts, Overdue follow-ups, equity card, two reports

Plain English: the board now counts the stages Call Mode writes after a yes (Appointment, Offer made, Under contract, Assigned, CLOSED) from the note fields (status, stageAt, nextStep.date / next), including leads that reached a rung and were later marked Dead. A new "Overdue follow-ups" strip lists leads whose next-step date has passed, oldest first, and says how many open deals have no dated next step. Each card shows the baked `eqc` state and whether a figure may be quoted (net shown only when may_quote). outcome_learn.py prints the same after-the-yes counts. Two read-only reports: reports/funnel_truth.py (dials, conversations, appointments, offers, contracts per day) and reports/followup_gaps.py (talked-or-later leads without a dated next step).

Stacked on origin/pr/364 (rebased): #364 changed the CONTACTED list in `_funnelStats`, the same lines this rewrites. Merge #364 first; the CONTACTED list here is #364's.

Tests (all with DEALFLOW_DIR=<temp>): `python _boardstagetest.py`, `_funneltruthtest.py`, `_followupgapstest.py`, `_outcomelearntest.py`, `_funnelparitytest.py`, `_equityboardtest.py`.

Exposure: none (display and counts only; no gate, send, suppression, stay or spend change). The owned suppression functions are untouched.
Flags: none. Visible after the next refresh-dealflow.bat. `eqc` appears on cards once the equity branch is merged (absent = no chip).
Owned-surface bugs found: none.
Note: a stageAt stamp counts a rung even after status becomes Dead; a CLOSED/Dead lead is never in the overdue list.
