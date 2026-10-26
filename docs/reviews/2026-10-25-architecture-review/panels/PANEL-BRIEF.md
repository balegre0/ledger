# Brief for every judging panel (Phase 3: 100 roles in 10 panels)

You are one panel of ten. Each panel embodies ten distinct roles from the tech world. Together the panels form 100
independent judges of the proposals in PROPOSALS.md.

## Read first (all in this folder)
1. CONTEXT.md (owner, goal, current repo, rules).
2. PROPOSALS.md (P0-P6 and the open questions).
3. R1.md-R8.md (the research: incumbents, styles, data, extensibility, language, ops/market, solo+AI, folder structure).
   Read them fully; they are the evidence. You may also read the repo read-only (C:\Users\brian\Desktop\ledger) to
   check a claim. Do not edit the repo. Do not search the web unless a role needs one specific fact the reports lack;
   if you do, cite the URL.

## How to judge
- Speak as each role in turn, with that role's incentives, experience and blind spots. Roles must disagree when their
  incentives differ; do not converge for politeness. Ground every argument in the reports (cite like "R3 §2.2" or a
  source number) or in the repo (file path), or mark it "[role judgment]".
- For each role: score every proposal P0-P6 from 0 to 10 (10 = best direction for THIS owner: solo founder in the DR,
  AI agents, CRM with ERP features, e-CF market deadline), give a one-line reason for the top and bottom score, name the
  role's favorite (a single proposal or an explicit hybrid/sequence like "P5→P1"), and state the one fact that would
  change the role's mind.
- Each role also gives one line on folder/code structure for its favorite (using R8).
- Then, as a panel: compute mean score per proposal, count favorites, write the panel's consensus recommendation (it
  may be a refined hybrid with concrete conditions), the three strongest objections to it, and the dissent.

## Output
Write `PANEL-<NN>.md` in this folder (NN given in your task) with:
1. A table: role | P0 | P1 | P2 | P3 | P4 | P5 | P6 | favorite.
2. Per-role paragraphs (reasons, mind-changer, folder line), 60-120 words each.
3. Panel summary: means, favorite counts, consensus recommendation, objections, dissent, kill criteria (signals that
   should make the owner abandon the recommendation).
Answer in English. Reply with: the means line, the favorite counts, and the consensus recommendation in 3-5 sentences.
