# D14: board _mergeLead carries the after-the-yes unit

The side with the larger afterYesAt supplies nextStep/equityVerified/keepOrSell/apptUnverified/next and its stage; stageAt unions (earliest per stage); an older push never walks a stage back; `replied` is sticky; DO NOT CONTACT / Dead are never overridden and get no next step. Mirrors send_server._merge_note (unchanged).

Not LEGAL REVIEW per the skill's trigger list; DNC/Dead precedence is stated in docs/agent-teams/runs/2026-10-30-boardmerge.md and proved by `_boardmergetest.py`.

Tests: `_boardmergetest` (fails on 8a575e4, passes), `_boardstagetest`, `_verifytraintest`, `_callwiringtest`, `_mergenotetest`, `_funnelparitytest`, `_nosynctest`, `_queueflowtest`, `_cmtabtest`.
