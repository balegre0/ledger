# Architecture review, 2026-10-25

Was the architecture the wrong path for a CRM with ERP features? Start with [`VERDICT.md`](VERDICT.md).

## Files, in the order they were produced

| File | What it is |
|---|---|
| [`CONTEXT.md`](CONTEXT.md) | The brief every researcher and judge read first |
| [`research/R1.md`](research/R1.md) | How the incumbents are built (Salesforce, Dataverse, SAP, Oracle, Workday, ServiceNow, HubSpot, Odoo, Frappe, Twenty, Attio) |
| [`research/R2.md`](research/R2.md) | Architecture styles and decomposition (modular monolith, microservices, DDD, events, CQRS, cells) |
| [`research/R3.md`](research/R3.md) | Data: tenancy, custom fields, ledger, audit, reporting, search, migrations |
| [`research/R4.md`](research/R4.md) | Extensibility: metadata platforms, customization tiers, plugin isolation, upgrade tax |
| [`research/R5.md`](research/R5.md) | Language and stack, agent benchmarks, hiring |
| [`research/R6.md`](research/R6.md) | Scale, reliability, operations and the 2025-2026 market |
| [`research/R7.md`](research/R7.md) | A solo founder with AI agents; build, extend or rewrite; licences |
| [`research/R8.md`](research/R8.md) | Repository and module folder structure |
| [`SYNTHESIS.md`](SYNTHESIS.md) | Findings across R1-R7 and the seven proposals |
| [`PROPOSALS.md`](PROPOSALS.md) | The proposals as given to the judges (without the synthesis verdict) |
| [`panels/PANEL-BRIEF.md`](panels/PANEL-BRIEF.md) | How the 100 judges scored |
| `panels/PANEL-01.md` to `panels/PANEL-10.md` | Ten panels of ten roles: architecture, data, ERP/CRM domain, product and business, AI-assisted engineering, operations and security, UX and end users, legal and organization, stack advocates, big tech and ecosystems |
| [`tribunals/TRIBUNAL-1-BRIEF.md`](tribunals/TRIBUNAL-1-BRIEF.md), `TRIBUNAL-1-A.md` to `-C.md` | Tribunal 1: three judges, repo-checked |
| [`research/VERIFY.md`](research/VERIFY.md) | Fact verification against official sources (Frappe/Odoo and Postgres, licences, DGII figures) |
| [`tribunals/TRIBUNAL-2-BRIEF.md`](tribunals/TRIBUNAL-2-BRIEF.md), `TRIBUNAL-2-D.md`, `TRIBUNAL-2-E.md` | Appeal tribunal: red team and operator |
| [`VERDICT.md`](VERDICT.md) | The conclusion, including the 25-30 person scenario and the language question |

The working files referred to "this folder" while they lived in a session scratchpad; the relative names are
unchanged. Each research report marks primary sources, vendor claims, anecdotes and the facts it could not verify.
