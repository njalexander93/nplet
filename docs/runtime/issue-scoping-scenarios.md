# Issue-scoping worked scenarios

**Stable identity:** `ISSUE-SCOPING`, revision `S-1`. **Status:** Hypothetical inputs and expected outcomes only.

These scenarios check the [sizing and classification policy][size] and the [intake procedure][intake] by walking each case to its outcome. Each row names the governing text that decides it. They are not tests of wording, issue records, provider states or runtime enforcement. No scenario creates, moves, approves or starts a real issue.

**Rule keys.** **[S]** = [sizing][size] (point table, counting and readiness list); **[W]** = [Wave boundaries][wave]; **[I]** = [intake][intake]; **[R]** = [scoping record][record]; **[P]** = [stage 3 proposal][proposal].

## 1. Point boundaries

Each range includes both endpoints **[S]**.

| Estimated changed lines | Points | Treatment |
| --- | --- | --- |
| 249 / 250 | 1 / 2 | Normal |
| 500 / 501 | 2 / 3 | Normal |
| 750 / 751 | 3 / 5 | Normal |
| 1,000 / 1,001 | 5 / 8 | Normal upper limit / 8-point exception (scenario 8) |
| 2,000 / 2,001 | 8 / 13 | 8-point exception / classification only (scenario 2) |

## 2. Thirteen points

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Complete estimate of 2,400 lines | 13 points. Execution is rejected until the item is decomposed into smaller Work Items, each scoped and classified. | **[S]** "Thirteen points is a classification, not permission" and readiness item 2, "its points, 8 or fewer"; **[I]** step 3, "Never admit a 13-point issue". |

## 3. Counting changed lines

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Replace 100 existing lines with 100 new lines | 200 lines, 1 point. Net growth of zero and file count are irrelevant. | **[S]** "Replacing 100 lines with 100 new lines counts as 200". |
| 600 implementation lines; no figure for tests, fixtures, configuration or documentation | Estimate incomplete: no points, not Planned. Re-estimate every area, then derive points from the full total. | **[S]** readiness item 2, "a complete changed-line estimate"; **[I]** step 2; **[R]** per-area line. |

## 4. Range across a boundary

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Estimate 900–1,100 lines | Points from the upper bound: 8. Decompose first; otherwise the 8-point exception applies (scenario 8). The 900 lower bound does not make it 5 points. | **[S]** "900–1,100 is 8 points and needs decomposition or the 8-point exception". |

## 5–6. Intake and Planned readiness

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| New issue, rough description, no estimate, no Assurance Level | Stays in its capture status (for example Backlog). Report its link, its actual read-back status and that it is a placeholder, not Planned. List the missing fields: estimate basis, total and points; Assurance Level; any other empty required field. | **[I]** steps 1 and 4; **[S]** "Capture versus Planned readiness". |
| Same issue with a complete record: deliverable, criteria, per-area estimate of 400 lines (2 points), Agent Class, Assurance Level, Type, Execution, decomposition evaluated | May move to Planned; read back the status. Planned does not approve an implementation roadmap, admit a Wave or authorize execution. Starting it still needs bootstrap and roadmap approval. | **[I]** step 4 and "do not approve an implementation roadmap, admit a Wave or authorize execution"; **[S]** "Planned readiness never bypasses …". |

## 7. Normal decomposition

Input: one issue estimated at about 1,400 lines (8 points). A reasonable split exists.

| Increment | Scope (full supporting work, including tests and docs) | Lines | Points | Relationship |
| --- | --- | --- | --- | --- |
| A | Shared parser plus its tests | 500 | 2 | Related to B and C |
| B | Command that uses the parser, plus tests and docs | 600 | 3 | Related to A and C; blocked by A (it cannot work without the parser) |
| C | Independent configuration cleanup, plus docs | 300 | 2 | Related to A and B; not blocked |

Expected outcome: each increment is ≤5 points, separately scoped, classified and reviewable as its own PR. C gets no blocker because it does not depend on A or B. The 8-point exception is not needed. Decided by **[I]** step 3 and **[S]** readiness item 4: "Link sibling increments as related; use blocking relationships only for genuine prerequisites".

## 8. Eight-point exception

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| (a) 1,500 lines, record complete except the failed-split rationale | Not Planned; report the missing rationale. | **[S]** readiness item 5; **[I]** step 4. |
| (a) 1,500 lines, rationale recorded, no human approval | Not Planned; report the missing approval. | **[S]** "Human Leadership approves that exception before Planned". |
| (b) 1,500 lines; recorded reason why reasonable splitting fails; record field cites `SIMULATED-APPROVAL-1` | May move to Planned in this worked walk only. `SIMULATED-APPROVAL-1` is a label in this scenario. It is not a real human approval and carries no authority. A real walk needs an attributable approval from Human Leadership. | **[S]** readiness item 5; **[R]** "8-point rationale and human approval reference". |

## 9. Wave caps

| Candidate set (priority order) | Expected outcome | Decided by |
| --- | --- | --- |
| Six 2-point issues (12 points); 2,800 lines | Fits: 12 ≤ 12 points and 2,800 ≤ 3,000 lines. | **[W]** "within both 12 points and 3,000 estimated changed lines". |
| Four 3-point issues (12 points) recorded as 3,100 lines | Inputs are inconsistent: four 3-point issues total at most 4 × 750 = 3,000 lines. At least one issue is above 750 lines, so it is 5 points and needs re-estimation. The corrected set has 14 points and exceeds the point cap, so the re-pointed issue is not admitted. Whether selection then considers smaller lower-priority work is left to the selection algorithm. | **[S]** point table; **[W]** caps and "Exact selection algorithms … remain implementation details". |
| Eligible work totals 10 points | The Wave holds 10 points. Spare capacity is not a reason to add work. | **[W]** "Both are caps, not targets to fill". |

## 10. Independent classifications

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| 120-line change to an authorization check | 1 point, with Assurance Level R3 and Agent Class A3. Size does not lower the other classifications. | **[S]** "a small change may still need advanced reasoning or high assurance". |
| 1,800-line mechanical rename (8-point exception approved) | Agent Class A1 and Assurance Level R1 are valid. The size does not raise them. | **[S]** "a large mechanical change may need neither". |

## 11. Resumed Planned issue

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Planned issue recorded at 900 lines (5 points); on start, re-estimated at 1,150 lines | The roadmap proposal reports that the issue no longer fits (8 points, above 1,000 lines) and proposes re-scoping or decomposition. Reassess only this issue; do not clean up other Planned issues. Approval of the proposal is still required. | **[P]** step 1 "exceeds 1,000 lines … Reassess only the started issue; do not clean up the backlog"; **[S]** "no blanket backlog cleanup is required". |

[size]: ../../src/nplet/resources/organization/baseline.md#issue-sizing-and-classification
[wave]: ../../src/nplet/resources/organization/baseline.md#planning-and-wave-boundaries
[intake]: contributor-workflow.md#issue-intake-and-planned-readiness
[record]: contributor-workflow.md#scoping-record
[proposal]: contributor-workflow.md#3-present-a-concrete-proposal-and-wait
