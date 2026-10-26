# Tribunal 2 (appeal) brief

Folder: this one. Read CONTEXT.md, SYNTHESIS.md, PROPOSALS.md, PANEL-01..10.md (skim the summaries), TRIBUNAL-1-A.md, TRIBUNAL-1-B.md, TRIBUNAL-1-C.md, VERIFY.md (verified facts: Frappe has no confirmed official Postgres support and ERPNext Postgres CI is opt-in; Odoo CE is Postgres-only and l10n_do has no e-CF, third-party e-CF modules cost USD 59-299; no ERPNext DR folder or Frappe DR e-CF app found; ERPNext GPL-3, Frappe MIT, Odoo CE LGPL-3; 190 PSFE providers; ~100k emitters; ~64% use DGII's free facturador; 15 Nov 2026 last cohort; no public per-document price; Alegra/Alanube alliance only partly sourced).

## Candidate plan from tribunal 1 (two of three judges; the third differs on the default)
"P5 then P1": the owner's first 45-60 days go to certification of the owner's own RNC (first real TesteCF, DGII-sourced fixtures, CerteCF, PSFE application), LICENSE file, ADR 0010 accepted/amended and its open signer risks closed, mutation testing automated on fiscal/money paths, spec split; connectors (Odoo first) and a portal-lite as sales channels; day-90 numeric gate opens the thin Python metadata layer (P1) only with pilots, signed MRR and unprompted demand; Frappe/Odoo-connector route is the named fallback if P1's first slice overruns 10 working days. Judge B wanted Frappe as the breadth default and a cash-first bridge; the new facts weaken Frappe-on-MariaDB as default but not the cash-first point.

## Your task
You are an appeal judge. 1) Try hard to break the candidate plan: what is the single most likely way the owner fails following it? Is there a better plan nobody proposed (for example Odoo CE as the breadth base since it is Postgres-only, a different sequence, or P1 limited to one vertical)? 2) Write YOUR OWN final recommended plan: what to do in the next 14 days, 90 days, 12 months; the gate in numbers; what NOT to do; the folder/repo structure; what would make you change your mind. 3) Say where you agree and disagree with the candidate, and rate your confidence (0-100%) in your final plan with the reason.

Write `TRIBUNAL-2-<letter>.md`. Answer in English. Reply with a 6-line summary and the path.
