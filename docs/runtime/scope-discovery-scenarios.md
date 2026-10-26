# Scope-discovery worked scenarios

**Stable identity:** `SCOPE-DISCOVERY`, revision `S-1`. **Status:** Hypothetical inputs and expected outcomes only.

These scenarios check the [scope-discovery rules][discovery], the [stop route][stop] and its resume paragraph by walking each execution-time discovery to its outcome. Each row names the governing text that decides it. They are not tests of wording, issue records, provider states or runtime enforcement. No scenario creates, moves, approves, starts or launches a real issue or thread.

**Rule keys.** **[D]** = [scope discovery during execution][discovery]; **[X]** = [stop route][stop], including its blocker record and **Resume** paragraph; **[M]** = [stage 4 material change][execute]; **[P]** = [stage 3 proposal gate][proposal]; **[E]** = [stage 8][deliver] step 4 and the [estimate outcome record][outcome]; **[I]** = [intake][intake]; **[B]** = [estimate outcomes and discovered scope][size]; **[A]** = [Work Item Plan amendment][plan]; **[T]** = [state transitions][states].

Every scenario starts from an issue with an approved roadmap, in **In Progress**, with lifecycle outcome `active`.

## 1. Ordinary overrun

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Estimated 1,000 lines; actual 1,250 lines; same deliverables and criteria | Estimate variance. Continue; no blocker, no new issue and no new approval. Record 1,000 against 1,250 and the reasons in the estimate outcome record at completion. No work is lost and nothing is launched. | **[D]** "It is not a blocker and needs no new approval."; **[B]** "Estimate variance inside the authorized scope does not change the plan." |

## 2. Optional improvement

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| A helper could be simplified, but the criteria are met without it | Record a separate issue, linked as related, through task-free project administration. It does not block; work continues. The new issue is only captured: it is neither approved nor launched. | **[D]** "Record it as a separate issue linked as related through task-free project administration, never as blocking, and continue." |

## 3. Necessary prerequisite: stop route

Input: the criteria need a lifecycle operation that does not exist. Adding it is outside the approved scope and forms a reviewable deliverable of its own.

| Step | Expected outcome | Decided by |
| --- | --- | --- |
| Classify | Necessary prerequisite, not variance and not an optional improvement. | **[D]** "needed to meet the criteria, outside the approved scope and a reviewable deliverable of its own"; **[B]** "becomes a separate blocking Work Item and pauses the affected work, rather than expanding the current Work Item". |
| Stop | Pause the whole issue; verify in-flight step handoffs or record them as unfinished. Do not start the missing operation. | **[X]** "Pause the whole issue at a consistent point." and "Do not start the prerequisite." |
| Preserve | Commit unfinished work to the issue branch and push it. Open no PR. Record a lifecycle `checkpoint` and outcome `blocked` citing the pushed commit. No work is lost. | **[X]** "commit it to the issue branch and push that branch. Do not open a PR"; **[X]** "add no lifecycle state or storage"; **[T]** "`active ↔ blocked`". |
| Prerequisite issue | Create it with a complete scoping record through intake. It stays in its capture status or Planned; it is not approved. | **[X]** "It stays in its capture status or Planned; creation is not approval." |
| Link and status | Link the prerequisite as blocking the original; move the original to **Blocked**; read both back. | **[X]** "Link it as blocking the original issue" and "read both back". |
| Report | Report the filled blocker record, ending "prerequisite neither executed nor launched", and stop. The prerequisite later gets its own planning, approval and master thread. | **[X]** "The master never executes the prerequisite or launches its thread". |

## 4. Resume after the prerequisite merges

| Input | Expected outcome | Decided by |
| --- | --- | --- |
| Prerequisite merged into `main`, merge verified; remaining original scope unchanged after reassessment | Merge `main` into the issue branch and reassess. Record the reassessment, set outcome `active`, move the issue to **In Progress** with read-back and continue under the existing approval. The preserved branch work is reused, not redone. | **[X]** "Merge `main` into the issue branch, reassess the remaining original scope against the approved roadmap"; **[X]** "set the lifecycle `outcome` back to `active`"; **[T]** "`active ↔ blocked`". |

## 5–10. Edge cases

| # | Input | Expected outcome | Decided by |
| --- | --- | --- | --- |
| 5 | An optional improvement was recorded as blocking | Reclassify: it is not needed for the criteria, so it is related, not blocking. Remove the blocker; work continues. | **[D]** "not needed to meet the acceptance criteria" and "never as blocking"; **[I]** step 3 "use blocking relationships only for genuine prerequisites". |
| 6 | A 30-line necessary change outside the approved scope that cannot stand alone as a PR | Material change, not a separate issue. Record it, present a revised proposal and wait for explicit approval before the affected work. Unaffected approved work continues. | **[D]** "A small necessary out-of-scope change that cannot stand alone is a material change"; **[M]** "requires a revised proposal and explicit approval before affected implementation" and "Continue unaffected approved work where possible." |
| 7 | Prerequisite merged, but it changes the remaining scope materially | Present a revised proposal and wait. The earlier approval does not carry over; the merge itself is not approval. | **[X]** "If the remaining scope, criteria or authority changed materially, present a revised proposal and wait"; **[X]** "Child completion, the prerequisite's merge or the human's earlier approval of other work is never new approval."; **[P]** "Silence is not approval." |
| 8 | Prerequisite PR approved but not merged, its merge not yet verified on `main`, or the issue still open | Do not resume. The original stays **Blocked** with outcome `blocked`. | **[X]** "only after the prerequisite's verified merge into `main`". |
| 9 | Variance or refreshed `main` pushes the remaining work above 5 points or 1,000 lines | Apply intake step 3 through a revised proposal: seek smaller deliverables, or record the 8-point rationale and get human approval. Variance alone is still not a blocker. | **[D]** "When refreshed `main` or the variance breaks the decomposition rules for the remaining work, apply"; **[I]** step 3 "Above 5 points or 1,000 estimated lines, first seek smaller concrete deliverables". |
| 10 | Issue merged and accepted; a later comparable issue is being estimated | Record the estimate outcome in the roadmap and as an issue comment before **Done**, listing discovered splits and prerequisites. The later estimate consults it. | **[E]** "record the outcome in the roadmap's delivery section and as a comment on the issue"; **[B]** "later estimates consult those recorded outcomes"; **[I]** step 2 "of comparable completed issues when estimating". |

A discovery that changes the current plan rather than adding a separate deliverable follows the amendment path **[A]**: "returns to the amendment path".

[discovery]: contributor-workflow.md#scope-discovery-during-execution
[stop]: contributor-workflow.md#stop-route-for-a-necessary-prerequisite
[execute]: contributor-workflow.md#4-execute-within-the-approval-boundary
[proposal]: contributor-workflow.md#3-present-a-concrete-proposal-and-wait
[deliver]: contributor-workflow.md#8-submit-and-verify-delivery
[outcome]: contributor-workflow.md#estimate-outcome-record
[intake]: contributor-workflow.md#issue-intake-and-planned-readiness
[size]: ../../src/nplet/resources/organization/baseline.md#issue-sizing-and-classification
[plan]: ../../src/nplet/resources/organization/baseline.md#work-item-plan-contract
[states]: task-workspace.md#state-transitions
