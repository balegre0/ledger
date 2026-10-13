# v2-M1b: the DGII spike (`countries/do`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the Dominican e-CF path end to end in week one, in parallel with the restructure (v2-M1a), as two new distributions the restructure does not move: `countries/do` (`ledger-country-do`: regime `do` as data, and channel `do-ecf-v1`: XML builders for types 31 and 32 and the RFCE, validation against DGII's vendored XSDs, DGII's XMLDSig signature, security code and QR URL, seed/token authentication, submission, TrackId polling, the status map with Aceptado Condicional, the synchronous RFCE path on `fc.dgii.gov.do`, buyer delivery with ARECF verification) and `countries/do/testing` (`ledger-country-do-testing`, dev-only: a fake DGII written from DGII's *Descripción Técnica*, a test PKI, xmlsec1 as independent verifier, samples, the `ledger-dgii-spike` CLI). A manual runbook takes it to DGII's real `TesteCF` once the owner has a test certificate and access.

**Architecture:** A pure library plus a thin httpx client. Nothing of the workspace is imported (no kernel, no platform, no service): the spike works whatever state the restructure is in, and import-linter enforces it. The regime is standard-library data (`ledger_country_do.regime`, with provisional local copies of the kernel v2 regime types). The channel is layered like every v2 package (spec §4.5): `domain` (stdlib + the regime: e-NCF, drafts, totals, routing, status map, printed data) ← `application` (ports and the emission use case) ← `adapters` (vendored XSDs, hardened XML, builders, the signing profile, DGII's client, the buyer client). The fake DGII is a Starlette app that checks with a second XSD implementation (xmlschema) and a second signature implementation (signxml); xmlsec1 in a container is the reference verifier. Integration into the runtime (`ChannelContext`, jobs, PostgreSQL, the signer module) is v2-M3/M4's.

**Tech Stack:** Python 3.15, uv 0.13 workspace, lxml 6.1.3 (+ types-lxml), cryptography 50.0.2, httpx 0.28.1, pydantic 2.14.1; dev-only: xmlschema 4.3.2, signxml 5.1.0, Starlette 1.7.0, python-multipart 0.0.32, granian 2.8.4, testcontainers 4.15.0 (xmlsec1 1.2.41 on Debian trixie), hypothesis, pytest (+asyncio, cov, randomly).

**Spec:** `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` (§1.5, §2 rules 6/7/12, §4.1, §4.3-§4.6, §8.2-§8.8, §14, §16.3 row v2-M1b, §16.4), ADRs `docs/adr/0005`, `0006`, `0007`, `0009`, `0010`; facts `.superpowers/research/06-dgii-facts.md` (cited **[06]**) and `01-localization.md`. DGII's documents are cited by [06]'s short names: **DT-DGII** (*Descripción Técnica Servicios DGII*, 02-01-2026), **DT-EMI** (*Descripción Técnica Emisores Electrónicos*), **IT** (*Informe Técnico e-CF v1.0*, 23-03-2026), **FMT** (*Formato e-CF V1.0*, October 2025), plus *Firmado de e-CF*, *Formato Resumen Factura Consumo Electrónica v1.0* and *Formato Acuse de Recibo v1.0*.

## Global Constraints

- Python `>=3.15`; every `pyproject.toml` says `requires-python = ">=3.15"`; build backend `uv_build>=0.13.0,<0.14.0`.
- The gate after every task: `uv run poe check` — ruff format + ruff (`select = ["ALL"]`), `suppressions` (zero `# noqa`, `# type: ignore`, `# pyright:`, tool directives, coverage pragmas), basedpyright (`all`, warnings fail) and mypy `--strict` per package, import-linter, deptry per package, typos, `buf lint`, pytest with **100 % branch coverage** and warnings as errors.
- **Do not touch what the restructure moves or rewrites**: nothing under `libs/`, `services/`, `deploy/`, `tests/`, `docs/superpowers/specs/`, `docs/adr/`, the v1 plans, the root `conftest.py` or `.github/`. The root `pyproject.toml` is edited **in Task 1 only** (see "Coordination with the restructure").
- `ledger_country_do` and `ledger_country_do_testing` import nothing of the workspace (no `ledger_kernel`, `ledger_platform`, `ledger_contracts`, `ledger_identity`, `ledger_testing`): a contract added in Task 1 enforces it.
- Code, comments and messages in English; DGII's own words (element names, statuses, messages, Spanish labels) stay DGII's and live in DGII's files, in string constants, or in regime labels.
- DGII's element names are never spelled in builder code: builders name elements in lowercase and `adapters.xml.names.element(format, name)` returns the name DGII's schema declares.
- lxml objects are never named in an annotation (`etree._Element` is private under basedpyright `all`): they stay inferred locals. XML is *written* with the standard library's `ElementTree` (public, typed) and *read* only through `adapters.xml.parsing`'s hardened parser (size limit, DOCTYPE refused, no entities, no DTD, no network).
- Type-only imports use `lazy from ... import ...` (PEP 810), as the rest of the repository does.
- An error builds its message inside its class and never quotes a document, an amount, a token, a password or a tax id.
- **Security.** Secrets are read from files under the git-ignored `.dgii/` directory whose paths come from environment variables; they are never stored in the repository and never printed (no `.env` value, token, password, key or certificate body in any output, log or exception). The spike never talks to the live `ecf` environment: the client refuses non-DGII hosts for `ecf`, and the CLI refuses `ecf` altogether.
- Processes: never kill processes by image name; stop only processes this work started, by PID. Port 8000 is never used: the fake DGII listens on `127.0.0.1:8471` by default.
- Docker must be running (xmlsec1 runs in a container, as the existing tests' PostgreSQL does).
- Work in a git worktree on branch `v2-dgii-spike` created from `main` (the restructure runs in parallel); one commit per task; merge into the local `main` at the end; never push, never rewrite history. Commit messages are conventional, then a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Dependency floors (each installed on CPython 3.15 during the v1 M3/M4 plan work, 2026-10-12): `lxml>=6.1.3`, `cryptography>=50.0.2`, `httpx>=0.28.1`, `pydantic>=2.14.1`, `xmlschema>=4.3.2`, `signxml>=5.1.0`, `starlette>=1.7.0`, `python-multipart>=0.0.32`, `granian>=2.8.4`, `testcontainers>=4.15.0`, `types-lxml>=2026.2.16`. `uv.lock` pins exact versions. A floor that fails to install on 3.15 stops the task: report it, never lower the bar.
- The code in this plan follows the v1 M3/M4 plans' code, which was run on CPython 3.15 (lxml, cryptography, signxml, xmlschema, Starlette, httpx); this plan as a whole was **not** run. Where a library's exact signature differs from what a step shows, the executor uses the library's and says so in the task report; a name in an **Interfaces** block never changes silently.

## Coordination with the restructure (v2-M1a)

1. **Root `pyproject.toml`.** It keeps hand-written package lists. Task 1 adds the two distributions to each list, once, in one commit: `[project] dependencies`, the `dev` dependency group, `[tool.uv.workspace] members`, `[tool.uv.sources]`, the poe `mypy-*`/`deps-*` tasks and their sequences, ruff `known-first-party` and the tests' `per-file-ignores` key, basedpyright `include`, pytest `testpaths`, coverage `source_pkgs`, import-linter `root_packages` and contracts. No later task edits the root. The restructure's `ledger-devtools sync` replaces these lists with generated ones; its member globs `countries/*` and `countries/*/testing` (ADR 0009) cover both distributions, so the spike needs nothing more from it. Whichever of the two branches merges second resolves `pyproject.toml` by keeping the restructure's form plus the spike's entries the generator does not produce yet.
2. **Nested member, a verified finding (uv 0.13.0, 2026-10-13, scratch workspace):** a workspace member nested inside another member (`countries/do/testing` inside `countries/do`) **fails to build when it declares `{ workspace = true }` sources of its own** ("references a workspace in `tool.uv.sources` ... but is not a workspace member"); without them it locks, builds, installs and imports, and the root's `[tool.uv.sources]` covers it. `ledger-devtools sync` must therefore never write `[tool.uv.sources]` into a nested member's generated `pyproject.toml`. (`uv build` of `countries/do` ships only `ledger_country_do/`: the nested `testing/` never enters its wheel.)
3. **`libs/kernel/src/ledger_kernel/countries/do.py`.** Spec §16.1 moves it to `countries/do/src/ledger_country_do/regime/tax_ids.py`. This spike creates that file (Task 1) from the regime's needs (a scheme check returning `valid | checksum_mismatch | invalid_format`, ADR 0006) without touching the kernel. The restructure should **delete** the kernel module (moving its callers to kernel v2's `TaxId(scheme, value)` and the regime), not move it onto the spike's file.
4. **Kernel v2 regime types.** `ledger_country_do.regime.definitions` holds provisional copies of spec §5.1/§8.2's types under the spec's names (`Source`, `Since`, `Timeline`, `TaxIdCheck`, `TaxIdSchemeDef`, `TaxCategoryDef`, `DocumentTypeDef`, `RoundingRule`, `RegimeDef`), so integration swaps imports. Differences the kernel's author should know: the spike's `Timeline` carries a `name` (for its error messages); `RegimeDef.country` and `currency` are `str` (no `Jurisdiction`/`Currency` yet); `GenericCategory` names the five UBL-like categories; `TaxIdSchemeDef.check` is a callable.
5. **Errors.** `ledger_country_do.channels.ecf_v1.domain.errors.EcfError` mirrors `ledger_kernel.errors` (`kind`, `code`); integration makes the spike's error bases subclasses of the kernel's and drops the local `truncate`.
6. **Generated files.** Both distributions' `pyproject.toml` are hand-written now (a header says so). The spike writes no `manifest.toml` (nothing validates one yet): the restructure's devtools work writes it from spec §4.3 and generates both files.
7. **Deviation from spec §16.3 (row v2-M1b), by the controller's instruction:** the spec allows throwaway spike code outside the gate in `.superpowers/spikes/`; this plan builds kept code under the full gate in `countries/do`, so nothing is thrown away.
8. **Signing.** Spec §3.7 keeps private keys in the signer process only. With no signer module yet, the spike keeps DGII's signing *profile* (`adapters/signing/signer.py`, a pure function of a key object) in the channel; nothing in `ledger-country-do` loads a key from a file (the PKCS#12 loader is in the dev-only distribution). v2-M2 moves the signing half to `modules/signer` (profile `do-ecf-v1`); the verifying half stays in the channel (it needs no key).

## Review Focus

1. **Hostile or odd XML from outside** (DGII's seed, a buyer's ARECF, any document the fake receives): a DOCTYPE, an entity, an external DTD, a document over 4 MiB, a malformed one, a second `Signature`, a signature that is not last, algorithms outside DGII's profile — each is refused before or while it is read, and no error quotes the document. Tests in Tasks 3, 6 and 13.
2. **A submission whose connection breaks after the request may have left** is never resent: the client reports the outcome unknown; the use case waits a settle delay, asks DGII for the e-NCF's track ids and adopts only one DGII received from the moment of signing on (less a small tolerance); anything else parks the document as outcome unknown. The spike never sends a document twice. Tests in Tasks 11 and 12.
3. **DGII's codes as DGII defines them**: Aceptado Condicional (4) is valid (messages kept as warnings, nothing re-issued); a rejection with `secuenciaUtilizada = false` releases the number, `true` or absent burns it; En Proceso (3) and No encontrado (0) are not final; a code DGII never documented is an error, never a guess. Tests in Tasks 8 and 12.
4. **The RD$250,000 line and the RFCE**: a type 32 whose total is below the threshold goes as an RFCE to the `fc` host, synchronously and without a track id, carrying the security code of the full signed e-CF; exactly RD$250,000.00 or more goes in full to `recepcion`. Tests in Tasks 4 and 12.
5. **Secrets and the live environment**: the CLI never prints a token, password, key or certificate, refuses the `ecf` environment, refuses an e-NCF it already used, and reads secrets only from files under the git-ignored `.dgii/`; the client refuses a non-DGII host for `ecf`. Tests in Tasks 11 and 14.

## Decisions

1. **No workspace dependency** (Coordination 4-5): the restructure can move `libs/` and `services/` at any time. Cost: a small error base and provisional regime types, swapped at integration.
2. **XSDs vendored byte for byte, corrected on load** (v1 M4 Decision 3; re-checked on 2026-10-13 against the files DGII served on 2026-10-12): the fifteen XSDs keep DGII's bytes (BOMs, CRLF), each pinned by URL and SHA-256; `.gitattributes` marks them `-text`, which matters because the repository normalizes `eol=lf`. Three do not compile as published: e-CF 31 names a type with a leading space (`name=" IndicadorServicioTodoIncluidoType"`), ACECF (once) and RFCE (four times) use Perl's `(?:`; each correction is pinned to its exact count. Validation runs on libxml2; xmlschema is the second implementation (tests and the fake).
3. **The signature is DGII's profile exactly** (*Firmado de e-CF*): an enveloped `Signature` in the default XMLDSig namespace as the root's last child, `Reference URI=""` with the single `enveloped-signature` transform, inclusive C14N 1.0 without comments, a SHA-256 digest, RSA-SHA256 (RSASSA-PKCS1-v1_5), `KeyInfo/X509Data/X509Certificate`. Not XAdES: DGII's document and samples carry no XAdES properties. `SignedInfo` is canonicalized as a standalone element declaring exactly what is in scope (lxml's in-document C14N writes a spurious `xmlns=""` that xmlsec1 rejects: v1 M3 Decision 13). The verifier also accepts the `[enveloped, c14n]` transform list that other signers (signxml, .NET with explicit transforms) write.
4. **The security code** is the first six characters of the `SignatureValue` as written (base64), whitespace removed (IT §18.2.3: "los primeros seis (6) dígitos del hash generado en el SignatureValue"; DT-DGII's example `dcp79q`).
5. **The QR URLs** follow IT §18.2.3: `{ecf host}/{env}/consultatimbre?RncEmisor&RncComprador&ENCF&FechaEmision&MontoTotal&FechaFirma&CodigoSeguridad` (`RncComprador` left out without a buyer RNC) and, for an RFCE, `{fc host}/{env}/consultatimbrefc?RncEmisor&ENCF&MontoTotal&CodigoSeguridad`; values are percent-encoded except `-`, `:` and `.` (IT's example keeps the colons of `HH:mm:ss` and encodes the space as `%20`).
6. **Routing.** A type 32 goes as an RFCE iff its `MontoTotal` is strictly below the dated `rfce_threshold` (DOP 250,000.00); the full e-CF is still built, signed and kept (IT §9.1), and the RFCE carries its security code.
7. **Polling.** The spec's schedule (1 s, 2 s, 5 s, 10 s, 30 s, 60 s) with an injected sleep; after it the outcome is "still pending" (v2-M3's job continues every 5 min).
8. **Retries.** Only calls that cannot change DGII's state (seed, seed validation, result, track ids, directory) are retried, twice, 0.2 s and 0.5 s apart; a document sent is never retried by the client. A 401 drops the token and the call is made once more with a new one. Tokens are cached per environment until five minutes before they expire, and never stored, logged or printed.
9. **The fake DGII** is written from DT-DGII independently of the client and checks with tools that share no code with it: xmlschema for XSDs, signxml for signatures (verifying ours) and for signing ARECFs (which our verifier checks). It is not DGII: no RNC registry, no range authorization, no tolerance arithmetic (scripted instead).
10. **Buyer delivery** in the spike is an adapter (directory lookup, the buyer's optional seed authentication, `POST` of the signed e-CF, a signed ARECF verified and matched); the `delivering` state machine is v2-M4's.

## DGII facts this plan could not pin down (and what it assumes)

`countries/do/docs/spike-findings.md` (Task 14) repeats this table; the runbook settles each row on `TesteCF`.

| # | Question | The plan assumes | Settled by |
|---|---|---|---|
| F1 | Is the bearer token from `ecf.dgii.gov.do/{env}/autenticacion` accepted by `fc.dgii.gov.do` (RFCE)? | Yes: DT-DGII's curl examples for both hosts carry the same JWT | Runbook step 5 |
| F2 | The RFCE reception's `codigo` values | 1 Aceptado, 2 Rechazado, 4 Aceptado Condicional, as `consultaresultado`; anything else is unreadable | Runbook step 5 |
| F3 | DGII's answer to a type 32 below RD$250,000 sent to `recepcion` | A refusal (the fake answers 400 with `error`/`mensaje`); our routing never sends one | Not exercised on purpose |
| F4 | An anonymous type 32: its XSD requires `Comprador` with every child optional, while DT-EMI forbids empty tags | The builder writes an empty `<Comprador />` only for an anonymous buyer | Runbook step 6 |
| F5 | Directory `urlRecepcion`/`urlAceptacion`/`urlOpcional`: a base URL or the full endpoint? | A base: the client appends `/fe/recepcion/api/ecf` and `/fe/autenticacion/api/{semilla,validacioncertificado}` (consistent with TesteCF's directory returning `…/testecf/emisorreceptor`) | Runbook step 7 |
| F6 | Is `consultadirectorio` authenticated? | DT-DGII's curl has no `Authorization`; the client sends the bearer anyway (harmless if ignored) | Runbook step 7 |
| F7 | The line limit of a type 32 | 1,000 (the e-CF 32 XSD: `Item [1..1000]`); [06]'s 10,000 for RFCE-summarized invoices is kept as a regime parameter, unused | Out of scope |
| F8 | `RncComprador` in the QR when there is no buyer RNC | Left out | Runbook step 6 |
| F9 | Does TesteCF check the signer's chain to a DR-authorized entity (so a self-made certificate is refused)? | Yes: only the owner's real certificate works on TesteCF; generated certificates are for tests and the fake | Runbook step 4 |
| F10 | When the ITBIS rates took effect (18 % by Ley 253-12; 16 % phased 2013-2016) | Not asserted: the regime's timelines start at 2019-01-01 (do-ecf-v1's `valid_from`), where FMT's 18/16/0 hold | Regime review (not DGII) |
| F11 | DGII's conditional-acceptance codes and messages | Only the cuadratura tolerance is documented (IT §12); messages are kept verbatim | Runbook step 5 (tolerance sample) |
| F12 | Whether `MontoTotal` (ITBIS included) is the amount compared with RD$250,000 | Yes | Runbook step 5 |

---

## File map

```
ledger/
├── pyproject.toml                        Task 1 only (Coordination 1)
├── .gitattributes  _typos.toml  .gitignore                                          Task 1
├── countries/do/                                                      ledger-country-do
│   ├── pyproject.toml  README.md                                      Task 1 (dependencies grow in Tasks 3, 6, 11)
│   ├── docs/runbooks/testecf-spike.md  docs/spike-findings.md         Task 14
│   ├── src/ledger_country_do/
│   │   ├── __init__.py  py.typed
│   │   ├── regime/{__init__, definitions, sources, tax_ids, taxes, document_types, parameters, rounding}.py
│   │   └── channels/__init__.py
│   │       └── ecf_v1/__init__.py
│   │           ├── domain/{__init__, errors, environments, encf, notation, drafts, totals, routing, printed,
│   │           │           status_map}.py
│   │           ├── application/{__init__, ports, emission}.py
│   │           └── adapters/
│   │               ├── __init__.py
│   │               ├── xsd/{__init__, published}.py + 15 *.xsd (DGII's bytes)
│   │               ├── xml/{__init__, parsing, schemas, names, writer, ecf, rfce, documents}.py
│   │               ├── signing/{__init__, profile, signer, verifier, stamp}.py
│   │               ├── dgii/{__init__, endpoints, wire, client}.py
│   │               └── buyers/{__init__, client}.py
│   └── tests/
│       ├── conftest.py
│       ├── unit/test_{tax_ids, regime, xml_parsing, xml_names, encf, drafts, totals, routing, ecf_xml,
│       │              signing, printed, status_map, dgii_endpoints, emission_flow}.py
│       ├── contract/test_{xsd_pins, golden}.py, golden/{ecf-31, ecf-32, ecf-32-anonymous, rfce-32}.xml
│       └── integration/test_{signing_xmlsec1, dgii_client, emission, buyer_delivery}.py
└── countries/do/testing/                                              ledger-country-do-testing (dev-only)
    ├── pyproject.toml  README.md
    ├── src/ledger_country_do_testing/
    │   ├── __init__.py  py.typed
    │   ├── pki.py  xmlsec.py  xmlsec1/Dockerfile  signatures.py  local_signer.py  samples.py  clock.py
    │   ├── answers.py  transports.py  spike.py
    │   └── fake_dgii/{__init__, state, emitter, receiver, app, serve}.py
    └── tests/conftest.py, test_{pki, xmlsec, signatures, local_signer, samples, fake_dgii_emitter,
              fake_dgii_receiver, fake_dgii_serve, spike_cli}.py
```

Test file names are unique across both `tests/` trees (mypy maps a test file to its base name).

---

### Task 1: The two distributions in the workspace, the regime's tax-id schemes, a test PKI

**Files:**
- Modify: `pyproject.toml` (root: every hand-kept list, once), `.gitattributes`, `_typos.toml`, `.gitignore`
- Create: `countries/do/pyproject.toml`, `countries/do/README.md`, `countries/do/src/ledger_country_do/{__init__.py, py.typed}`, `countries/do/src/ledger_country_do/regime/{__init__, definitions, sources, tax_ids}.py`, `countries/do/src/ledger_country_do/channels/__init__.py`, `countries/do/src/ledger_country_do/channels/ecf_v1/{__init__.py, domain/__init__.py, application/__init__.py, adapters/__init__.py}`
- Create: `countries/do/testing/pyproject.toml`, `countries/do/testing/README.md`, `countries/do/testing/src/ledger_country_do_testing/{__init__.py, py.typed, pki.py}`
- Test: `countries/do/tests/unit/test_tax_ids.py`, `countries/do/testing/tests/test_pki.py`

**Interfaces:**
- Consumes: nothing of the workspace.
- Produces: `ledger_country_do.regime.definitions`: `TaxIdCheck` (`VALID`, `CHECKSUM_MISMATCH`, `INVALID_FORMAT`), `Source(title, url, version, retrieved_on)`, `TaxIdSchemeDef(key, label, digits, check_digit_enforced, check, source)`. `ledger_country_do.regime.sources`: `RETRIEVED`, `COVERED_FROM` (2019-01-01), `STDNUM_RNC`, `STDNUM_CEDULA`. `ledger_country_do.regime.tax_ids`: `MAX_TEXT`, `RNC_DIGITS`, `CEDULA_DIGITS`, `normalize(text) -> str | None`, `check_rnc(text) -> TaxIdCheck`, `check_cedula(text) -> TaxIdCheck`, `RNC`, `CEDULA`, `SCHEMES`, `scheme_of(text) -> TaxIdSchemeDef | None`. `ledger_country_do_testing.pki`: `OPENING_PHRASE`, `DEFAULT_BITS`, `SIGNING`, `type PrivateKey`, `rsa_key(bits=2048)`, `ec_key()`, `person(cedula="40212345678", *, name="JUAN PEREZ")`, `organization(rnc="101850043", *, name=...)`, `Issued(certificate, key)` with `pem()`, `Authority.root(name=..., *, now=None)`, `Authority.intermediate(name=...)`, `Authority.issue(subject, *, key=None, not_before=None, not_after=None) -> Issued`, `Authority.certificate`, `bundle(issued, *, chain=(), password=OPENING_PHRASE) -> bytes`, `pem(*certificates) -> bytes`.

Re-check before starting: `git log --oneline -5 main` and the root `pyproject.toml`. If the restructure has landed (members by glob, `ledger-devtools`), skip the root edits it already covers, add only what its generator does not produce yet, and say so in the report; never undo a restructure edit.

- [ ] **Step 1: The root `pyproject.toml`, once (every hand-kept list)**

Apply exactly these edits; every list keeps its existing entries and nothing else in the file changes:

```toml
# [project]
dependencies = ["ledger-kernel", "ledger-contracts", "ledger-platform", "ledger-identity", "ledger-country-do"]

# [dependency-groups] dev — add, in alphabetical order with the rest:
    "ledger-country-do-testing",
    "signxml>=5.1.0",
    "types-lxml>=2026.2.16",
    "xmlschema>=4.3.2",

# [tool.uv.workspace]
members = ["libs/*", "services/*", "countries/do", "countries/do/testing"]

# [tool.uv.sources] — add:
ledger-country-do = { workspace = true }
ledger-country-do-testing = { workspace = true }

# [tool.poe.tasks] — add; append the two mypy names to `mypy`'s sequence before "mypy-workspace" and the two
# deps names to the end of `deps`' sequence:
mypy-country-do = { cmd = "mypy countries/do/src countries/do/tests" }
mypy-country-do-testing = { cmd = "mypy countries/do/testing" }
deps-country-do = { cmd = "deptry countries/do/src --config countries/do/pyproject.toml" }
deps-country-do-testing = { cmd = "deptry countries/do/testing/src --config countries/do/testing/pyproject.toml" }

# [tool.ruff.lint.per-file-ignores] — the tests' key becomes (its list unchanged):
"{libs/*/tests,services/*/tests,countries/do/tests,countries/do/testing/tests,tests}/**" = [...]

# [tool.ruff.lint.isort]
known-first-party = [..., "ledger_country_do", "ledger_country_do_testing"]

# [tool.basedpyright]
include = ["libs", "services", "countries", "tests", "conftest.py"]

# [tool.pytest]
testpaths = ["libs", "services", "countries", "tests"]

# [tool.coverage.run]
source_pkgs = [..., "ledger_country_do", "ledger_country_do_testing"]

# [tool.importlinter]
root_packages = [..., "ledger_country_do", "ledger_country_do_testing"]
```

The existing contract "Production code never imports test tooling" gains `"ledger_country_do"` in `source_modules`, and `"ledger_country_do_testing"`, `"signxml"`, `"xmlschema"`, `"starlette"` in `forbidden_modules`. Append these contracts after the existing ones:

```toml
# ── The Dominican pack (the v2-M1b spike) ───────────────────────────────────────
# It stands alone so the restructure can move libs/ and services/ at any time; the integration milestone takes
# the kernel off the first contract's list.
[[tool.importlinter.contracts]]
name = "The DO pack imports nothing of the workspace"
type = "forbidden"
source_modules = ["ledger_country_do", "ledger_country_do_testing"]
forbidden_modules = ["ledger_kernel", "ledger_contracts", "ledger_platform", "ledger_identity", "ledger_testing"]

[[tool.importlinter.contracts]]
name = "The DO regime is pure data: the standard library only"
type = "forbidden"
source_modules = ["ledger_country_do.regime"]
forbidden_modules = ["ledger_country_do.channels", "lxml", "httpx", "pydantic", "cryptography"]

[[tool.importlinter.contracts]]
name = "Channel do-ecf-v1 is layered: adapters -> application -> domain"
type = "layers"
containers = ["ledger_country_do.channels.ecf_v1"]
exhaustive = true
layers = ["adapters", "application", "domain"]

[[tool.importlinter.contracts]]
name = "do-ecf-v1's domain and use cases know no third party"
type = "forbidden"
source_modules = ["ledger_country_do.channels.ecf_v1.domain", "ledger_country_do.channels.ecf_v1.application"]
forbidden_modules = ["lxml", "httpx", "pydantic", "cryptography"]
```

`.gitattributes` — append (DGII's XSDs keep their BOMs and CRLF; their pinned SHA-256 is of the bytes as published, and `* text=auto eol=lf` would otherwise rewrite them):

```
countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd/*.xsd -text
```

`_typos.toml` — `[files] extend-exclude` gains DGII's files and the documents written from them:

```toml
[files]
extend-exclude = [
    "uv.lock",
    "libs/contracts/src/ledger/",
    "countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd/",
    "countries/do/tests/contract/golden/",
]
```

(Later tasks add under `[default.extend-words]` only the Spanish words of DGII's that `typos` actually reports, each mapped to itself as `condicional` already is.)

`.gitignore` — append (the runbook's secrets and outputs; a certificate never enters the repository by any path):

```
# DGII spike: the owner's certificate and password file, spike outputs (countries/do/docs/runbooks/testecf-spike.md)
/.dgii/
*.p12
*.pfx
```

- [ ] **Step 2: The two distributions**

`countries/do/pyproject.toml`:

```toml
# Hand-written for the DGII spike (v2-M1b). The restructure's `ledger-devtools sync` generates this file from
# countries/do/manifest.toml (spec §4.3); until then this file is the source of truth.
[project]
name = "ledger-country-do"
version = "0.1.0"
description = "Dominican Republic: regime 'do' (DGII's tax law as data) and channel 'do-ecf-v1' (e-CF)."
requires-python = ">=3.15"
dependencies = []

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`countries/do/testing/pyproject.toml`:

```toml
# Hand-written for the DGII spike (v2-M1b); generated by `ledger-devtools sync` after the restructure.
# Dev-only: no production image installs it (spec §4.1).
# No [tool.uv.sources] here: a member nested inside another member that declares `{ workspace = true }` sources
# fails to build (uv 0.13.0, verified 2026-10-13); the root's [tool.uv.sources] covers every member.
[project]
name = "ledger-country-do-testing"
version = "0.1.0"
description = "Dev-only tools of the DO pack: a fake DGII from DGII's documentation, a test PKI, samples, the spike CLI."
requires-python = ">=3.15"
dependencies = ["cryptography>=50.0.2"]

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
```

`countries/do/src/ledger_country_do/__init__.py`:

```python
"""The Dominican Republic: regime ``do`` (tax law as data) and channel ``do-ecf-v1`` (DGII's e-CF)."""
```

`countries/do/src/ledger_country_do/regime/__init__.py` (Task 2 adds `REGIME`):

```python
"""Regime ``do``: the Dominican Republic's tax law as typed, effective-dated data (spec §8.2, §8.6.2).

Standard library only (import-linter: "The DO regime is pure data").
"""
```

`countries/do/src/ledger_country_do/channels/__init__.py`:

```python
"""The Dominican Republic's authority channels: one package per format major version."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/__init__.py`:

```python
"""Channel ``do-ecf-v1``: DGII's electronic fiscal vouchers (e-CF), format v1.0 (spec §8.6).

Layered as every v2 package (spec §4.5): ``domain`` (the standard library and the regime) <- ``application``
(ports and use cases) <- ``adapters`` (DGII's XSDs and XML, the signature profile, DGII's and buyers' web
services). The runtime integration (``CHANNEL``, ``assembly``, jobs, PostgreSQL) is v2-M3's.
"""
```

`.../ecf_v1/domain/__init__.py`:

```python
"""do-ecf-v1's domain: e-NCF, drafts, totals, routing, DGII's status codes, printed data. Standard library only."""
```

`.../ecf_v1/application/__init__.py`:

```python
"""do-ecf-v1's use cases and the ports they need: a signer, DGII, DGII's XML formats."""
```

`.../ecf_v1/adapters/__init__.py`:

```python
"""How do-ecf-v1 reaches the world: DGII's schemas and XML, the signature profile, DGII's and buyers' services."""
```

`countries/do/testing/src/ledger_country_do_testing/__init__.py`:

```python
"""Dev-only tools of the DO pack: a fake DGII, a test PKI, xmlsec1, samples and the spike CLI."""
```

Empty `py.typed` files in both `src/` packages.

`countries/do/README.md`:

```markdown
# ledger-country-do

The Dominican Republic as a country pack (spec §8.6): regime `do` (DGII's tax law as typed, dated data) and
channel `do-ecf-v1` (DGII's e-CF: XML, schemas, signature, DGII's web services, buyers' receivers).

- `src/ledger_country_do/regime/`: tax-id schemes (`do.rnc`, `do.cedula`), ITBIS rates, the ten e-CF types,
  dated parameters, rounding and tolerance. Standard library only.
- `src/ledger_country_do/channels/ecf_v1/`: `domain` <- `application` <- `adapters`.
- `docs/runbooks/testecf-spike.md`: running the spike against DGII's TesteCF; `docs/spike-findings.md`: what it
  settled.

Built by the DGII spike (`docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`); no app assembles it yet.
```

`countries/do/testing/README.md`:

```markdown
# ledger-country-do-testing (dev-only)

Never installed in an image. A fake DGII (`ledger-fake-dgii`, written from DGII's *Descripción Técnica*), a test
PKI, xmlsec1 as the reference signature verifier, samples, and the spike CLI (`ledger-dgii-spike`).
```

- [ ] **Step 3: Write the failing tests**

`countries/do/tests/unit/test_tax_ids.py`:

```python
import string
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.regime.definitions import TaxIdCheck
from ledger_country_do.regime.tax_ids import (
    CEDULA,
    MAX_TEXT,
    RNC,
    SCHEMES,
    check_cedula,
    check_rnc,
    normalize,
    scheme_of,
)


@pytest.mark.parametrize("text", ["101850043", "1-01-85004-3", "131098193", " 131098193 ", "401506254"])
def test_a_well_formed_rnc_with_its_check_digit_is_valid(text: str) -> None:
    assert check_rnc(text) is TaxIdCheck.VALID


@pytest.mark.parametrize("text", ["00113918205", "001-1391820-5", "402 1234567 8"])
def test_a_well_formed_cedula_with_its_check_digit_is_valid(text: str) -> None:
    assert check_cedula(text) is TaxIdCheck.VALID


@pytest.mark.parametrize(
    ("check", "text"),
    [(check_rnc, "101850042"), (check_cedula, "00113918204"), (check_cedula, "00112345678")],
)
def test_a_wrong_check_digit_is_reported_not_a_format_error(check: Callable[[str], TaxIdCheck], text: str) -> None:
    assert check(text) is TaxIdCheck.CHECKSUM_MISMATCH


@pytest.mark.parametrize(
    "text",
    ["", "12345678", "1234567890", "10185004A", "101.850.043", "١٠١٨٥٠٠٤٣", "1" * (MAX_TEXT + 1), "00113918205"],
    ids=["empty", "eight", "ten", "letter", "dots", "arabic-indic", "too-long", "a-cedula"],
)
def test_anything_else_is_not_an_rnc(text: str) -> None:
    assert check_rnc(text) is TaxIdCheck.INVALID_FORMAT


def test_an_rnc_is_not_a_cedula() -> None:
    assert check_cedula("101850043") is TaxIdCheck.INVALID_FORMAT


@given(st.text(alphabet=string.digits, min_size=8, max_size=8))
def test_exactly_one_ninth_digit_completes_any_rnc(first: str) -> None:
    assert sum(check_rnc(first + digit) is TaxIdCheck.VALID for digit in string.digits) == 1


@given(st.text(alphabet=string.digits, min_size=10, max_size=10))
def test_exactly_one_eleventh_digit_completes_any_cedula(first: str) -> None:
    assert sum(check_cedula(first + digit) is TaxIdCheck.VALID for digit in string.digits) == 1


def test_separators_are_ignored_and_nothing_else() -> None:
    assert normalize("1-01 85004-3") == "101850043"
    assert normalize("1.01") is None
    assert normalize("") is None
    assert normalize("1" * (MAX_TEXT + 1)) is None


def test_the_digit_count_names_the_scheme() -> None:
    assert scheme_of("101850043") is RNC
    assert scheme_of("001-1391820-5") is CEDULA
    assert scheme_of("12") is None
    assert scheme_of("abc") is None


def test_the_regime_reports_check_digits_without_enforcing_them() -> None:
    assert [scheme.key for scheme in SCHEMES] == ["do.rnc", "do.cedula"]
    assert not any(scheme.check_digit_enforced for scheme in SCHEMES)
    assert all(scheme.source.url.startswith("https://") for scheme in SCHEMES)
```

`countries/do/testing/tests/test_pki.py`:

```python
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ledger_country_do_testing.pki import OPENING_PHRASE, Authority, bundle, ec_key, organization, pem, person


def test_subjects_follow_the_dominican_conventions() -> None:
    serial = person("40212345678").get_attributes_for_oid(NameOID.SERIAL_NUMBER)[0].value
    vat = organization("130000001").get_attributes_for_oid(NameOID.ORGANIZATION_IDENTIFIER)[0].value
    assert (serial, vat) == ("IDCDO-40212345678", "VATDO-130000001")


def test_a_hierarchy_issues_certificates_that_bundle_and_open_again() -> None:
    root = Authority.root()
    intermediate = root.intermediate()
    issued = intermediate.issue(person())
    loaded = pkcs12.load_pkcs12(bundle(issued, chain=[intermediate.certificate]), OPENING_PHRASE.encode())
    assert loaded.cert is not None
    assert loaded.cert.certificate == issued.certificate
    assert [extra.certificate for extra in loaded.additional_certs] == [intermediate.certificate]
    assert issued.certificate.issuer == intermediate.certificate.subject
    assert pem(root.certificate).startswith(b"-----BEGIN CERTIFICATE-----")
    assert issued.pem() == pem(issued.certificate)
    assert pkcs12.load_pkcs12(bundle(issued), OPENING_PHRASE.encode()).additional_certs == []


def test_a_test_breaks_what_it_needs() -> None:
    root = Authority.root(now=datetime.now(UTC))
    past = datetime.now(UTC) - timedelta(days=10)
    issued = root.issue(organization(), key=ec_key(), not_before=past - timedelta(days=1), not_after=past)
    assert isinstance(issued.key, EllipticCurvePrivateKey)
    assert issued.certificate.not_valid_after_utc == past.replace(microsecond=0)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv lock && uv sync && uv run pytest countries -q --no-cov -p no:randomly`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.regime.definitions'` (and `ledger_country_do_testing.pki`).

- [ ] **Step 5: Implement**

`countries/do/src/ledger_country_do/regime/definitions.py` (Task 2 adds the dated and document types):

```python
"""The schema of a regime: provisional, local copies of the kernel v2 regime types (spec §5.1, §8.2).

The kernel v2, which will hold these types (``ledger_kernel.regimes``), is built by the restructure in parallel
with this spike; the spike keeps copies under the spec's names and fields and imports nothing of the workspace.
The integration milestone replaces this module with imports from the kernel.

Standard library only.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from collections.abc import Callable
lazy from datetime import date


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a text (ADR 0006)."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but not the check digit the algorithm predicts: reported; refused only where enforced."""
    INVALID_FORMAT = "invalid_format"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a value of a regime comes from."""

    title: str
    url: str
    version: str
    retrieved_on: date


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax-identifier scheme (``<cc>.<name>``): its length, its check, whether its check digit is enforced."""

    key: str
    label: str
    digits: int
    check_digit_enforced: bool
    check: Callable[[str], TaxIdCheck]
    source: Source
```

`countries/do/src/ledger_country_do/regime/sources.py` (Task 2 adds DGII's documents and `ALL`):

```python
"""Where regime ``do``'s values come from: one ``Source`` per document, cited by the values that use it."""

from datetime import date
from typing import Final

from ledger_country_do.regime.definitions import Source

RETRIEVED: Final = date(2026, 10, 13)
COVERED_FROM: Final = date(2019, 1, 1)
"""The first day the pack asserts values for: channel do-ecf-v1's ``valid_from`` (spec §4.3)."""

STDNUM_RNC: Final = Source(
    title="python-stdnum, stdnum.do.rnc: the RNC's check digit (weights 7 9 8 6 5 4 3 2, modulo 11)",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/rnc.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
STDNUM_CEDULA: Final = Source(
    title="python-stdnum, stdnum.do.cedula: the cédula's check digit (Luhn) and DGII's numbers that fail it",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/cedula.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
```

`countries/do/src/ledger_country_do/regime/tax_ids.py`:

```python
"""Dominican taxpayer identifiers: ``do.rnc`` (9 digits) and ``do.cedula`` (11 digits), check digits reported.

DGII accepts a cédula wherever an RNC is asked for, and has issued numbers whose check digit does not match
(python-stdnum keeps hundreds of such cédulas), so the check digit is reported, never enforced
(``check_digit_enforced = False``, ADR 0006): whether a number exists is for DGII's registry to say. Hyphens
and spaces are ignored; anything else that is not an ASCII digit is a format error. A check never repeats the
text it read.
"""

from typing import Final

from ledger_country_do.regime.definitions import TaxIdCheck, TaxIdSchemeDef
from ledger_country_do.regime.sources import STDNUM_CEDULA, STDNUM_RNC

MAX_TEXT: Final = 20
"""The longest text read: eleven digits and their separators, with room to spare."""
RNC_DIGITS: Final = 9
CEDULA_DIGITS: Final = 11
_RNC_WEIGHTS: Final = (7, 9, 8, 6, 5, 4, 3, 2)
_SEPARATORS: Final = str.maketrans("", "", "- ")
_LUHN_FOLD: Final = 9


def normalize(text: str) -> str | None:
    """``text``'s digits without hyphens and spaces; ``None`` unless that leaves ASCII digits only."""
    if len(text) > MAX_TEXT:
        return None
    digits = text.translate(_SEPARATORS)
    return digits if digits.isascii() and digits.isdigit() else None


def _rnc_matches(digits: str) -> bool:
    remainder = sum(weight * int(digit) for weight, digit in zip(_RNC_WEIGHTS, digits[:8], strict=True)) % 11
    return (10 - remainder) % 9 + 1 == int(digits[8])


def _luhn_matches(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character) * (2 if position % 2 else 1)
        total += digit - _LUHN_FOLD if digit > _LUHN_FOLD else digit
    return total % 10 == 0


def check_rnc(text: str) -> TaxIdCheck:
    """An RNC's check: nine digits, the last the one DGII's weights predict."""
    digits = normalize(text)
    if digits is None or len(digits) != RNC_DIGITS:
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if _rnc_matches(digits) else TaxIdCheck.CHECKSUM_MISMATCH


def check_cedula(text: str) -> TaxIdCheck:
    """A cédula's check: eleven digits, the last the one Luhn's algorithm predicts."""
    digits = normalize(text)
    if digits is None or len(digits) != CEDULA_DIGITS:
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if _luhn_matches(digits) else TaxIdCheck.CHECKSUM_MISMATCH


RNC: Final = TaxIdSchemeDef(
    key="do.rnc", label="RNC", digits=RNC_DIGITS, check_digit_enforced=False, check=check_rnc, source=STDNUM_RNC
)
CEDULA: Final = TaxIdSchemeDef(
    key="do.cedula",
    label="Cédula",
    digits=CEDULA_DIGITS,
    check_digit_enforced=False,
    check=check_cedula,
    source=STDNUM_CEDULA,
)
SCHEMES: Final = (RNC, CEDULA)


def scheme_of(text: str) -> TaxIdSchemeDef | None:
    """The scheme ``text``'s digit count names (a cédula is taken wherever an RNC is); ``None`` if neither."""
    digits = normalize(text)
    if digits is None:
        return None
    return next((scheme for scheme in SCHEMES if scheme.digits == len(digits)), None)
```

`countries/do/testing/src/ledger_country_do_testing/pki.py`:

```python
"""Certificates for tests: authorities of the test's own, and the PKCS#12 bundles a company holds.

A test makes its own hierarchy (``Authority.root``, then ``intermediate``), so no test depends on a real
authority. Subjects follow the Dominican certification authorities' conventions: a natural person's
tax-procedure certificate names its cédula in ``serialNumber`` (``IDCDO-<cédula>``), an organization's seal its
RNC in ``organizationIdentifier`` (``VATDO-<rnc>``). DGII's TesteCF takes only certificates of authorized
entities: these serve tests and the fake DGII, never DGII.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, final
lazy from collections.abc import Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID

OPENING_PHRASE: Final = "it's the bundle's password"
"""What opens every bundle a test makes, unless it names another password."""
DEFAULT_BITS: Final = 2048
TAX_PROCEDURES: Final = "QUALIFIED CERTIFICATE FOR NATURAL PERSON - TAX PROCEDURES"
SIGNING: Final = x509.KeyUsage(
    digital_signature=True,
    content_commitment=True,
    key_encipherment=True,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)
_CERTIFYING: Final = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=False,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=True,
    crl_sign=True,
    encipher_only=False,
    decipher_only=False,
)
_AUTHORITY_YEARS: Final = timedelta(days=3650)
_LEAF_DAYS: Final = timedelta(days=365)
_BACKDATED: Final = timedelta(days=1)

type PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
"""RSA, which DGII's profile signs with; P-256, which it refuses."""


def rsa_key(bits: int = DEFAULT_BITS) -> rsa.RSAPrivateKey:
    """A new RSA key (tens of milliseconds at 2048 bits: tests that need many make them once)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def ec_key() -> ec.EllipticCurvePrivateKey:
    """A new P-256 key: one DGII's profile refuses."""
    return ec.generate_private_key(ec.SECP256R1())


def _name(*attributes: tuple[x509.ObjectIdentifier, str]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in attributes])


def person(cedula: str = "40212345678", *, name: str = "JUAN PEREZ") -> x509.Name:
    """A natural person's certificate for tax procedures: ``serialNumber`` ``IDCDO-<cedula>``."""
    given, _, family = name.partition(" ")
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.DN_QUALIFIER, TAX_PROCEDURES),
        (NameOID.COMMON_NAME, name),
        (NameOID.SERIAL_NUMBER, f"IDCDO-{cedula}"),
        (NameOID.GIVEN_NAME, given),
        (NameOID.SURNAME, family or given),
    )


def organization(rnc: str = "101850043", *, name: str = "FERRETERIA DUARTE SRL") -> x509.Name:
    """An organization's electronic seal: ``organizationIdentifier`` ``VATDO-<rnc>``."""
    return _name(
        (NameOID.COUNTRY_NAME, "DO"),
        (NameOID.ORGANIZATION_NAME, name),
        (NameOID.COMMON_NAME, name),
        (NameOID.ORGANIZATION_IDENTIFIER, f"VATDO-{rnc}"),
    )


@final
@dataclass(frozen=True, slots=True)
class Issued:
    """A certificate and its private key."""

    certificate: x509.Certificate
    key: PrivateKey

    def pem(self) -> bytes:
        """The certificate in PEM."""
        return self.certificate.public_bytes(Encoding.PEM)


def _authority_builder(
    subject: x509.Name, issuer: x509.Name, key: PrivateKey, issuer_key: PrivateKey, *, path_length: int
) -> x509.CertificateBuilder:
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
        .add_extension(_CERTIFYING, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        )
    )


@final
class Authority:
    """A certification authority of a test's own: a root, or an intermediate a root issued."""

    __slots__ = ("issued",)

    def __init__(self, issued: Issued) -> None:
        self.issued: Issued = issued

    @property
    def certificate(self) -> x509.Certificate:
        """The authority's own certificate."""
        return self.issued.certificate

    @classmethod
    def root(cls, name: str = "LEDGER TEST ROOT CA", *, now: datetime | None = None) -> Authority:
        """A self-signed root, valid from a day before ``now`` for ten years."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        moment = datetime.now(UTC) if now is None else now
        certificate = (
            _authority_builder(subject, subject, key, key, path_length=1)
            .not_valid_before(moment - _BACKDATED)
            .not_valid_after(moment + _AUTHORITY_YEARS)
            .sign(key, hashes.SHA256())
        )
        return cls(Issued(certificate, key))

    def intermediate(self, name: str = "LEDGER TEST QUALIFIED CERTIFICATES") -> Authority:
        """An authority this one issued, which issues end-entity certificates only."""
        key = rsa_key()
        subject = _name((NameOID.COUNTRY_NAME, "DO"), (NameOID.COMMON_NAME, name))
        certificate = (
            _authority_builder(subject, self.certificate.subject, key, self.issued.key, path_length=0)
            .not_valid_before(self.certificate.not_valid_before_utc)
            .not_valid_after(self.certificate.not_valid_after_utc)
            .sign(self.issued.key, hashes.SHA256())
        )
        return Authority(Issued(certificate, key))

    def issue(
        self,
        subject: x509.Name,
        *,
        key: PrivateKey | None = None,
        not_before: datetime | None = None,
        not_after: datetime | None = None,
    ) -> Issued:
        """An end-entity signing certificate for ``subject``, valid from a day ago for a year unless told else."""
        own = rsa_key() if key is None else key
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.certificate.subject)
            .public_key(own.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _BACKDATED if not_before is None else not_before)
            .not_valid_after(now + _LEAF_DAYS if not_after is None else not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(SIGNING, critical=True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.issued.key.public_key()),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(own.public_key()), critical=False)
            .sign(self.issued.key, hashes.SHA256())
        )
        return Issued(certificate, own)


def bundle(issued: Issued, *, chain: Sequence[x509.Certificate] = (), password: str = OPENING_PHRASE) -> bytes:
    """``issued`` as the PKCS#12 file a company holds, with ``chain``."""
    return pkcs12.serialize_key_and_certificates(
        b"ledger test",
        issued.key,
        issued.certificate,
        list(chain) or None,
        BestAvailableEncryption(password.encode()),
    )


def pem(*certificates: x509.Certificate) -> bytes:
    """``certificates`` one after the other in PEM: a trust store's file."""
    return b"".join(certificate.public_bytes(Encoding.PEM) for certificate in certificates)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest countries -q --no-cov`
Expected: PASS (17 tests; the two hypothesis properties run 100 examples each).

- [ ] **Step 7: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes; coverage 100 % (both new packages included); `lint-imports` reports the four new contracts kept. If `typos` reports a Spanish word of DGII's, add it to `[default.extend-words]` mapped to itself.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitattributes _typos.toml .gitignore countries
git commit -m "feat(do): the DO country pack and its dev-only testing distribution join the workspace

Root lists edited once for the spike; ledger-devtools replaces them in the restructure.
Regime tax-id schemes do.rnc and do.cedula (check digits reported); a test PKI.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Regime `do`: ITBIS rates, the ten e-CF types, dated parameters, rounding and tolerance

**Files:**
- Modify: `countries/do/src/ledger_country_do/regime/definitions.py` (whole file below), `regime/sources.py` (whole file below), `regime/__init__.py`
- Create: `countries/do/src/ledger_country_do/regime/{taxes, document_types, parameters, rounding}.py`
- Test: `countries/do/tests/unit/test_regime.py`

**Interfaces:**
- Consumes: Task 1's `definitions`, `sources`, `tax_ids`.
- Produces: `ledger_country_do.regime.definitions` adds `RegimeDataError(ValueError)`, `NotInForceError(LookupError)`, `UnknownEntryError(LookupError)`, `Since[T](since, value)`, `Timeline[T](name, entries)` with `at(day) -> T`, `GenericCategory`, `TaxCategoryDef(key, label, generic, rates, source)` with `rate_at(day) -> Decimal | None`, `DocumentKind`, `Direction`, `DocumentTypeDef(code, label, kind, direction, requires_buyer_tax_id, source)`, `RoundingRule(mode, places, unit_price_places, line_tolerance, global_tolerance_per_line, source)`, `type ParameterValue = Decimal | int`, `RegimeDef(...)` with `category(key)`, `document_type(code)`, `parameter(name, day)`. `ledger_country_do.regime.sources` adds `FORMAT_ECF`, `TECHNICAL_REPORT`, `SERVICES_DGII`, `DECREE_587_24`, `ALL`. `ledger_country_do.regime.taxes`: `STANDARD`, `REDUCED`, `ZERO`, `EXEMPT`, `CATEGORIES`. `ledger_country_do.regime.document_types`: `TYPES`. `ledger_country_do.regime.parameters`: `RFCE_THRESHOLD`, `CONTINGENCY_CONNECTIVITY_HOURS`, `CONTINGENCY_PAPER_MAX_DAYS`, `CONTINGENCY_REPLACEMENT_DAYS`, `LEGAL_NAME_MAX`, `LINES_MAX`, `RFCE_LINES_MAX` (the parameter names), `PARAMETERS`, `PARAMETER_SOURCES`. `ledger_country_do.regime.rounding`: `ROUNDING`, `CENT`, `round_amount(value) -> Decimal`, `within_line_tolerance(declared, *, quantity, unit_price) -> bool`, `within_global_tolerance(declared, *, computed, lines) -> bool`. `ledger_country_do.regime.REGIME: RegimeDef`.

- [ ] **Step 1: Write the failing test**

`countries/do/tests/unit/test_regime.py`:

```python
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.regime import REGIME
from ledger_country_do.regime.definitions import (
    Direction,
    DocumentKind,
    NotInForceError,
    RegimeDataError,
    Since,
    Timeline,
    UnknownEntryError,
)
from ledger_country_do.regime.parameters import PARAMETER_SOURCES, PARAMETERS
from ledger_country_do.regime.rounding import round_amount, within_global_tolerance, within_line_tolerance
from ledger_country_do.regime.sources import COVERED_FROM

TODAY = date(2026, 10, 13)


def test_the_regime_is_the_dominican_republic() -> None:
    assert (REGIME.key, REGIME.country, REGIME.currency, REGIME.time_zone) == (
        "do",
        "DO",
        "DOP",
        "America/Santo_Domingo",
    )


def test_itbis_rates_in_force_today() -> None:
    rates = {key: REGIME.category(key).rate_at(TODAY) for key in ("do.itbis.standard", "do.itbis.reduced")}
    assert rates == {"do.itbis.standard": Decimal(18), "do.itbis.reduced": Decimal(16)}
    assert REGIME.category("do.itbis.zero").rate_at(TODAY) == 0
    assert REGIME.category("do.itbis.exempt").rate_at(TODAY) is None


def test_no_rate_is_asserted_before_the_channel_s_era() -> None:
    with pytest.raises(NotInForceError, match="do.itbis.standard has no value in force on 2018-12-31"):
        REGIME.category("do.itbis.standard").rate_at(date(2018, 12, 31))


def test_the_ten_e_cf_types_with_their_kind_direction_and_buyer_rule() -> None:
    types = {kind.code: kind for kind in REGIME.document_types}
    assert list(types) == ["31", "32", "33", "34", "41", "43", "44", "45", "46", "47"]
    assert {code for code, kind in types.items() if kind.requires_buyer_tax_id} == {"31", "41", "45"}
    assert {code for code, kind in types.items() if kind.direction is Direction.PURCHASE} == {"41", "43", "47"}
    assert (types["33"].kind, types["34"].kind) == (DocumentKind.DEBIT_NOTE, DocumentKind.CREDIT_NOTE)
    assert REGIME.document_type("32").label == "Factura de Consumo Electrónica"


def test_dated_parameters() -> None:
    assert REGIME.parameter("rfce_threshold", TODAY) == Decimal("250000.00")
    assert REGIME.parameter("contingency_connectivity_hours", TODAY) == 72
    assert REGIME.parameter("contingency_paper_max_days", TODAY) == 15
    assert REGIME.parameter("contingency_replacement_days", TODAY) == 30
    assert REGIME.parameter("legal_name_max", TODAY) == 150
    assert REGIME.parameter("lines_max", TODAY) == 1000


def test_every_value_cites_a_source_the_regime_lists() -> None:
    assert set(PARAMETERS) == set(PARAMETER_SOURCES)
    assert set(PARAMETER_SOURCES.values()) <= set(REGIME.sources)
    assert all(source.url.startswith("https://") for source in REGIME.sources)


def test_unknown_entries_are_refused_by_name() -> None:
    with pytest.raises(UnknownEntryError, match="no tax category 'x'"):
        REGIME.category("x")
    with pytest.raises(UnknownEntryError, match="no document type '99'"):
        REGIME.document_type("99")
    with pytest.raises(UnknownEntryError, match="no parameter 'x'"):
        REGIME.parameter("x", TODAY)


def test_a_timeline_lists_its_values_newest_first() -> None:
    timeline = Timeline("t", (Since(date(2025, 1, 1), 2), Since(COVERED_FROM, 1)))
    assert (timeline.at(date(2024, 12, 31)), timeline.at(date(2025, 1, 1))) == (1, 2)
    with pytest.raises(RegimeDataError, match="at least one value"):
        Timeline[int]("t", ())
    for wrong in ((Since(COVERED_FROM, 1), Since(date(2025, 1, 1), 2)), (Since(COVERED_FROM, 1), Since(COVERED_FROM, 2))):
        with pytest.raises(RegimeDataError, match="newest first"):
            Timeline("t", wrong)


def test_a_regime_refuses_duplicates_and_uncited_sources() -> None:
    with pytest.raises(RegimeDataError, match="document type is declared twice"):
        replace(REGIME, document_types=REGIME.document_types * 2)
    with pytest.raises(RegimeDataError, match="cites a source"):
        replace(REGIME, sources=())


@pytest.mark.parametrize(
    ("value", "rounded"),
    [("750.5212", "750.52"), ("750.5276", "750.53"), ("750.525", "750.53"), ("0.005", "0.01"), ("2.675", "2.68")],
)
def test_amounts_round_half_up_to_two_places(value: str, rounded: str) -> None:
    assert round_amount(Decimal(value)) == Decimal(rounded)


@given(st.decimals(min_value=0, max_value=10**12, places=6, allow_nan=False, allow_infinity=False))
def test_rounding_moves_an_amount_by_half_a_cent_at_most_and_once(value: Decimal) -> None:
    rounded = round_amount(value)
    assert abs(rounded - value) <= Decimal("0.005")
    assert round_amount(rounded) == rounded


def test_the_technical_report_s_tolerance_examples() -> None:
    # IT §12: each line may differ from quantity x price by one unit; the total by as many units as lines.
    assert within_line_tolerance(Decimal("552.80"), quantity=Decimal("5.50"), unit_price=Decimal("100.33"))
    assert within_line_tolerance(Decimal("667.50"), quantity=Decimal(2), unit_price=Decimal("333.25"))
    assert not within_line_tolerance(Decimal("668.51"), quantity=Decimal(2), unit_price=Decimal("333.25"))
    assert within_global_tolerance(Decimal("6914.30"), computed=Decimal("6911.58"), lines=3)
    assert not within_global_tolerance(Decimal("6914.59"), computed=Decimal("6911.58"), lines=3)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest countries/do/tests/unit/test_regime.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'REGIME' from 'ledger_country_do.regime'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/regime/definitions.py` (the whole file):

```python
"""The schema of a regime: provisional, local copies of the kernel v2 regime types (spec §5.1, §8.2).

The kernel v2, which will hold these types (``ledger_kernel.regimes``), is built by the restructure in parallel
with this spike; the spike keeps copies under the spec's names and fields and imports nothing of the workspace.
The integration milestone replaces this module with imports from the kernel.

Legal rules are effective-dated data (spec rule 12): a ``Timeline`` lists a value's versions newest first and
answers the one in force on a day. Standard library only.
"""

import itertools
from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from collections.abc import Callable, Mapping
lazy from datetime import date
lazy from decimal import Decimal


class RegimeDataError(ValueError):
    """A regime's data breaks the regime schema: a defect of the pack, raised when the pack is built."""


class NotInForceError(LookupError):
    """A dated value was asked for a day before its oldest version."""

    def __init__(self, name: str, day: date) -> None:
        super().__init__(f"{name} has no value in force on {day.isoformat()}")


class UnknownEntryError(LookupError):
    """A regime was asked for an entry it does not declare."""

    def __init__(self, kind: str, key: str) -> None:
        super().__init__(f"regime declares no {kind} {key!r}")


class TaxIdCheck(StrEnum):
    """What a scheme's check says of a text (ADR 0006)."""

    VALID = "valid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    """Well formed, but not the check digit the algorithm predicts: reported; refused only where enforced."""
    INVALID_FORMAT = "invalid_format"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a value of a regime comes from."""

    title: str
    url: str
    version: str
    retrieved_on: date


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax-identifier scheme (``<cc>.<name>``): its length, its check, whether its check digit is enforced."""

    key: str
    label: str
    digits: int
    check_digit_enforced: bool
    check: Callable[[str], TaxIdCheck]
    source: Source


@final
@dataclass(frozen=True, slots=True)
class Since[T]:
    """A value in force from ``since`` (inclusive) until the next newer version of its timeline."""

    since: date
    value: T


@final
@dataclass(frozen=True, slots=True)
class Timeline[T]:
    """A value's versions, newest first; ``at(day)`` is the one in force on ``day``."""

    name: str
    entries: tuple[Since[T], ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise RegimeDataError(f"{self.name}: a timeline holds at least one value")
        for newer, older in itertools.pairwise(self.entries):
            if newer.since <= older.since:
                raise RegimeDataError(f"{self.name}: versions are listed newest first, each after the next")

    def at(self, day: date) -> T:
        """The version in force on ``day``; ``NotInForceError`` before the oldest one."""
        for entry in self.entries:
            if entry.since <= day:
                return entry.value
        raise NotInForceError(self.name, day)


class GenericCategory(StrEnum):
    """The country-neutral kind of a tax category (UBL-like; v1 M5 decision 3)."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    EXEMPT = "exempt"
    OUTSIDE_SCOPE = "outside_scope"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxCategoryDef:
    """A tax category and its dated percent; ``rates`` is ``None`` for a category without one (exempt)."""

    key: str
    label: str
    generic: GenericCategory
    rates: Timeline[Decimal] | None
    source: Source

    def rate_at(self, day: date) -> Decimal | None:
        """The percent in force on ``day``; ``None`` for a category without a rate."""
        return None if self.rates is None else self.rates.at(day)


class DocumentKind(StrEnum):
    """The commercial kind a fiscal document type covers (spec §7.6)."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


class Direction(StrEnum):
    """Whether the company sells (issues to a buyer) or buys (self-bills a seller)."""

    SALES = "sales"
    PURCHASE = "purchase"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentTypeDef:
    """A fiscal document type of the regime (an e-CF type in the Dominican Republic)."""

    code: str
    label: str
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    source: Source


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RoundingRule:
    """How amounts round, and how far a declared amount may stray before the authority flags it."""

    mode: str
    """A ``decimal`` rounding constant, such as ``decimal.ROUND_HALF_UP``."""
    places: int
    unit_price_places: int
    line_tolerance: Decimal
    global_tolerance_per_line: Decimal
    source: Source


type ParameterValue = Decimal | int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:
    """A regime: a jurisdiction's tax law as typed, dated data (spec §8.2)."""

    key: str
    country: str
    currency: str
    time_zone: str
    labels: Mapping[str, str]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    rounding: RoundingRule
    parameters: Mapping[str, Timeline[ParameterValue]]
    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        declared = (
            ("tax-id scheme", [scheme.key for scheme in self.tax_id_schemes]),
            ("tax category", [category.key for category in self.tax_categories]),
            ("document type", [kind.code for kind in self.document_types]),
        )
        for name, keys in declared:
            if len(set(keys)) != len(keys):
                raise RegimeDataError(f"{self.key}: a {name} is declared twice")
        cited = {
            *(scheme.source for scheme in self.tax_id_schemes),
            *(category.source for category in self.tax_categories),
            *(kind.source for kind in self.document_types),
            self.rounding.source,
        }
        if not cited <= set(self.sources):
            raise RegimeDataError(f"{self.key}: a value cites a source the regime does not list")

    def category(self, key: str) -> TaxCategoryDef:
        """The tax category ``key``; ``UnknownEntryError`` if the regime declares none."""
        found = next((category for category in self.tax_categories if category.key == key), None)
        if found is None:
            raise UnknownEntryError("tax category", key)
        return found

    def document_type(self, code: str) -> DocumentTypeDef:
        """The document type ``code``; ``UnknownEntryError`` if the regime declares none."""
        found = next((kind for kind in self.document_types if kind.code == code), None)
        if found is None:
            raise UnknownEntryError("document type", code)
        return found

    def parameter(self, name: str, day: date) -> ParameterValue:
        """The parameter ``name`` in force on ``day``."""
        timeline = self.parameters.get(name)
        if timeline is None:
            raise UnknownEntryError("parameter", name)
        return timeline.at(day)
```

`countries/do/src/ledger_country_do/regime/sources.py` (the whole file):

```python
"""Where regime ``do``'s values come from: one ``Source`` per document, cited by the values that use it.

DGII's documents were fetched on 2026-10-12 from https://dgii.gov.do (index: "Documentación sobre e-CF");
`.superpowers/research/06-dgii-facts.md` lists them with their versions.
"""

from datetime import date
from typing import Final

from ledger_country_do.regime.definitions import Source

RETRIEVED: Final = date(2026, 10, 13)
COVERED_FROM: Final = date(2019, 1, 1)
"""The first day the pack asserts values for: channel do-ecf-v1's ``valid_from`` (spec §4.3)."""
_DGII_ECF: Final = (
    "https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/"
)
_FETCHED: Final = date(2026, 10, 12)

STDNUM_RNC: Final = Source(
    title="python-stdnum, stdnum.do.rnc: the RNC's check digit (weights 7 9 8 6 5 4 3 2, modulo 11)",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/rnc.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
STDNUM_CEDULA: Final = Source(
    title="python-stdnum, stdnum.do.cedula: the cédula's check digit (Luhn) and DGII's numbers that fail it",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/cedula.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
FORMAT_ECF: Final = Source(
    title="DGII, Formato Comprobante Fiscal Electrónico (e-CF) V1.0",
    url=_DGII_ECF + "Formatos%20XML/Formato%20Comprobante%20Fiscal%20Electr%C3%B3nico%20(e-CF)%20V1.0.pdf",
    version="Versión 1.0, Octubre 2025",
    retrieved_on=_FETCHED,
)
TECHNICAL_REPORT: Final = Source(
    title="DGII, Informe Técnico e-CF v1.0 (§12 tolerancia, §13 redondeo, §18 representación impresa)",
    url=_DGII_ECF + "Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Informe%20T%C3%A9cnico%20e-CF%20v1.0.pdf",
    version="changelog 23-03-2026",
    retrieved_on=_FETCHED,
)
SERVICES_DGII: Final = Source(
    title="DGII, Descripción Técnica Servicios DGII (recepción, RFCE, consultas)",
    url=_DGII_ECF + "Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Descripcion%20Tecnica%20Servicios%20DGII.pdf",
    version="changelog 02-01-2026",
    retrieved_on=_FETCHED,
)
DECREE_587_24: Final = Source(
    title="Decreto 587-24, Reglamento de aplicación de la Ley 32-23 (Arts. 39-43, contingencia)",
    url="https://dgii.gov.do/legislacion/decretos/Documents/2024/Decreto587-24.pdf",
    version="10-10-2024",
    retrieved_on=_FETCHED,
)
ALL: Final = (STDNUM_RNC, STDNUM_CEDULA, FORMAT_ECF, TECHNICAL_REPORT, SERVICES_DGII, DECREE_587_24)
```

`countries/do/src/ledger_country_do/regime/taxes.py`:

```python
"""ITBIS, the Dominican VAT: its categories and their dated rates (FMT, Totales: tasas 18 %, 16 %, 0 %).

The timelines start on ``COVERED_FROM`` (2019-01-01, do-ecf-v1's ``valid_from``): every e-CF falls after it,
and the rates' earlier history (18 % from Ley 253-12; 16 % reached in phases from 2013 to 2016) is not asserted.
A rate change is a new ``Since`` at the head of a timeline, never an edit of an old one.
"""

from decimal import Decimal
from typing import Final

from ledger_country_do.regime.definitions import GenericCategory, Since, TaxCategoryDef, Timeline
from ledger_country_do.regime.sources import COVERED_FROM, FORMAT_ECF


def _percent(key: str, percent: int) -> Timeline[Decimal]:
    return Timeline(key, (Since(COVERED_FROM, Decimal(percent)),))


STANDARD: Final = TaxCategoryDef(
    key="do.itbis.standard",
    label="ITBIS 18 %",
    generic=GenericCategory.STANDARD,
    rates=_percent("do.itbis.standard", 18),
    source=FORMAT_ECF,
)
REDUCED: Final = TaxCategoryDef(
    key="do.itbis.reduced",
    label="ITBIS 16 %",
    generic=GenericCategory.REDUCED,
    rates=_percent("do.itbis.reduced", 16),
    source=FORMAT_ECF,
)
ZERO: Final = TaxCategoryDef(
    key="do.itbis.zero",
    label="ITBIS 0 %",
    generic=GenericCategory.ZERO,
    rates=_percent("do.itbis.zero", 0),
    source=FORMAT_ECF,
)
EXEMPT: Final = TaxCategoryDef(
    key="do.itbis.exempt", label="Exento", generic=GenericCategory.EXEMPT, rates=None, source=FORMAT_ECF
)
CATEGORIES: Final = (STANDARD, REDUCED, ZERO, EXEMPT)
```

`countries/do/src/ledger_country_do/regime/document_types.py`:

```python
"""The ten e-CF types (FMT; spec §8.6.2): kind, direction, and whether the buyer's tax id is required.

``requires_buyer_tax_id`` is what the e-CF schemas demand (``RNCComprador`` required in types 31, 41 and 45);
for a purchase (41, 43, 47) the "buyer" block names the seller the company self-bills. Corrections (which types
a note may modify) are v2-M3's.
"""

from typing import Final

from ledger_country_do.regime.definitions import Direction, DocumentKind, DocumentTypeDef
from ledger_country_do.regime.sources import FORMAT_ECF

_I, _C, _D = DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE
_S, _P = Direction.SALES, Direction.PURCHASE


def _type(code: str, label: str, kind: DocumentKind, direction: Direction, *, buyer: bool) -> DocumentTypeDef:
    return DocumentTypeDef(
        code=code, label=label, kind=kind, direction=direction, requires_buyer_tax_id=buyer, source=FORMAT_ECF
    )


TYPES: Final = (
    _type("31", "Factura de Crédito Fiscal Electrónica", _I, _S, buyer=True),
    _type("32", "Factura de Consumo Electrónica", _I, _S, buyer=False),
    _type("33", "Nota de Débito Electrónica", _D, _S, buyer=False),
    _type("34", "Nota de Crédito Electrónica", _C, _S, buyer=False),
    _type("41", "Comprobante Electrónico de Compras", _I, _P, buyer=True),
    _type("43", "Comprobante Electrónico para Gastos Menores", _I, _P, buyer=False),
    _type("44", "Comprobante Electrónico para Regímenes Especiales", _I, _S, buyer=False),
    _type("45", "Comprobante Electrónico Gubernamental", _I, _S, buyer=True),
    _type("46", "Comprobante Electrónico para Exportaciones", _I, _S, buyer=False),
    _type("47", "Comprobante Electrónico para Pagos al Exterior", _I, _P, buyer=False),
)
```

`countries/do/src/ledger_country_do/regime/parameters.py`:

```python
"""Regime ``do``'s dated parameters, each with its source (spec §8.6.2)."""

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_country_do.regime.definitions import ParameterValue, Since, Source, Timeline
from ledger_country_do.regime.sources import COVERED_FROM, DECREE_587_24, FORMAT_ECF, SERVICES_DGII

RFCE_THRESHOLD: Final = "rfce_threshold"
"""A type-32 invoice whose total is below it is sent as a summary (RFCE); from it, in full (DT-DGII)."""
CONTINGENCY_CONNECTIVITY_HOURS: Final = "contingency_connectivity_hours"
"""Decreto 587-24 Art. 40.1: documents signed while offline are sent within 72 h of the connection's return."""
CONTINGENCY_PAPER_MAX_DAYS: Final = "contingency_paper_max_days"
"""Decreto 587-24 Arts. 40.2-42: a contingency with paper NCF lasts at most 15 calendar days."""
CONTINGENCY_REPLACEMENT_DAYS: Final = "contingency_replacement_days"
"""Decreto 587-24 Art. 42: replacement e-CF go to DGII within 30 calendar days after the contingency."""
LEGAL_NAME_MAX: Final = "legal_name_max"
LINES_MAX: Final = "lines_max"
RFCE_LINES_MAX: Final = "rfce_lines_max"
"""[06] §2: an RFCE-summarized type 32 may hold 10,000 lines; the e-CF 32 schema says 1,000 (unused: F7)."""
_DECREE_DATE: Final = date(2024, 10, 10)


def _since(name: str, day: date, value: ParameterValue) -> Timeline[ParameterValue]:
    return Timeline(name, (Since(day, value),))


PARAMETERS: Final[Mapping[str, Timeline[ParameterValue]]] = MappingProxyType(
    {
        RFCE_THRESHOLD: _since(RFCE_THRESHOLD, COVERED_FROM, Decimal("250000.00")),
        CONTINGENCY_CONNECTIVITY_HOURS: _since(CONTINGENCY_CONNECTIVITY_HOURS, _DECREE_DATE, 72),
        CONTINGENCY_PAPER_MAX_DAYS: _since(CONTINGENCY_PAPER_MAX_DAYS, _DECREE_DATE, 15),
        CONTINGENCY_REPLACEMENT_DAYS: _since(CONTINGENCY_REPLACEMENT_DAYS, _DECREE_DATE, 30),
        LEGAL_NAME_MAX: _since(LEGAL_NAME_MAX, COVERED_FROM, 150),
        LINES_MAX: _since(LINES_MAX, COVERED_FROM, 1000),
        RFCE_LINES_MAX: _since(RFCE_LINES_MAX, COVERED_FROM, 10000),
    }
)
PARAMETER_SOURCES: Final[Mapping[str, Source]] = MappingProxyType(
    {
        RFCE_THRESHOLD: SERVICES_DGII,
        CONTINGENCY_CONNECTIVITY_HOURS: DECREE_587_24,
        CONTINGENCY_PAPER_MAX_DAYS: DECREE_587_24,
        CONTINGENCY_REPLACEMENT_DAYS: DECREE_587_24,
        LEGAL_NAME_MAX: FORMAT_ECF,
        LINES_MAX: FORMAT_ECF,
        RFCE_LINES_MAX: SERVICES_DGII,
    }
)
```

`countries/do/src/ledger_country_do/regime/rounding.py`:

```python
"""DGII's rounding and tolerance (IT §13 "Regla de Redondeo", §12 "Regla de tolerancia. Cuadratura").

Amounts carry two decimals, the third rounding half up (750.5212 -> 750.52, 750.5276 -> 750.53); unit prices
carry up to four. Each detail line may differ from quantity x price by one unit, and the document's total by as
many units as it has lines; beyond that DGII answers *Aceptado Condicional*, which is valid but flagged.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from ledger_country_do.regime.definitions import RoundingRule
from ledger_country_do.regime.sources import TECHNICAL_REPORT

ROUNDING: Final = RoundingRule(
    mode=ROUND_HALF_UP,
    places=2,
    unit_price_places=4,
    line_tolerance=Decimal(1),
    global_tolerance_per_line=Decimal(1),
    source=TECHNICAL_REPORT,
)
CENT: Final = Decimal("0.01")


def round_amount(value: Decimal) -> Decimal:
    """``value`` to two places, half up."""
    return value.quantize(CENT, rounding=ROUNDING.mode)


def within_line_tolerance(declared: Decimal, *, quantity: Decimal, unit_price: Decimal) -> bool:
    """Whether a line's declared amount is within one unit of quantity x price."""
    return abs(declared - quantity * unit_price) <= ROUNDING.line_tolerance


def within_global_tolerance(declared: Decimal, *, computed: Decimal, lines: int) -> bool:
    """Whether a document's declared total is within as many units of the computed one as it has lines."""
    return abs(declared - computed) <= ROUNDING.global_tolerance_per_line * lines
```

`countries/do/src/ledger_country_do/regime/__init__.py` (the whole file):

```python
"""Regime ``do``: the Dominican Republic's tax law as typed, effective-dated data (spec §8.2, §8.6.2).

Standard library only (import-linter: "The DO regime is pure data"). ``REGIME`` is what the entry point
``ledger.countries.regimes`` will name (``ledger_country_do.regime:REGIME``, spec §4.3) once the runtime loads
regimes.
"""

from types import MappingProxyType
from typing import Final

from ledger_country_do.regime.definitions import RegimeDef
from ledger_country_do.regime.document_types import TYPES
from ledger_country_do.regime.parameters import PARAMETERS
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do.regime.sources import ALL
from ledger_country_do.regime.tax_ids import SCHEMES
from ledger_country_do.regime.taxes import CATEGORIES

REGIME: Final = RegimeDef(
    key="do",
    country="DO",
    currency="DOP",
    time_zone="America/Santo_Domingo",
    labels=MappingProxyType({"es": "República Dominicana", "en": "Dominican Republic"}),
    tax_id_schemes=SCHEMES,
    tax_categories=CATEGORIES,
    document_types=TYPES,
    rounding=ROUNDING,
    parameters=PARAMETERS,
    sources=ALL,
)
```

Submodules import from modules (`from ledger_country_do.regime.sources import ...`), never from the package `ledger_country_do.regime`, so the package's `__init__` and its submodules form no import cycle (basedpyright `reportImportCycles`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest countries/do/tests/unit/test_regime.py countries/do/tests/unit/test_tax_ids.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add countries/do
git commit -m "feat(do): regime do as dated data: ITBIS, the ten e-CF types, parameters, rounding and tolerance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: DGII's XSDs vendored and pinned, hardened XML reading, validation, element names

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/domain/errors.py`
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd/{__init__, published}.py` and, downloaded by Step 4 and never edited, `adapters/xsd/{ecf-31, ecf-32, ecf-33, ecf-34, ecf-41, ecf-43, ecf-44, ecf-45, ecf-46, ecf-47, rfce-32, anecf, acecf, arecf, semilla}.xsd`
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/{__init__, parsing, schemas, names}.py`
- Modify: `countries/do/pyproject.toml` (`dependencies = ["lxml>=6.1.3"]`)
- Test: `countries/do/tests/contract/test_xsd_pins.py`, `countries/do/tests/unit/test_xml_parsing.py`, `countries/do/tests/unit/test_xml_names.py`

**Interfaces:**
- Consumes: nothing of other tasks.
- Produces: `domain.errors`: `type ErrorKind`, `EcfError` (`kind`, `code`), `EcfInvalidError`, `EcfConflictError`, `EcfUnavailableError`, `truncate(text, limit)`. `adapters.xsd.published`: `SOURCE`, `FETCHED_ON`, `Format` (`ECF_31` … `ECF_47`, `RFCE`, `ANECF`, `ACECF`, `ARECF`, `SEED`; values are the vendored file names), `Published(format, name, sha256, root)` with `url`, `PUBLISHED`, `Correction(format, wrong, right, count, reason)`, `CORRECTIONS`. `adapters.xml.parsing`: `DSIG`, `SIGNATURE`, `MAX_DOCUMENT` (4 MiB), `UnreadableDocumentError` (`do_ecf_document_unreadable`) with `DocumentTooLargeError`, `DoctypeRefusedError`, `MalformedDocumentError`; `parser() -> etree.XMLParser` (per thread), `require_readable(document, *, limit=MAX_DOCUMENT)`, `texts(document, *paths) -> tuple[str | None, ...]`. `adapters.xml.schemas`: `MAX_VIOLATIONS`, `SchemaDriftError`, `SchemaChangedError`, `CorrectionDriftError`, `Violation(line, path, message)`, `SchemaViolationError` (`do_ecf_schema_invalid`, `.violations`), `published_bytes(format)`, `corrected(format)`, `schema(format)` (per thread), `violations(format, document, *, unsigned=False)`, `checked(format, document, *, unsigned) -> bytes`. `adapters.xml.names`: `UndeclaredElementError`, `declared(format)`, `element(format, name)`.

- [ ] **Step 1: Dependency**

`countries/do/pyproject.toml`: `dependencies = ["lxml>=6.1.3"]`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/tests/contract/test_xsd_pins.py`:

```python
import hashlib
from types import MappingProxyType

import pytest
import xmlschema
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml import schemas
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import SchemaDriftError, corrected, published_bytes
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import CORRECTIONS, PUBLISHED, SOURCE, Format, Published

XS = "{http://www.w3.org/2001/XMLSchema}"
NOT_AS_PUBLISHED = frozenset(correction.format for correction in CORRECTIONS)


@pytest.mark.parametrize("format_", list(Format))
def test_every_vendored_schema_is_the_file_dgii_published(format_: Format) -> None:
    published = PUBLISHED[format_]
    assert hashlib.sha256(published_bytes(format_)).hexdigest() == published.sha256
    assert published.url.startswith(SOURCE)
    assert " " not in published.url


@pytest.mark.parametrize("format_", list(Format))
def test_every_schema_compiles_in_both_implementations_once_corrected(format_: Format) -> None:
    data = corrected(format_)
    etree.XMLSchema(etree.fromstring(data))
    xmlschema.XMLSchema10(data.decode("utf-8"))
    root = etree.fromstring(data).find(f"{XS}element")
    assert root is not None
    assert root.get("name") == PUBLISHED[format_].root


@pytest.mark.parametrize("format_", sorted(NOT_AS_PUBLISHED))
def test_the_corrected_schemas_do_not_compile_as_published(format_: Format) -> None:
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(etree.fromstring(published_bytes(format_)))


def test_a_schema_that_changed_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    def changed(format_: Format) -> bytes:
        del format_
        return b"<changed/>"

    monkeypatch.setattr(schemas, "published_bytes", changed)
    with pytest.raises(SchemaDriftError, match="is not the file pinned"):
        corrected(Format.SEED)


def test_a_correction_that_no_longer_applies_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = published_bytes(Format.ACECF).replace(b"(?:", b"(")
    pinned = Published(
        format=Format.ACECF, name="ACECF v.1.0.xsd", sha256=hashlib.sha256(fixed).hexdigest(), root="ACECF"
    )

    def fixed_by_dgii(format_: Format) -> bytes:
        del format_
        return fixed

    monkeypatch.setattr(schemas, "published_bytes", fixed_by_dgii)
    monkeypatch.setattr(schemas, "PUBLISHED", MappingProxyType({Format.ACECF: pinned}))
    with pytest.raises(SchemaDriftError, match="holds 0 of a correction pinned at 1"):
        corrected(Format.ACECF)
```

`countries/do/tests/unit/test_xml_parsing.py`:

```python
import threading
from typing import Final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    MAX_DOCUMENT,
    UnreadableDocumentError,
    require_readable,
    texts,
)
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import (
    SchemaViolationError,
    checked,
    schema,
    violations,
)
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

SEED: Final = b"<SemillaModel><valor>abc</valor><fecha>2026-10-12T09:00:00-04:00</fecha></SemillaModel>"
SIGNED_SEED: Final = SEED.replace(
    b"</SemillaModel>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></SemillaModel>'
)


def test_a_document_is_checked_as_it_will_be_once_signed() -> None:
    assert violations(Format.SEED, SEED, unsigned=True) == ()
    assert violations(Format.SEED, SIGNED_SEED) == ()
    (missing,) = violations(Format.SEED, SEED)
    assert missing.path == "/SemillaModel"
    assert "Missing child element" in missing.message


def test_a_violation_says_where_and_cuts_what_libxml2_quotes() -> None:
    wrong = SEED.replace(b"2026-10-12T09:00:00-04:00", b"x" * 400)
    (violation,) = violations(Format.SEED, wrong, unsigned=True)
    assert (violation.line, violation.path) == (1, "/SemillaModel/fecha")
    assert len(violation.message) <= 160


def test_checked_returns_the_document_or_refuses_it_with_its_violations() -> None:
    assert checked(Format.SEED, SEED, unsigned=True) == SEED
    with pytest.raises(SchemaViolationError) as caught:
        checked(Format.SEED, SEED, unsigned=False)
    assert caught.value.code == "do_ecf_schema_invalid"
    assert len(caught.value.violations) == 1


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (b"x" * (MAX_DOCUMENT + 1), "do_ecf_document_too_large"),
        (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', "do_ecf_document_doctype_refused"),
        (b"<!doctype a><a/>", "do_ecf_document_doctype_refused"),
        (b'<a/><!DOCTYPE a SYSTEM "http://169.254.169.254/">', "do_ecf_document_doctype_refused"),
        (b"<a>", "do_ecf_document_malformed"),
    ],
    ids=["too-large", "entity", "doctype-lowercase", "doctype-anywhere", "malformed"],
)
def test_what_cannot_be_read_safely_is_refused_unread(document: bytes, code: str) -> None:
    with pytest.raises(UnreadableDocumentError) as caught:
        texts(document, "b")
    assert (caught.value.code, caught.value.kind) == (code, "invalid")
    assert "ENTITY" not in str(caught.value)


def test_the_limit_is_the_caller_s() -> None:
    require_readable(b"<a/>", limit=4)
    with pytest.raises(UnreadableDocumentError):
        require_readable(b"<ab/>", limit=4)


def test_texts_reads_what_it_is_asked_and_nothing_else() -> None:
    assert texts(b"<a><b>x</b><c/></a>", "b", "c", "d") == ("x", "", None)


def test_each_thread_compiles_its_own_schema() -> None:
    here = schema(Format.SEED)
    assert schema(Format.SEED) is here
    elsewhere: list[object] = []
    thread = threading.Thread(target=lambda: elsewhere.append(schema(Format.SEED)))
    thread.start()
    thread.join()
    assert elsewhere[0] is not here
```

`countries/do/tests/unit/test_xml_names.py`:

```python
import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.names import UndeclaredElementError, declared, element
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format


def test_an_element_is_named_as_its_schema_declares_it() -> None:
    assert element(Format.ECF_31, "rncemisor") == "RNCEmisor"
    assert element(Format.ECF_31, "encf") == "eNCF"
    assert element(Format.ANECF, "rncemisor") == "RncEmisor"
    assert element(Format.RFCE, "codigoseguridadecf") == "CodigoSeguridadeCF"


def test_an_element_a_schema_does_not_declare_is_refused() -> None:
    with pytest.raises(UndeclaredElementError, match=r"ecf-43.xsd"):
        element(Format.ECF_43, "comprador")


def test_names_that_differ_only_in_case_are_left_out() -> None:
    assert "subtotalimpuestoadicional" not in declared(Format.ECF_31)
    assert all(key == key.lower() for key in declared(Format.ECF_31))
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries/do/tests/contract/test_xsd_pins.py countries/do/tests/unit/test_xml_parsing.py countries/do/tests/unit/test_xml_names.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.adapters.xml'`.

- [ ] **Step 4: Implement, and vendor DGII's files**

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/errors.py`:

```python
"""The errors do-ecf-v1 raises on purpose.

Each has a ``kind`` (how it crosses a boundary: invalid, conflict, unavailable, internal) and a stable
``code``, mirroring ``ledger_kernel.errors``; the integration milestone makes these bases subclasses of the
kernel's (the spike imports nothing of the workspace). A message is built inside its class and never quotes a
document, an amount, a token or a tax id.
"""

from typing import ClassVar, Final, Literal

type ErrorKind = Literal["invalid", "conflict", "unavailable", "internal"]
_ELLIPSIS: Final = "…"


class EcfError(Exception):
    """Base of every error do-ecf-v1 raises on purpose: a defect unless a subclass says otherwise."""

    kind: ClassVar[ErrorKind] = "internal"
    code: ClassVar[str] = "do_ecf_internal"


class EcfInvalidError(EcfError):
    """The input breaks a rule: sending it again cannot work."""

    kind: ClassVar[ErrorKind] = "invalid"
    code: ClassVar[str] = "do_ecf_invalid"


class EcfConflictError(EcfError):
    """The current state forbids it."""

    kind: ClassVar[ErrorKind] = "conflict"
    code: ClassVar[str] = "do_ecf_conflict"


class EcfUnavailableError(EcfError):
    """Something outside failed; the same call may succeed later."""

    kind: ClassVar[ErrorKind] = "unavailable"
    code: ClassVar[str] = "do_ecf_unavailable"


def truncate(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, an ellipsis marking the cut."""
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd/__init__.py`:

```python
"""DGII's XSDs for e-CF v1.0, byte for byte as DGII published them (``published`` pins each one)."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd/published.py` — v1 M4 Task 1's `ledger_dgii.published`, unchanged in content (`SOURCE`, `FETCHED_ON = "2026-10-12"`, `Format`, `Published`, `PUBLISHED` with the fifteen SHA-256 pins, `Correction`, `CORRECTIONS`); the full text:

```python
"""What DGII publishes for e-CF v1.0, as it published it: the XSDs, where each came from, and their SHA-256.

The files beside this module are DGII's own, byte for byte (BOMs and CRLF line ends included:
``.gitattributes`` keeps git from touching them), renamed only so that no path holds a space. Each is pinned to
the SHA-256 it had when fetched: a file that changed fails ``test_xsd_pins``, and replacing one is a deliberate
change of this module (a new hash, a new date), never a silent download.

Three of the fifteen do not compile as DGII publishes them; ``CORRECTIONS`` holds the least edit that makes each
compile, applied when the schema is loaded and never to the file.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Mapping

SOURCE: Final = (
    "https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/"
    "Documentacin%20sobre%20eCF/Documentaci%C3%B3n%20T%C3%A9cnica%20(XSD)/"
)
"""Where DGII publishes its XSDs (its page "Documentación sobre e-CF" links each file under it)."""
FETCHED_ON: Final = "2026-10-12"


class Format(StrEnum):
    """Each XML format DGII defines, by the file of its schema."""

    ECF_31 = "ecf-31.xsd"
    ECF_32 = "ecf-32.xsd"
    ECF_33 = "ecf-33.xsd"
    ECF_34 = "ecf-34.xsd"
    ECF_41 = "ecf-41.xsd"
    ECF_43 = "ecf-43.xsd"
    ECF_44 = "ecf-44.xsd"
    ECF_45 = "ecf-45.xsd"
    ECF_46 = "ecf-46.xsd"
    ECF_47 = "ecf-47.xsd"
    RFCE = "rfce-32.xsd"
    """The summary of a consumer invoice (type 32) under DOP 250,000."""
    ANECF = "anecf.xsd"
    """The annulment of unused e-NCF ranges."""
    ACECF = "acecf.xsd"
    """A buyer's commercial approval or rejection."""
    ARECF = "arecf.xsd"
    """A buyer's acknowledgement of receipt."""
    SEED = "semilla.xsd"
    """The authentication seed (``SemillaModel``), signed to obtain a token."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Published:
    """One schema as DGII published it."""

    format: Format
    name: str
    """The file's name on DGII's site."""
    sha256: str
    """Of the file as fetched, lowercase hexadecimal."""
    root: str
    """The root element of the documents it describes."""

    @property
    def url(self) -> str:
        """Where it was fetched from."""
        return SOURCE + self.name.replace(" ", "%20")


def _published(format_: Format, name: str, sha256: str, root: str) -> Published:
    return Published(format=format_, name=name, sha256=sha256, root=root)


PUBLISHED: Final[Mapping[Format, Published]] = MappingProxyType(
    {
        entry.format: entry
        for entry in (
            _published(
                Format.ECF_31,
                "e-CF 31 v.1.0.xsd",
                "6f2909a93d84919518d2ae3c77fead4b35c3e8c95996b8af67b0040c2e2be298",
                "ECF",
            ),
            _published(
                Format.ECF_32,
                "e-CF 32 v.1.0.xsd",
                "151676df03c61335b4ac1eca97f99395488d57ea92793b49353602871e651f3c",
                "ECF",
            ),
            _published(
                Format.ECF_33,
                "e-CF 33 v.1.0.xsd",
                "087cd71beba11c19cf548e22b96363cb4aa210dc6f5c3c8a62122a42c88d2866",
                "ECF",
            ),
            _published(
                Format.ECF_34,
                "e-CF 34 v.1.0.xsd",
                "6c62ebabdea9a6f6d62ae85d9e1c2eb788f44f3f0b61945fe116700c4eaac2da",
                "ECF",
            ),
            _published(
                Format.ECF_41,
                "e-CF 41 v.1.0.xsd",
                "eae1993c637375bc1cbe80932411d87f5680cd54e77e4d1b2752d72a6c8b2ab3",
                "ECF",
            ),
            _published(
                Format.ECF_43,
                "e-CF 43 v.1.0.xsd",
                "776f030980c2c50cf0221e9263c55f367c8631727adff00ab45e2c7c1abafc52",
                "ECF",
            ),
            _published(
                Format.ECF_44,
                "e-CF 44 v.1.0.xsd",
                "19834f37a9f0e2db40f00c80d07c2bf92f9019f23474480f32cef1ba06af4e67",
                "ECF",
            ),
            _published(
                Format.ECF_45,
                "e-CF 45 v.1.0.xsd",
                "030492cc8ef7d1a09a89b16b241f8e5c4920dfd09c0b629b1db8e68406ecd6ca",
                "ECF",
            ),
            _published(
                Format.ECF_46,
                "e-CF 46 v.1.0.xsd",
                "e7f8613ade25c7efb88e84d34d7c0ad330d1b22238ad9ef90c80aed01911d67b",
                "ECF",
            ),
            _published(
                Format.ECF_47,
                "e-CF 47 v.1.0.xsd",
                "14daf18f52f63dd80e439a18fb60a102d74e71243ee90ee253367f61ce8e1994",
                "ECF",
            ),
            _published(
                Format.RFCE,
                "RFCE 32 v.1.0.xsd",
                "6aad535875661b05eef072963295202fc94b3b320b257791feb8d3c39a5f8ee6",
                "RFCE",
            ),
            _published(
                Format.ANECF,
                "ANECF v.1.0.xsd",
                "af2e6a16c2900dfa55264d6ebec58ccbbbb25c1eb9821ee18ccc44fc142d0e78",
                "ANECF",
            ),
            _published(
                Format.ACECF,
                "ACECF v.1.0.xsd",
                "072f65de202df8ec136a8d4493e0592172690a4e181047245401cc2e7b23c095",
                "ACECF",
            ),
            _published(
                Format.ARECF,
                "ARECF v1.0.xsd",
                "c6d186167159110959eb3f54706ecc7462ad9128fc2c3e03440c8352b1c66f10",
                "ARECF",
            ),
            _published(
                Format.SEED,
                "Semilla v.1.0.xsd",
                "bebef2033c930a30c8cef45b513db00403423125a7f3f48e100a73e3e355f67e",
                "SemillaModel",
            ),
        )
    }
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Correction:
    """The least edit that makes a published schema compile: ``wrong``, ``count`` times, becomes ``right``."""

    format: Format
    wrong: str
    right: str
    count: int
    reason: str


CORRECTIONS: Final[tuple[Correction, ...]] = (
    Correction(
        format=Format.ECF_31,
        wrong='name=" IndicadorServicioTodoIncluidoType"',
        right='name="IndicadorServicioTodoIncluidoType"',
        count=1,
        reason="a space before the type's name leaves the element that uses it without a type",
    ),
    Correction(
        format=Format.ACECF,
        wrong="(?:",
        right="(",
        count=1,
        reason="a non-capturing group is Perl's, not XML Schema's: the date pattern does not compile",
    ),
    Correction(
        format=Format.RFCE,
        wrong="(?:",
        right="(",
        count=4,
        reason="a non-capturing group is Perl's, not XML Schema's: date and amount patterns do not compile",
    ),
)
"""Every correction, each checked to apply exactly ``count`` times: when DGII fixes a file its hash changes, and
``test_xsd_pins`` says so before a correction could silently stop applying."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/__init__.py`:

```python
"""DGII's XML: read hardened, validated against DGII's schemas, written in their order and with their names."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/parsing.py`:

```python
"""Reading XML that comes from outside, hardened (spec §8.4 ``xml``, §10).

A document is checked before it is parsed: at most ``MAX_DOCUMENT`` bytes, and no ``<!DOCTYPE`` anywhere in it
(a DTD, internal or external, is never read: no entity expansion, no external fetch). The parser then resolves
no entity, loads no DTD, reaches no network and keeps libxml2's own limits on depth and text (``huge_tree``
off). lxml parsers keep per-object state, so each thread makes its own.

Nothing here returns an lxml element (their class is private to the type checkers): callers parse with
``parser()`` after ``require_readable``, or ask ``texts`` for the values they need.
"""

import re
import threading
from typing import ClassVar, Final, final

from lxml import etree

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

DSIG: Final = "http://www.w3.org/2000/09/xmldsig#"
SIGNATURE: Final = f"{{{DSIG}}}Signature"
MAX_DOCUMENT: Final = 4 * 1024 * 1024
"""The largest document read: a type-32 e-CF of 1,000 lines is a few hundred kilobytes."""
_DOCTYPE: Final = re.compile(rb"<!DOCTYPE", re.IGNORECASE)


class UnreadableDocumentError(EcfInvalidError):
    """Bytes that are not a document read here; each subclass says why, never quoting the document."""

    code: ClassVar[str] = "do_ecf_document_unreadable"


class DocumentTooLargeError(UnreadableDocumentError):
    """A document over the limit: refused unread."""

    code: ClassVar[str] = "do_ecf_document_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a document is at most {limit} bytes")


class DoctypeRefusedError(UnreadableDocumentError):
    """A document that declares a DOCTYPE: no DTD, internal or external, is ever read."""

    code: ClassVar[str] = "do_ecf_document_doctype_refused"

    def __init__(self) -> None:
        super().__init__("a document that declares a DOCTYPE is refused")


class MalformedDocumentError(UnreadableDocumentError):
    """Not well-formed XML. The parser's message quotes the document, so it is not repeated."""

    code: ClassVar[str] = "do_ecf_document_malformed"

    def __init__(self) -> None:
        super().__init__("the document is not well-formed XML")


@final
class _PerThread(threading.local):
    """This thread's hardened parser."""

    def __init__(self) -> None:
        self.parser: etree.XMLParser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
        )


_THREAD: Final = _PerThread()


def parser() -> etree.XMLParser:
    """The calling thread's hardened parser (see the module)."""
    return _THREAD.parser


def require_readable(document: bytes, *, limit: int = MAX_DOCUMENT) -> None:
    """Refuse, unread, a document over ``limit`` bytes or one that declares a DOCTYPE."""
    if len(document) > limit:
        raise DocumentTooLargeError(limit)
    if _DOCTYPE.search(document) is not None:
        raise DoctypeRefusedError


def texts(document: bytes, *paths: str) -> tuple[str | None, ...]:
    """The text at each of ``paths`` (ElementPath from the root; ``{namespace}name``), ``None`` where absent."""
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    return tuple(root.findtext(path) for path in paths)
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/schemas.py`:

```python
"""DGII's schemas, loaded with their corrections, and the validation of documents against them.

Validation runs on libxml2 (lxml's ``XMLSchema``, about half a millisecond for a 60-line e-CF); the tests and
the fake DGII validate with ``xmlschema`` too, an implementation that shares no code with it. A compiled schema
is not shared between threads (lxml keeps per-object state).

Every DGII document ends with its XML signature, which each schema demands (``xs:any``, at least once). A
document is checked before it is signed as it will be once signed: ``unsigned=True`` validates it with an
empty ``Signature`` element in the place the signature will take.
"""

import hashlib
import threading
from dataclasses import dataclass
from importlib.resources import files
from typing import ClassVar, Final, final

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import CORRECTIONS, PUBLISHED
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError, truncate
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

MAX_VIOLATIONS: Final = 20
"""How many problems a refusal lists."""
_MESSAGE: Final = 160
"""How much of libxml2's message a violation keeps: it names the element and facet, and may quote a value."""
_XSD_PACKAGE: Final = "ledger_country_do.channels.ecf_v1.adapters.xsd"


class SchemaDriftError(EcfError):
    """A vendored schema is not as pinned: a defect to fix, never a caller's mistake."""

    code: ClassVar[str] = "do_ecf_schema_drift"


class SchemaChangedError(SchemaDriftError):
    """A vendored file is not the one whose SHA-256 ``published`` pins."""

    def __init__(self, format_: Format) -> None:
        super().__init__(f"DGII's schema {format_.value} is not the file pinned in adapters.xsd.published")


class CorrectionDriftError(SchemaDriftError):
    """A correction no longer applies as many times as pinned."""

    def __init__(self, format_: Format, *, found: int, pinned: int) -> None:
        super().__init__(f"DGII's schema {format_.value} holds {found} of a correction pinned at {pinned}")


@final
@dataclass(frozen=True, slots=True)
class Violation:
    """One way a document breaks its schema: where (line and element path), and what libxml2 says (cut short)."""

    line: int
    path: str
    message: str


class SchemaViolationError(EcfInvalidError):
    """A document DGII's schema refuses, with where and why (at most ``MAX_VIOLATIONS``, each cut short)."""

    code: ClassVar[str] = "do_ecf_schema_invalid"

    def __init__(self, format_: Format, found: tuple[Violation, ...]) -> None:
        self.violations: tuple[Violation, ...] = found
        listed = "; ".join(f"{violation.path}: {violation.message}" for violation in found)
        super().__init__(f"DGII's schema {format_.value} refuses the document: {listed}")


def published_bytes(format_: Format) -> bytes:
    """The schema of ``format_`` exactly as DGII published it."""
    return files(_XSD_PACKAGE).joinpath(format_.value).read_bytes()


def corrected(format_: Format) -> bytes:
    """The schema of ``format_`` with its corrections applied; ``SchemaDriftError`` if it is not as pinned."""
    data = published_bytes(format_)
    if hashlib.sha256(data).hexdigest() != PUBLISHED[format_].sha256:
        raise SchemaChangedError(format_)
    text = data.decode("utf-8-sig")
    for correction in CORRECTIONS:
        if correction.format is not format_:
            continue
        found = text.count(correction.wrong)
        if found != correction.count:
            raise CorrectionDriftError(format_, found=found, pinned=correction.count)
        text = text.replace(correction.wrong, correction.right)
    return text.encode("utf-8")


@final
class _PerThread(threading.local):
    """The schemas this thread compiled."""

    def __init__(self) -> None:
        self.schemas: dict[Format, etree.XMLSchema] = {}


_THREAD: Final = _PerThread()


def schema(format_: Format) -> etree.XMLSchema:
    """The compiled schema of ``format_`` for the calling thread."""
    found = _THREAD.schemas.get(format_)
    if found is None:
        found = _THREAD.schemas[format_] = etree.XMLSchema(etree.fromstring(corrected(format_), parser()))
    return found


def violations(format_: Format, document: bytes, *, unsigned: bool = False) -> tuple[Violation, ...]:
    """How ``document`` breaks the schema of ``format_``; empty when it does not (``unsigned``: the module)."""
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    if unsigned:
        etree.SubElement(root, SIGNATURE)
    checking = schema(format_)
    if checking.validate(root):
        return ()
    return tuple(
        Violation(line=entry.line, path=entry.path or "", message=truncate(entry.message, _MESSAGE))
        for entry in list(checking.error_log)[:MAX_VIOLATIONS]
    )


def checked(format_: Format, document: bytes, *, unsigned: bool) -> bytes:
    """``document``, once ``format_``'s schema accepts it; ``SchemaViolationError`` otherwise."""
    found = violations(format_, document, unsigned=unsigned)
    if found:
        raise SchemaViolationError(format_, found)
    return document
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/names.py`:

```python
"""The names of DGII's elements, as each schema declares them, looked up by their lowercase spelling.

Code that writes a DGII document names each element in lowercase (``rncemisor``) and gets the name its schema
declares (``RNCEmisor``): every name written is one DGII's schema holds, so a misspelled one fails where it is
written (``UndeclaredElementError``), not later as a schema violation, and DGII's Spanish names live in DGII's
files only. Two names of one schema that differ only in case (``SubTotalImpuestoAdicional`` and
``SubtotalImpuestoAdicional``, additional taxes the spike does not write) cannot be looked up this way and are
left out.
"""

from collections import Counter
from functools import cache
from types import MappingProxyType
from typing import ClassVar, Final
lazy from collections.abc import Mapping

from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import parser
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format

_ELEMENT: Final = "{http://www.w3.org/2001/XMLSchema}element"


class UndeclaredElementError(EcfError):
    """Code named an element its schema does not declare: a defect of the writer."""

    code: ClassVar[str] = "do_ecf_element_undeclared"

    def __init__(self, format_: Format, name: str) -> None:
        super().__init__(f"DGII's schema {format_.value} declares no element {name}")


@cache
def declared(format_: Format) -> Mapping[str, str]:
    """The elements ``format_``'s schema declares, by their lowercase names (see the module)."""
    root = etree.fromstring(corrected(format_), parser())
    names = {name for found in root.iter(_ELEMENT) if (name := found.get("name")) is not None}
    spellings = Counter(name.lower() for name in names)
    return MappingProxyType({name.lower(): name for name in names if spellings[name.lower()] == 1})


def element(format_: Format, name: str) -> str:
    """The element ``format_`` declares as ``name`` in lowercase; ``UndeclaredElementError`` if none."""
    found = declared(format_).get(name)
    if found is None:
        raise UndeclaredElementError(format_, name)
    return found
```

Vendor the fifteen files, each checked against its pinned SHA-256 before it is written (a file DGII changed since 2026-10-12 stops the download: compare it with the published one, then pin the new hash and date deliberately, in a commit of its own):

```bash
uv run python - <<'EOF'
import hashlib
from pathlib import Path

import httpx

from ledger_country_do.channels.ecf_v1.adapters.xsd.published import PUBLISHED

target = Path("countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd")
with httpx.Client(timeout=60, follow_redirects=True) as client:
    for entry in PUBLISHED.values():
        data = client.get(entry.url).raise_for_status().content
        found = hashlib.sha256(data).hexdigest()
        if found != entry.sha256:
            raise SystemExit(f"{entry.name}: {found} is not the pinned {entry.sha256}")
        (target / entry.format.value).write_bytes(data)
print(sorted(path.name for path in target.glob("*.xsd")))
EOF
```

Expected: the fifteen file names. If DGII's site refuses a scripted download, download the file from DGII's page in a browser into the same name: the hash still decides. Then `git add --renormalize` is **not** used on these files (`-text` keeps their bytes).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/tests/contract/test_xsd_pins.py countries/do/tests/unit/test_xml_parsing.py countries/do/tests/unit/test_xml_names.py -q --no-cov`
Expected: PASS (fifteen hashes checked; fifteen schemas compiled in both implementations; three refuse to compile as published).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %. Check `git diff --stat` shows the XSDs byte-identical after `git add` (`git ls-files --eol countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xsd` reports `-text`).

- [ ] **Step 7: Commit**

```bash
git add countries/do
git commit -m "feat(do): DGII's XSDs vendored and pinned, corrected on load; hardened XML reading and validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The e-CF domain: e-NCF, notation, drafts of types 31 and 32, totals, routing; samples

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/domain/{encf, notation, drafts, totals, routing}.py`
- Create: `countries/do/testing/src/ledger_country_do_testing/samples.py`
- Modify: `countries/do/testing/pyproject.toml` (`dependencies = ["cryptography>=50.0.2", "ledger-country-do"]`)
- Test: `countries/do/tests/unit/test_{encf, drafts, totals, routing}.py`, `countries/do/testing/tests/test_samples.py`

**Interfaces:**
- Consumes: `domain.errors` (Task 3); `ledger_country_do.regime` (`REGIME`, `RegimeDef`, `RegimeDataError`, `tax_ids.normalize`, `tax_ids.scheme_of`, `rounding.round_amount`, `parameters.RFCE_THRESHOLD`) (Tasks 1-2).
- Produces: `domain.encf`: `EcfType` (`CREDIT_INVOICE = "31"` … `FOREIGN_PAYMENTS = "47"`), `SEQUENCE_DIGITS`, `MAX_SEQUENCE`, `InvalidEncfError` (`do_ecf_encf_invalid`), `Encf(ecf_type, sequence)` with `parse(text)` and `str()`. `domain.notation`: `SANTO_DOMINGO`, `dgii_date(date)`, `dgii_moment(datetime)`, `local_date(datetime)`, `dgii_amount(Decimal)`, `dgii_number(Decimal)`, `dgii_percent(Decimal)`. `domain.drafts`: `BillingIndicator`, `TAXED`, `CATEGORY_OF`, `GoodsOrService`, `IncomeType`, `PaymentType`, `SPIKE_TYPES`, `UNDATED_TYPES`, `MAX_LINES`, `DraftInvalidError` (`do_ecf_draft_invalid`, `.field`), `UnsupportedTypeError` (`do_ecf_type_unsupported`), `Issuer(rnc, legal_name, address)`, `Buyer(rnc=None, name=None)` with `anonymous`, `Line(name, quantity, unit_price, billing, kind=GOODS)`, `EcfDraft(encf, issued_on, issuer, lines, buyer=Buyer(), sequence_expires_on=None, income=OPERATIONS, payment=CASH)` with `ecf_type`. `domain.totals`: `RateGroup(indicator, base, percent, itbis)`, `Totals(line_amounts, groups, exempt, total)` with `group(indicator)`, `taxed_total`, `itbis_total`; `compute_totals(draft, regime=REGIME) -> Totals`. `domain.routing`: `Route` (`RECEPTION`, `RFCE`), `route(draft, totals, regime=REGIME) -> Route`. `ledger_country_do_testing.samples`: `ISSUER`, `BUYER`, `ISSUED_ON`, `SIGNED_AT`, `SEQUENCE_EXPIRES_ON`, `CREDIT_LINES`, `credit_invoice(sequence=1, *, issuer=ISSUER, buyer=BUYER)`, `consumer_invoice(sequence=1, *, large=False, buyer=None, issuer=ISSUER)`.

- [ ] **Step 1: Dependency**

`countries/do/testing/pyproject.toml`: `dependencies = ["cryptography>=50.0.2", "ledger-country-do"]` (no `[tool.uv.sources]`: Coordination 2). Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/tests/unit/test_encf.py`:

```python
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf, InvalidEncfError


def test_an_encf_is_e_the_type_and_ten_digits() -> None:
    encf = Encf(EcfType.CREDIT_INVOICE, 1)
    assert str(encf) == "E310000000001"
    assert Encf.parse("E320000000064") == Encf(EcfType.CONSUMER_INVOICE, 64)


@pytest.mark.parametrize(
    "text",
    ["E3100000000001", "E31000000001", "E990000000001", "e310000000001", "E31000000000A", "E31０000000001", ""],
    ids=["fourteen", "twelve", "unknown-type", "lowercase", "letter", "fullwidth-digit", "empty"],
)
def test_anything_else_is_not_an_encf(text: str) -> None:
    with pytest.raises(InvalidEncfError) as caught:
        Encf.parse(text)
    assert caught.value.code == "do_ecf_encf_invalid"


@pytest.mark.parametrize("sequence", [0, -1, MAX_SEQUENCE + 1])
def test_the_sequence_is_one_to_ten_digits(sequence: int) -> None:
    with pytest.raises(InvalidEncfError):
        Encf(EcfType.CONSUMER_INVOICE, sequence)


@given(st.sampled_from(list(EcfType)), st.integers(min_value=1, max_value=MAX_SEQUENCE))
def test_an_encf_reads_back_as_written(ecf_type: EcfType, sequence: int) -> None:
    encf = Encf(ecf_type, sequence)
    assert len(str(encf)) == 13
    assert Encf.parse(str(encf)) == encf
```

`countries/do/tests/unit/test_drafts.py`:

```python
from contextlib import AbstractContextManager
from dataclasses import replace
from decimal import Decimal
lazy from collections.abc import Callable

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    MAX_LINES,
    BillingIndicator,
    Buyer,
    DraftInvalidError,
    Issuer,
    Line,
    UnsupportedTypeError,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do_testing.samples import (
    BUYER,
    CREDIT_LINES,
    ISSUER,
    SEQUENCE_EXPIRES_ON,
    consumer_invoice,
    credit_invoice,
)

LINE = CREDIT_LINES[0]


def refused(field: str) -> AbstractContextManager[pytest.ExceptionInfo[DraftInvalidError]]:
    return pytest.raises(DraftInvalidError, match=f"^{field} ")


def test_the_samples_are_drafts_of_the_spike_s_two_types() -> None:
    assert credit_invoice().ecf_type is EcfType.CREDIT_INVOICE
    assert consumer_invoice().ecf_type is EcfType.CONSUMER_INVOICE
    assert consumer_invoice().buyer.anonymous
    assert not BUYER.anonymous


def test_only_types_31_and_32_are_drafted_in_the_spike() -> None:
    with pytest.raises(UnsupportedTypeError, match="e-CF type 33"):
        replace(credit_invoice(), encf=Encf(EcfType.DEBIT_NOTE, 1))


@pytest.mark.parametrize("count", [0, MAX_LINES + 1])
def test_a_draft_holds_one_to_a_thousand_lines(count: int) -> None:
    with refused("lines"):
        replace(credit_invoice(), lines=(LINE,) * count)


def test_the_sequence_s_expiry_is_given_exactly_for_dated_types() -> None:
    with refused("sequence_expires_on"):
        replace(credit_invoice(), sequence_expires_on=None)
    with refused("sequence_expires_on"):
        replace(consumer_invoice(), sequence_expires_on=SEQUENCE_EXPIRES_ON)


def test_a_credit_invoice_names_its_buyer() -> None:
    with refused("buyer"):
        replace(credit_invoice(), buyer=Buyer(name="SOMEONE"))
    with refused("buyer"):
        replace(credit_invoice(), buyer=Buyer(rnc="131098193"))


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("issuer.rnc", lambda: replace(ISSUER, rnc="1-01-85004-3")),
        ("issuer.rnc", lambda: replace(ISSUER, rnc="10185004")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name=" ")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name="X" * 151)),
        ("issuer.address", lambda: replace(ISSUER, address="X" * 101)),
        ("buyer.rnc", lambda: Buyer(rnc="ABC")),
        ("buyer.name", lambda: Buyer(name="X" * 151)),
        ("line.name", lambda: replace(LINE, name="X" * 81)),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal(0))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("1.005"))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("NaN"))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal("-1"))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal("1.00001"))),
        ("line.billing", lambda: replace(LINE, billing=BillingIndicator.NOT_BILLABLE)),
    ],
)
def test_each_field_is_checked_and_named_without_its_value(field: str, build: Callable[[], object]) -> None:
    with refused(field) as caught:
        build()
    assert caught.value.field == field
    assert "10185004" not in str(caught.value)


def test_a_cedula_is_taken_wherever_an_rnc_is() -> None:
    assert Issuer(rnc="00113918205", legal_name="JUAN PEREZ", address="CALLE 1").rnc == "00113918205"
```

`countries/do/tests/unit/test_totals.py`:

```python
import random
from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Line
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.definitions import RegimeDataError
from ledger_country_do.regime.taxes import STANDARD
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice

D = Decimal


def test_the_credit_invoice_s_totals_are_dgii_s() -> None:
    totals = compute_totals(credit_invoice())
    assert totals.line_amounts == (D("3000.00"), D("850.50"), D("361.00"))
    first, second = totals.groups
    assert (first.indicator, first.base, first.percent, first.itbis) == (BillingIndicator.ITBIS_1, D("3000.00"), 18, D("540.00"))
    assert (second.indicator, second.base, second.percent, second.itbis) == (BillingIndicator.ITBIS_2, D("850.50"), 16, D("136.08"))
    assert (totals.taxed_total, totals.exempt, totals.itbis_total, totals.total) == (
        D("3850.50"),
        D("361.00"),
        D("676.08"),
        D("4887.58"),
    )
    assert totals.group(BillingIndicator.ITBIS_3) is None


def test_a_document_without_taxed_lines_has_no_taxed_totals() -> None:
    exempt = Line(name="LIBRO", quantity=D(1), unit_price=D("100.00"), billing=BillingIndicator.EXEMPT)
    totals = compute_totals(replace(consumer_invoice(), lines=(exempt,)))
    assert (totals.groups, totals.taxed_total, totals.itbis_total, totals.total) == ((), None, None, D("100.00"))


def test_the_zero_rate_is_a_taxed_group_with_no_itbis() -> None:
    zero = Line(name="EXPORTACION", quantity=D(1), unit_price=D("100.00"), billing=BillingIndicator.ITBIS_3)
    totals = compute_totals(replace(consumer_invoice(), lines=(zero,)))
    (group,) = totals.groups
    assert (group.percent, group.itbis, totals.exempt, totals.total) == (0, D("0.00"), None, D("100.00"))


def test_a_taxed_category_without_a_rate_is_a_defect_of_the_regime() -> None:
    broken = replace(REGIME, tax_categories=(replace(STANDARD, rates=None), *REGIME.tax_categories[1:]))
    with pytest.raises(RegimeDataError, match=r"do.itbis.standard has no rate"):
        compute_totals(consumer_invoice(), broken)


_LINES = st.lists(
    st.builds(
        Line,
        name=st.just("ITEM"),
        quantity=st.decimals(min_value=D("0.01"), max_value=D(9999), places=2),
        unit_price=st.decimals(min_value=D(0), max_value=D(99999), places=4),
        billing=st.sampled_from([BillingIndicator.ITBIS_1, BillingIndicator.ITBIS_2, BillingIndicator.ITBIS_3, BillingIndicator.EXEMPT]),
    ),
    min_size=1,
    max_size=20,
)


@given(_LINES, st.randoms())
def test_the_total_is_the_sum_of_its_parts_whatever_the_order(lines: list[Line], order: random.Random) -> None:
    draft = replace(consumer_invoice(), lines=tuple(lines))
    totals = compute_totals(draft)
    parts = sum((group.base + group.itbis for group in totals.groups), D(0)) + (totals.exempt or D(0))
    assert totals.total == parts
    shuffled = list(lines)
    order.shuffle(shuffled)
    assert compute_totals(replace(draft, lines=tuple(shuffled))).total == totals.total
```

`countries/do/tests/unit/test_routing.py`:

```python
from dataclasses import replace
from decimal import Decimal

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Buyer, DraftInvalidError, Line
from ledger_country_do.channels.ecf_v1.domain.routing import Route, route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import BUYER, consumer_invoice, credit_invoice


def exempt_consumer_invoice(amount: str, buyer: Buyer) -> Route:
    line = Line(name="LIBROS", quantity=Decimal(1), unit_price=Decimal(amount), billing=BillingIndicator.EXEMPT)
    draft = replace(consumer_invoice(), lines=(line,), buyer=buyer)
    return route(draft, compute_totals(draft))


def test_a_consumer_invoice_below_the_threshold_goes_as_an_rfce() -> None:
    draft = consumer_invoice()
    assert route(draft, compute_totals(draft)) is Route.RFCE
    assert exempt_consumer_invoice("249999.99", Buyer()) is Route.RFCE


def test_from_the_threshold_it_goes_in_full() -> None:
    draft = consumer_invoice(large=True)
    assert route(draft, compute_totals(draft)) is Route.RECEPTION
    assert exempt_consumer_invoice("250000.00", BUYER) is Route.RECEPTION


def test_a_consumer_invoice_from_the_threshold_names_its_buyer() -> None:
    with pytest.raises(DraftInvalidError, match="^buyer "):
        exempt_consumer_invoice("250000.00", Buyer(name="SOMEONE"))


def test_every_other_type_goes_in_full() -> None:
    draft = credit_invoice()
    assert route(draft, compute_totals(draft)) is Route.RECEPTION
```

`countries/do/testing/tests/test_samples.py`:

```python
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer
from ledger_country_do_testing.samples import BUYER, ISSUER, consumer_invoice, credit_invoice


def test_samples_take_what_a_test_changes() -> None:
    other = Buyer(rnc="131880738", name="OTRA EMPRESA SRL")
    assert credit_invoice(7, buyer=other).buyer == other
    assert str(credit_invoice(7).encf) == "E310000000007"
    assert consumer_invoice(large=True).buyer == BUYER
    assert consumer_invoice(buyer=other, issuer=ISSUER).buyer == other
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries/do/tests/unit/test_encf.py countries/do/tests/unit/test_drafts.py countries/do/tests/unit/test_totals.py countries/do/tests/unit/test_routing.py countries/do/testing/tests/test_samples.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.domain.encf'`.

- [ ] **Step 4: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/encf.py`:

```python
"""The e-NCF, an e-CF's number: ``E``, the two-digit e-CF type, and a sequence of ten digits (FMT, IdDoc)."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final, override

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

SEQUENCE_DIGITS: Final = 10
MAX_SEQUENCE: Final = 10**SEQUENCE_DIGITS - 1
_PATTERN: Final = re.compile(r"E([0-9]{2})([0-9]{10})")


class EcfType(StrEnum):
    """The ten e-CF types (FMT; regime ``do``'s document types)."""

    CREDIT_INVOICE = "31"
    CONSUMER_INVOICE = "32"
    DEBIT_NOTE = "33"
    CREDIT_NOTE = "34"
    PURCHASES = "41"
    MINOR_EXPENSES = "43"
    SPECIAL_REGIMES = "44"
    GOVERNMENT = "45"
    EXPORTS = "46"
    FOREIGN_PAYMENTS = "47"


_CODES: Final = frozenset(ecf_type.value for ecf_type in EcfType)


class InvalidEncfError(EcfInvalidError):
    """Not an e-NCF; the text is not repeated."""

    code: ClassVar[str] = "do_ecf_encf_invalid"

    def __init__(self) -> None:
        super().__init__("an e-NCF is E, a two-digit e-CF type and a sequence from 1 to 9999999999 in ten digits")


@final
@dataclass(frozen=True, slots=True)
class Encf:
    """An e-NCF."""

    ecf_type: EcfType
    sequence: int

    def __post_init__(self) -> None:
        if not 1 <= self.sequence <= MAX_SEQUENCE:
            raise InvalidEncfError

    @classmethod
    def parse(cls, text: str) -> Encf:
        """The e-NCF ``text`` writes; ``InvalidEncfError`` if it writes none."""
        found = _PATTERN.fullmatch(text)
        if found is None or found.group(1) not in _CODES:
            raise InvalidEncfError
        return cls(EcfType(found.group(1)), int(found.group(2)))

    @override
    def __str__(self) -> str:
        return f"E{self.ecf_type.value}{self.sequence:0{SEQUENCE_DIGITS}d}"
```

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/notation.py`:

```python
"""How DGII writes dates, moments and numbers (FMT): ``dd-MM-yyyy``, ``dd-MM-yyyy HH:mm:ss``, two decimals.

Moments are written in the Dominican Republic's time, UTC-4 all year (the tz database's America/Santo_Domingo
keeps -04:00 since 2000-12-03). A fixed offset keeps the domain free of a time-zone database, which Windows
lacks without ``tzdata``.
"""

from datetime import timedelta, timezone
from typing import Final
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_country_do.regime.rounding import round_amount

SANTO_DOMINGO: Final = timezone(timedelta(hours=-4), "AST")
_DATE: Final = "%d-%m-%Y"
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"


def dgii_date(value: date) -> str:
    """``dd-MM-yyyy``."""
    return value.strftime(_DATE)


def dgii_moment(value: datetime) -> str:
    """``dd-MM-yyyy HH:mm:ss`` in the Dominican Republic's time (``value`` is timezone-aware)."""
    return value.astimezone(SANTO_DOMINGO).strftime(_MOMENT)


def local_date(value: datetime) -> date:
    """The Dominican Republic's date at ``value``."""
    return value.astimezone(SANTO_DOMINGO).date()


def dgii_amount(value: Decimal) -> str:
    """An amount, rounded half up, with exactly two decimals."""
    return format(round_amount(value), "f")


def dgii_number(value: Decimal) -> str:
    """A quantity or a unit price as it is, never in exponent form."""
    return format(value, "f")


def dgii_percent(value: Decimal) -> str:
    """An ITBIS rate as DGII's schemas want it: an integer (``18``, ``16``, ``0``)."""
    return format(value.normalize(), "f")
```

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/drafts.py`:

```python
"""What an e-CF of the spike says before its XML is written: issuer, buyer, lines, payment (FMT §A-§C).

The spike drafts types 31 (crédito fiscal) and 32 (consumo); the other eight types are v2-M3's. Every limit
here is one of DGII's e-CF schemas (lengths, decimals, line counts) or of DGII's rules the schemas do not say
(a type 31 names its buyer; dated ranges). An error names the field and the rule, never the value.
"""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping
lazy from datetime import date
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.regime.tax_ids import normalize, scheme_of
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf

MAX_LINES: Final = 1000
"""The e-CF 31 and 32 schemas: ``Item [1..1000]`` (F7)."""
_LEGAL_NAME: Final = 150
_ADDRESS: Final = 100
_ITEM_NAME: Final = 80
_QUANTITY_PLACES: Final = 2
_UNIT_PRICE_PLACES: Final = 4


class BillingIndicator(IntEnum):
    """FMT's ``IndicadorFacturacion``: how a line is taxed."""

    NOT_BILLABLE = 0
    ITBIS_1 = 1
    """ITBIS at the standard rate (18 %)."""
    ITBIS_2 = 2
    """ITBIS at the reduced rate (16 %)."""
    ITBIS_3 = 3
    """ITBIS at 0 % (exports)."""
    EXEMPT = 4


TAXED: Final = (BillingIndicator.ITBIS_1, BillingIndicator.ITBIS_2, BillingIndicator.ITBIS_3)
CATEGORY_OF: Final[Mapping[BillingIndicator, str]] = MappingProxyType(
    {
        BillingIndicator.ITBIS_1: "do.itbis.standard",
        BillingIndicator.ITBIS_2: "do.itbis.reduced",
        BillingIndicator.ITBIS_3: "do.itbis.zero",
        BillingIndicator.EXEMPT: "do.itbis.exempt",
    }
)
"""Each billing indicator's tax category in regime ``do``."""


class GoodsOrService(IntEnum):
    """FMT's ``IndicadorBienoServicio``."""

    GOODS = 1
    SERVICE = 2


class IncomeType(StrEnum):
    """FMT's ``TipoIngresos``."""

    OPERATIONS = "01"
    FINANCIAL = "02"
    EXTRAORDINARY = "03"
    LEASES = "04"
    DEPRECIABLE_ASSETS = "05"
    OTHER = "06"


class PaymentType(IntEnum):
    """FMT's ``TipoPago``: 1 contado, 2 crédito, 3 gratuito."""

    CASH = 1
    CREDIT = 2
    FREE = 3


SPIKE_TYPES: Final = frozenset({EcfType.CREDIT_INVOICE, EcfType.CONSUMER_INVOICE})
UNDATED_TYPES: Final = frozenset({EcfType.CONSUMER_INVOICE, EcfType.CREDIT_NOTE})
"""DGII dates every range it authorizes except those of types 32 and 34 (v1 M4 Decision 13)."""


class DraftInvalidError(EcfInvalidError):
    """A draft breaks a rule of DGII's; ``field`` names where, the message the rule."""

    code: ClassVar[str] = "do_ecf_draft_invalid"

    def __init__(self, field_name: str, requirement: str) -> None:
        self.field: str = field_name
        super().__init__(f"{field_name} {requirement}")


class UnsupportedTypeError(EcfInvalidError):
    """A type the spike does not draft."""

    code: ClassVar[str] = "do_ecf_type_unsupported"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"the spike drafts e-CF types 31 and 32, not e-CF type {ecf_type.value}")


def _text(field_name: str, value: str, limit: int) -> None:
    if not value.strip() or len(value) > limit:
        raise DraftInvalidError(field_name, f"holds 1 to {limit} characters, not only spaces")


def _tax_id(field_name: str, value: str) -> None:
    if scheme_of(value) is None or normalize(value) != value:
        raise DraftInvalidError(field_name, "is an RNC (9 digits) or a cédula (11 digits), digits only")


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _decimal(field_name: str, value: Decimal, *, places: int, positive: bool) -> None:
    if not value.is_finite() or value < 0 or (positive and value == 0) or _places(value) > places:
        sign = "greater than zero" if positive else "zero or more"
        raise DraftInvalidError(field_name, f"is a number {sign} with at most {places} decimals")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Issuer:
    """The company issuing the e-CF."""

    rnc: str
    legal_name: str
    address: str

    def __post_init__(self) -> None:
        _tax_id("issuer.rnc", self.rnc)
        _text("issuer.legal_name", self.legal_name, _LEGAL_NAME)
        _text("issuer.address", self.address, _ADDRESS)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Buyer:
    """The buyer: an RNC or cédula and a name, either, or neither (an anonymous consumer)."""

    rnc: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.rnc is not None:
            _tax_id("buyer.rnc", self.rnc)
        if self.name is not None:
            _text("buyer.name", self.name, _LEGAL_NAME)

    @property
    def anonymous(self) -> bool:
        """Whether the buyer is neither identified nor named."""
        return self.rnc is None and self.name is None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """A detail line: quantity x unit price, billed under one indicator."""

    name: str
    quantity: Decimal
    unit_price: Decimal
    billing: BillingIndicator
    kind: GoodsOrService = GoodsOrService.GOODS

    def __post_init__(self) -> None:
        _text("line.name", self.name, _ITEM_NAME)
        _decimal("line.quantity", self.quantity, places=_QUANTITY_PLACES, positive=True)
        _decimal("line.unit_price", self.unit_price, places=_UNIT_PRICE_PLACES, positive=False)
        if self.billing is BillingIndicator.NOT_BILLABLE:
            raise DraftInvalidError("line.billing", "is taxed or exempt (the spike writes no non-billable lines)")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfDraft:
    """An e-CF of type 31 or 32, numbered, before its XML is written."""

    encf: Encf
    issued_on: date
    issuer: Issuer
    lines: tuple[Line, ...]
    buyer: Buyer = field(default_factory=Buyer)
    sequence_expires_on: date | None = None
    income: IncomeType = IncomeType.OPERATIONS
    payment: PaymentType = PaymentType.CASH

    def __post_init__(self) -> None:
        if self.ecf_type not in SPIKE_TYPES:
            raise UnsupportedTypeError(self.ecf_type)
        if not 1 <= len(self.lines) <= MAX_LINES:
            raise DraftInvalidError("lines", f"holds 1 to {MAX_LINES} lines")
        if (self.ecf_type not in UNDATED_TYPES) != (self.sequence_expires_on is not None):
            raise DraftInvalidError(
                "sequence_expires_on", "is given exactly for types whose ranges DGII dates (all but 32 and 34)"
            )
        if self.ecf_type is EcfType.CREDIT_INVOICE and (self.buyer.rnc is None or self.buyer.name is None):
            raise DraftInvalidError("buyer", "names its RNC or cédula and its legal name on a type 31")

    @property
    def ecf_type(self) -> EcfType:
        """The draft's e-CF type, the e-NCF's."""
        return self.encf.ecf_type
```

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/totals.py`:

```python
"""An e-CF's totals, computed as DGII computes them (FMT, Totales; IT §13).

A line's amount (``MontoItem``) is quantity x unit price, rounded half up to two places. Each ITBIS rate's base
(``MontoGravadoI1``..``I3``) is the sum of its lines' amounts, and its ITBIS (``TotalITBIS1``..``3``) the base
times the rate in force on the issue date, rounded. The total (``MontoTotal``) is the taxed bases, the exempt
amount and the ITBIS. Discounts, surcharges, withholdings and additional taxes are v2-M3's.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import final

from ledger_country_do.channels.ecf_v1.domain.drafts import CATEGORY_OF, TAXED, BillingIndicator
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.definitions import RegimeDataError
from ledger_country_do.regime.rounding import round_amount
lazy from datetime import date

lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.regime.definitions import RegimeDef

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)


@final
@dataclass(frozen=True, slots=True)
class RateGroup:
    """The lines taxed at one ITBIS rate: their base, the rate, the ITBIS."""

    indicator: BillingIndicator
    base: Decimal
    percent: Decimal
    itbis: Decimal


@final
@dataclass(frozen=True, slots=True)
class Totals:
    """A document's amounts: each line's, each rate's, the exempt amount, the total."""

    line_amounts: tuple[Decimal, ...]
    groups: tuple[RateGroup, ...]
    """The ITBIS rates the document uses, in DGII's order (1, 2, 3)."""
    exempt: Decimal | None
    total: Decimal

    def group(self, indicator: BillingIndicator) -> RateGroup | None:
        """The group of ``indicator``; ``None`` if no line is taxed at it."""
        return next((group for group in self.groups if group.indicator is indicator), None)

    @property
    def taxed_total(self) -> Decimal | None:
        """``MontoGravadoTotal``: the taxed bases; ``None`` without taxed lines."""
        return sum((group.base for group in self.groups), _ZERO) if self.groups else None

    @property
    def itbis_total(self) -> Decimal | None:
        """``TotalITBIS``; ``None`` without taxed lines."""
        return sum((group.itbis for group in self.groups), _ZERO) if self.groups else None


def _percent(regime: RegimeDef, indicator: BillingIndicator, day: date) -> Decimal:
    key = CATEGORY_OF[indicator]
    rate = regime.category(key).rate_at(day)
    if rate is None:
        raise RegimeDataError(f"{key} has no rate, yet lines are taxed at it")
    return rate


def compute_totals(draft: EcfDraft, regime: RegimeDef = REGIME) -> Totals:
    """``draft``'s totals under ``regime``'s rates in force on its issue date."""
    amounts = tuple(round_amount(line.quantity * line.unit_price) for line in draft.lines)
    groups: list[RateGroup] = []
    for indicator in TAXED:
        billed = [amount for line, amount in zip(draft.lines, amounts, strict=True) if line.billing is indicator]
        if billed:
            base = sum(billed, _ZERO)
            percent = _percent(regime, indicator, draft.issued_on)
            groups.append(RateGroup(indicator, base, percent, round_amount(base * percent / _HUNDRED)))
    exempt_lines = [
        amount for line, amount in zip(draft.lines, amounts, strict=True) if line.billing is BillingIndicator.EXEMPT
    ]
    exempt = sum(exempt_lines, _ZERO) if exempt_lines else None
    total = sum((group.base + group.itbis for group in groups), _ZERO) + (exempt or _ZERO)
    return Totals(amounts, tuple(groups), exempt, total)
```

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/routing.py`:

```python
"""Where a document goes: DGII's reception (track id, then polling) or the RFCE service (synchronous).

DT-DGII: a type-32 invoice whose total is below the dated ``rfce_threshold`` (DOP 250,000.00) is sent as a
summary (RFCE) to ``fc.dgii.gov.do``, and DGII's reception refuses it in full; from the threshold it goes in
full through reception and, as every type 32 of that size, names its buyer (FMT, Comprador).
"""

from enum import StrEnum

from ledger_country_do.channels.ecf_v1.domain.drafts import DraftInvalidError
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.parameters import RFCE_THRESHOLD
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_country_do.regime.definitions import RegimeDef


class Route(StrEnum):
    """Which DGII service receives a document."""

    RECEPTION = "reception"
    RFCE = "rfce"


def route(draft: EcfDraft, totals: Totals, regime: RegimeDef = REGIME) -> Route:
    """``draft``'s route (see the module); ``DraftInvalidError`` for a large type 32 without its buyer's id."""
    if draft.ecf_type is not EcfType.CONSUMER_INVOICE:
        return Route.RECEPTION
    if totals.total < regime.parameter(RFCE_THRESHOLD, draft.issued_on):
        return Route.RFCE
    if draft.buyer.rnc is None:
        raise DraftInvalidError("buyer", "names its RNC or cédula on a type 32 of DOP 250,000 or more")
    return Route.RECEPTION
```

`countries/do/testing/src/ledger_country_do_testing/samples.py`:

```python
"""Sample drafts for tests, the fake DGII's dry runs and the spike CLI.

The credit invoice's numbers are worked out in ``test_totals``: lines of 3,000.00 at 18 %, 850.50 at 16 % and
361.00 exempt, ITBIS 676.08, total 4,887.58. A small consumer invoice (1,180.00) goes as an RFCE; a large one
(295,000.00) goes in full and names its buyer. TesteCF's ranges expire on 2028-12-31 ([06] quick reference).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BillingIndicator,
    Buyer,
    EcfDraft,
    GoodsOrService,
    Issuer,
    Line,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO

ISSUER: Final = Issuer(rnc="101850043", legal_name="FERRETERIA DUARTE SRL", address="AV. DUARTE 100, SANTO DOMINGO")
BUYER: Final = Buyer(rnc="131098193", name="CONSTRUCTORA DEL CARIBE SRL")
ISSUED_ON: Final = date(2026, 10, 13)
SIGNED_AT: Final = datetime(2026, 10, 13, 9, 30, tzinfo=SANTO_DOMINGO)
SEQUENCE_EXPIRES_ON: Final = date(2028, 12, 31)
CREDIT_LINES: Final = (
    Line(name="CEMENTO GRIS 42.5 KG", quantity=Decimal(2), unit_price=Decimal("1500.00"), billing=BillingIndicator.ITBIS_1),
    Line(name="CAFE MOLIDO 1 LB", quantity=Decimal(1), unit_price=Decimal("850.50"), billing=BillingIndicator.ITBIS_2),
    Line(
        name="LIBRO DE TEXTO",
        quantity=Decimal(3),
        unit_price=Decimal("120.3333"),
        billing=BillingIndicator.EXEMPT,
        kind=GoodsOrService.GOODS,
    ),
)


def credit_invoice(sequence: int = 1, *, issuer: Issuer = ISSUER, buyer: Buyer = BUYER) -> EcfDraft:
    """A type 31 of three lines (18 %, 16 %, exempt), numbered ``sequence``."""
    return EcfDraft(
        encf=Encf(EcfType.CREDIT_INVOICE, sequence),
        issued_on=ISSUED_ON,
        issuer=issuer,
        lines=CREDIT_LINES,
        buyer=buyer,
        sequence_expires_on=SEQUENCE_EXPIRES_ON,
    )


def consumer_invoice(
    sequence: int = 1, *, large: bool = False, buyer: Buyer | None = None, issuer: Issuer = ISSUER
) -> EcfDraft:
    """A type 32 of one line at 18 %: 1,180.00 (an RFCE, anonymous by default) or, ``large``, 295,000.00."""
    price = Decimal("250000.00") if large else Decimal("1000.00")
    line = Line(name="VARILLA 3/8", quantity=Decimal(1), unit_price=price, billing=BillingIndicator.ITBIS_1)
    chosen = buyer if buyer is not None else (BUYER if large else Buyer())
    return EcfDraft(
        encf=Encf(EcfType.CONSUMER_INVOICE, sequence), issued_on=ISSUED_ON, issuer=issuer, lines=(line,), buyer=chosen
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock countries
git commit -m "feat(do): e-CF domain for types 31 and 32: e-NCF, drafts, DGII's totals, RFCE routing; samples

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: DGII's XML for types 31 and 32 and the RFCE, golden and schema-valid

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/{writer, ecf, rfce}.py`
- Create (generated by Step 4, read and reviewed, committed): `countries/do/tests/contract/golden/{ecf-31, ecf-32, ecf-32-anonymous, rfce-32}.xml`
- Test: `countries/do/tests/unit/test_ecf_xml.py`, `countries/do/tests/contract/test_golden.py`

**Interfaces:**
- Consumes: `adapters.xml.names.element`, `adapters.xml.schemas.{checked, violations, corrected}`, `adapters.xsd.published.Format` (Task 3); `domain.{drafts, totals, notation, encf}` (Task 4); `samples` (Task 4).
- Produces: `adapters.xml.writer.Node` (`root(format, name)`, `child(name)`, `text(name, value)`, `amount(name, value)`, `number(name, value)`, `document()`). `adapters.xml.ecf`: `VERSION`, `FORMAT_OF: Mapping[EcfType, Format]`, `write_totals(header, totals, *, rates)`, `render(draft, totals, *, signed_at) -> bytes`. `adapters.xml.rfce`: `RfceTypeError` (`do_ecf_rfce_type`), `render(draft, totals, *, security_code) -> bytes`.

- [ ] **Step 1: Write the failing tests**

`countries/do/tests/unit/test_ecf_xml.py`:

```python
from dataclasses import replace
from decimal import Decimal

import pytest
import xmlschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected, violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Buyer, Line
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice

D = Decimal


def test_the_credit_invoice_carries_its_range_s_expiry_and_its_rates() -> None:
    draft = credit_invoice()
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    found = texts(
        document,
        "Encabezado/IdDoc/eNCF",
        "Encabezado/IdDoc/FechaVencimientoSecuencia",
        "Encabezado/IdDoc/IndicadorMontoGravado",
        "Encabezado/Totales/ITBIS1",
        "Encabezado/Totales/ITBIS3",
        "Encabezado/Totales/MontoTotal",
        "FechaHoraFirma",
    )
    assert found == ("E310000000001", "31-12-2028", "0", "18", None, "4887.58", "13-10-2026 09:30:00")


def test_a_consumer_invoice_has_no_expiry_and_an_anonymous_buyer_an_empty_comprador() -> None:
    draft = consumer_invoice()
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert b"FechaVencimientoSecuencia" not in document
    assert b"<Comprador />" in document  # F4: the schema requires the element, every child optional


def test_a_document_of_exempt_lines_only_writes_no_taxed_totals() -> None:
    exempt = Line(name="LIBRO", quantity=D(1), unit_price=D("10.00"), billing=BillingIndicator.EXEMPT)
    draft = replace(consumer_invoice(), lines=(exempt,))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert b"MontoGravadoTotal" not in document
    assert b"IndicadorMontoGravado" not in document
    assert violations(Format.ECF_32, document, unsigned=True) == ()


def test_the_rfce_summarizes_a_consumer_invoice_with_its_security_code() -> None:
    draft = consumer_invoice()
    summary = rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    found = texts(summary, "Encabezado/IdDoc/eNCF", "Encabezado/Totales/MontoTotal", "Encabezado/CodigoSeguridadeCF")
    assert found == ("E320000000001", "1180.00", "dcp79q")
    assert texts(summary, "Encabezado/Totales/ITBIS1", "Encabezado/Totales/TotalITBIS1") == (None, "180.00")
    assert violations(Format.RFCE, summary, unsigned=True) == ()


def test_only_a_consumer_invoice_is_summarized() -> None:
    draft = credit_invoice()
    with pytest.raises(rfce.RfceTypeError):
        rfce.render(draft, compute_totals(draft), security_code="dcp79q")


_NAMES = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ", min_size=1, max_size=80).filter(str.strip)
_LINE = st.builds(
    Line,
    name=_NAMES,
    quantity=st.decimals(min_value=D("0.01"), max_value=D(99999), places=2),
    unit_price=st.decimals(min_value=D(0), max_value=D(999999), places=4),
    billing=st.sampled_from([BillingIndicator.ITBIS_1, BillingIndicator.ITBIS_2, BillingIndicator.ITBIS_3, BillingIndicator.EXEMPT]),
)
XSD_32 = xmlschema.XMLSchema10(corrected(Format.ECF_32).decode("utf-8"))


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(_LINE, min_size=1, max_size=8), st.booleans())
def test_every_drafted_consumer_invoice_is_valid_for_both_validators(lines: list[Line], named: bool) -> None:
    buyer = Buyer(rnc="131098193", name="CLIENTE") if named else Buyer()
    draft = replace(consumer_invoice(large=named, buyer=buyer), lines=tuple(lines))
    document = ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)
    assert violations(Format.ECF_32, document, unsigned=True) == ()
    signed_shape = document.replace(b"</ECF>", b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></ECF>')
    assert XSD_32.is_valid(signed_shape.decode("utf-8"))
```

`countries/do/tests/contract/test_golden.py`:

```python
from pathlib import Path
from typing import Final

import pytest
import xmlschema

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected, violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice

GOLDEN: Final = Path(__file__).with_name("golden")
SIGNATURE: Final = b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/>'


def written(name: str) -> bytes:
    match name:
        case "ecf-31":
            draft = credit_invoice()
        case "ecf-32":
            draft = consumer_invoice(2, large=True)
        case "ecf-32-anonymous":
            draft = consumer_invoice(1)
        case _:
            draft = consumer_invoice(1)
            return rfce.render(draft, compute_totals(draft), security_code="dcp79q")
    return ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT)


CASES: Final = {"ecf-31": Format.ECF_31, "ecf-32": Format.ECF_32, "ecf-32-anonymous": Format.ECF_32, "rfce-32": Format.RFCE}


@pytest.mark.parametrize("name", list(CASES))
def test_the_builders_write_the_golden_documents_byte_for_byte(name: str) -> None:
    assert written(name) == (GOLDEN / f"{name}.xml").read_bytes()


@pytest.mark.parametrize("name", list(CASES))
def test_every_golden_document_is_valid_for_both_validators(name: str) -> None:
    document = (GOLDEN / f"{name}.xml").read_bytes()
    root = b"</RFCE>" if CASES[name] is Format.RFCE else b"</ECF>"
    assert violations(CASES[name], document, unsigned=True) == ()
    signed_shape = document.replace(root, SIGNATURE + root)
    xmlschema.XMLSchema10(corrected(CASES[name]).decode("utf-8")).validate(signed_shape.decode("utf-8"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest countries/do/tests/unit/test_ecf_xml.py countries/do/tests/contract/test_golden.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'ecf'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/writer.py`:

```python
"""Writing DGII's XML: one element after another in the schema's order, never an empty one by accident.

DT-EMI: no XML may hold an empty tag, and an unused tag is left out; so a field without a value is never
written (the one exception, an anonymous buyer's ``Comprador``, is the builder's explicit choice: F4).
Elements are named in lowercase and written as their schema declares them (``names.element``). The tree is the
standard library's ``ElementTree``, used here to build and serialize only (never to parse): its ``Element`` is
public and typed.
"""

from io import BytesIO
from typing import final
from xml.etree.ElementTree import Element, ElementTree, SubElement
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.adapters.xml.names import element
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_amount, dgii_number
lazy from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format


@final
class Node:
    """An element being written: its children are added in the order the schema wants them."""

    __slots__ = ("element", "format")

    def __init__(self, format_: Format, written: Element) -> None:
        self.format: Format = format_
        self.element: Element = written

    @classmethod
    def root(cls, format_: Format, name: str) -> Node:
        """A document's root element, of ``format_``'s schema."""
        return cls(format_, Element(element(format_, name)))

    def child(self, name: str) -> Node:
        """A new child element, last."""
        return Node(self.format, SubElement(self.element, element(self.format, name)))

    def text(self, name: str, value: str | None) -> None:
        """A child with ``value`` as its text; nothing when there is no value."""
        if value is not None:
            SubElement(self.element, element(self.format, name)).text = value

    def amount(self, name: str, value: Decimal | None) -> None:
        """A child with an amount (two decimals); nothing when there is none."""
        self.text(name, None if value is None else dgii_amount(value))

    def number(self, name: str, value: Decimal | int | None) -> None:
        """A child with a number written as it is; nothing when there is none."""
        if value is not None:
            self.text(name, str(value) if isinstance(value, int) else dgii_number(value))

    def document(self) -> bytes:
        """The whole document, UTF-8, with its XML declaration."""
        written = BytesIO()
        ElementTree(self.element).write(written, encoding="utf-8", xml_declaration=True)
        return written.getvalue()
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/ecf.py`:

```python
"""The e-CF of types 31 and 32, in the order of DGII's schemas (FMT, sections A to C, then the signing moment).

What the spike writes: identification (type, e-NCF, the range's expiry for dated types, whether prices include
ITBIS, income and payment type), issuer, buyer, totals (bases, rates and ITBIS per rate, exempt amount, total),
lines (number, billing indicator, name, goods or service, quantity, unit price, amount) and
``FechaHoraFirma``. The signature is added by the signer, after ``FechaHoraFirma``.
"""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping
lazy from datetime import datetime
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.drafts import TAXED, BillingIndicator
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_date, dgii_moment, dgii_percent
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, EcfDraft, Line
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals

VERSION: Final = "1.0"
FORMAT_OF: Final[Mapping[EcfType, Format]] = MappingProxyType(
    {
        EcfType.CREDIT_INVOICE: Format.ECF_31,
        EcfType.CONSUMER_INVOICE: Format.ECF_32,
        EcfType.DEBIT_NOTE: Format.ECF_33,
        EcfType.CREDIT_NOTE: Format.ECF_34,
        EcfType.PURCHASES: Format.ECF_41,
        EcfType.MINOR_EXPENSES: Format.ECF_43,
        EcfType.SPECIAL_REGIMES: Format.ECF_44,
        EcfType.GOVERNMENT: Format.ECF_45,
        EcfType.EXPORTS: Format.ECF_46,
        EcfType.FOREIGN_PAYMENTS: Format.ECF_47,
    }
)
"""The schema of each e-CF type."""
_RATE_TAGS: Final = {BillingIndicator.ITBIS_1: "1", BillingIndicator.ITBIS_2: "2", BillingIndicator.ITBIS_3: "3"}
_PRICES_EXCLUDE_ITBIS: Final = "0"


def _identification(header: Node, draft: EcfDraft) -> None:
    ident = header.child("iddoc")
    ident.text("tipoecf", draft.ecf_type.value)
    ident.text("encf", str(draft.encf))
    expires = draft.sequence_expires_on
    ident.text("fechavencimientosecuencia", None if expires is None else dgii_date(expires))
    taxed = any(line.billing in TAXED for line in draft.lines)
    ident.text("indicadormontogravado", _PRICES_EXCLUDE_ITBIS if taxed else None)
    ident.text("tipoingresos", draft.income.value)
    ident.number("tipopago", draft.payment.value)


def _issuer(header: Node, draft: EcfDraft) -> None:
    issuing = header.child("emisor")
    issuing.text("rncemisor", draft.issuer.rnc)
    issuing.text("razonsocialemisor", draft.issuer.legal_name)
    issuing.text("direccionemisor", draft.issuer.address)
    issuing.text("fechaemision", dgii_date(draft.issued_on))


def write_buyer(header: Node, buyer: Buyer) -> None:
    """``Comprador``: written always (the schemas of 31, 32 and the RFCE require it), empty when anonymous."""
    buying = header.child("comprador")
    buying.text("rnccomprador", buyer.rnc)
    buying.text("razonsocialcomprador", buyer.name)


def write_totals(header: Node, totals: Totals, *, rates: bool) -> None:
    """``Totales``: bases, (with ``rates``) the rates, ITBIS per rate, exempt amount and total."""
    summed = header.child("totales")
    summed.amount("montogravadototal", totals.taxed_total)
    for indicator in TAXED:
        group = totals.group(indicator)
        summed.amount(f"montogravadoi{_RATE_TAGS[indicator]}", None if group is None else group.base)
    summed.amount("montoexento", totals.exempt)
    if rates:
        for indicator in TAXED:
            group = totals.group(indicator)
            summed.text(f"itbis{_RATE_TAGS[indicator]}", None if group is None else dgii_percent(group.percent))
    summed.amount("totalitbis", totals.itbis_total)
    for indicator in TAXED:
        group = totals.group(indicator)
        summed.amount(f"totalitbis{_RATE_TAGS[indicator]}", None if group is None else group.itbis)
    summed.amount("montototal", totals.total)


def _line(items: Node, number: int, line: Line, amount: Decimal) -> None:
    item = items.child("item")
    item.number("numerolinea", number)
    item.number("indicadorfacturacion", line.billing.value)
    item.text("nombreitem", line.name)
    item.number("indicadorbienoservicio", line.kind.value)
    item.number("cantidaditem", line.quantity)
    item.number("preciounitarioitem", line.unit_price)
    item.amount("montoitem", amount)


def render(draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
    """``draft`` as its unsigned e-CF, ``FechaHoraFirma`` set to ``signed_at``."""
    root = Node.root(FORMAT_OF[draft.ecf_type], "ecf")
    header = root.child("encabezado")
    header.text("version", VERSION)
    _identification(header, draft)
    _issuer(header, draft)
    write_buyer(header, draft.buyer)
    write_totals(header, totals, rates=True)
    items = root.child("detallesitems")
    for number, (line, amount) in enumerate(zip(draft.lines, totals.line_amounts, strict=True), start=1):
        _line(items, number, line, amount)
    root.text("fechahorafirma", dgii_moment(signed_at))
    return root.document()
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/rfce.py`:

```python
"""The summary of a consumer invoice under DOP 250,000 (RFCE), which DGII receives instead of the e-CF.

*Formato Resumen Factura Consumo Electrónica v1.0*: the invoice's identification, issuer, buyer and totals (no
rates), and the security code of the full, signed e-CF (``CodigoSeguridadeCF``), which the company keeps (IT
§9.1). The summary is signed in its turn.
"""

from typing import ClassVar, Final

from ledger_country_do.channels.ecf_v1.adapters.xml.ecf import write_buyer, write_totals
from ledger_country_do.channels.ecf_v1.adapters.xml.writer import Node
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_date
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals

VERSION: Final = "1.0"


class RfceTypeError(EcfError):
    """Only a type 32 has a summary: asking for another's is a defect of the caller."""

    code: ClassVar[str] = "do_ecf_rfce_type"

    def __init__(self, ecf_type: EcfType) -> None:
        super().__init__(f"an RFCE summarizes a type 32, not a type {ecf_type.value}")


def render(draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
    """The unsigned RFCE of ``draft``, a type 32 whose full e-CF was signed with ``security_code``."""
    if draft.ecf_type is not EcfType.CONSUMER_INVOICE:
        raise RfceTypeError(draft.ecf_type)
    root = Node.root(Format.RFCE, "rfce")
    header = root.child("encabezado")
    header.text("version", VERSION)
    ident = header.child("iddoc")
    ident.text("tipoecf", draft.ecf_type.value)
    ident.text("encf", str(draft.encf))
    ident.text("tipoingresos", draft.income.value)
    ident.number("tipopago", draft.payment.value)
    issuing = header.child("emisor")
    issuing.text("rncemisor", draft.issuer.rnc)
    issuing.text("razonsocialemisor", draft.issuer.legal_name)
    issuing.text("fechaemision", dgii_date(draft.issued_on))
    write_buyer(header, draft.buyer)
    write_totals(header, totals, rates=False)
    header.text("codigoseguridadecf", security_code)
    return root.document()
```

- [ ] **Step 4: Write the golden files, then read every one**

```bash
uv run python - <<'EOF'
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("golden_cases", "countries/do/tests/contract/test_golden.py")
assert spec is not None and spec.loader is not None
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)
golden = Path("countries/do/tests/contract/golden")
golden.mkdir(exist_ok=True)
for name in cases.CASES:
    (golden / f"{name}.xml").write_bytes(cases.written(name))
print(sorted(path.name for path in golden.iterdir()))
EOF
```

Expected: `['ecf-31.xml', 'ecf-32-anonymous.xml', 'ecf-32.xml', 'rfce-32.xml']`. Read each before committing: a golden file is trusted because two validators that share no code accept it **and** a reviewer read it against FMT, not because the code wrote it. `ecf-31.xml` must be exactly (one line after the declaration):

```xml
<?xml version='1.0' encoding='utf-8'?>
<ECF><Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF><FechaVencimientoSecuencia>31-12-2028</FechaVencimientoSecuencia><IndicadorMontoGravado>0</IndicadorMontoGravado><TipoIngresos>01</TipoIngresos><TipoPago>1</TipoPago></IdDoc><Emisor><RNCEmisor>101850043</RNCEmisor><RazonSocialEmisor>FERRETERIA DUARTE SRL</RazonSocialEmisor><DireccionEmisor>AV. DUARTE 100, SANTO DOMINGO</DireccionEmisor><FechaEmision>13-10-2026</FechaEmision></Emisor><Comprador><RNCComprador>131098193</RNCComprador><RazonSocialComprador>CONSTRUCTORA DEL CARIBE SRL</RazonSocialComprador></Comprador><Totales><MontoGravadoTotal>3850.50</MontoGravadoTotal><MontoGravadoI1>3000.00</MontoGravadoI1><MontoGravadoI2>850.50</MontoGravadoI2><MontoExento>361.00</MontoExento><ITBIS1>18</ITBIS1><ITBIS2>16</ITBIS2><TotalITBIS>676.08</TotalITBIS><TotalITBIS1>540.00</TotalITBIS1><TotalITBIS2>136.08</TotalITBIS2><MontoTotal>4887.58</MontoTotal></Totales></Encabezado><DetallesItems><Item><NumeroLinea>1</NumeroLinea><IndicadorFacturacion>1</IndicadorFacturacion><NombreItem>CEMENTO GRIS 42.5 KG</NombreItem><IndicadorBienoServicio>1</IndicadorBienoServicio><CantidadItem>2</CantidadItem><PrecioUnitarioItem>1500.00</PrecioUnitarioItem><MontoItem>3000.00</MontoItem></Item><Item><NumeroLinea>2</NumeroLinea><IndicadorFacturacion>2</IndicadorFacturacion><NombreItem>CAFE MOLIDO 1 LB</NombreItem><IndicadorBienoServicio>1</IndicadorBienoServicio><CantidadItem>1</CantidadItem><PrecioUnitarioItem>850.50</PrecioUnitarioItem><MontoItem>850.50</MontoItem></Item><Item><NumeroLinea>3</NumeroLinea><IndicadorFacturacion>4</IndicadorFacturacion><NombreItem>LIBRO DE TEXTO</NombreItem><IndicadorBienoServicio>1</IndicadorBienoServicio><CantidadItem>3</CantidadItem><PrecioUnitarioItem>120.3333</PrecioUnitarioItem><MontoItem>361.00</MontoItem></Item></DetallesItems><FechaHoraFirma>13-10-2026 09:30:00</FechaHoraFirma></ECF>
```

If the schema refuses a golden document, the builder is wrong, never the golden file: fix the builder in this task and regenerate.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/tests/unit/test_ecf_xml.py countries/do/tests/contract/test_golden.py -q --no-cov`
Expected: PASS (50 generated consumer invoices accepted by libxml2 and xmlschema).

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add countries/do
git commit -m "feat(do): e-CF 31/32 and RFCE builders, golden documents valid for libxml2 and xmlschema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: DGII's signature profile: sign, verify, proven by signxml and xmlsec1; a local signer

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py`
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/{__init__, profile, signer, verifier}.py`
- Create: `countries/do/testing/src/ledger_country_do_testing/{signatures, local_signer, xmlsec}.py`, `countries/do/testing/src/ledger_country_do_testing/xmlsec1/Dockerfile`
- Create: `countries/do/tests/conftest.py`
- Modify: `countries/do/pyproject.toml` (`dependencies = ["cryptography>=50.0.2", "lxml>=6.1.3"]`), `countries/do/testing/pyproject.toml` (adds `"lxml>=6.1.3"`, `"signxml>=5.1.0"`, `"testcontainers>=4.15.0"`)
- Test: `countries/do/tests/unit/test_signing.py`, `countries/do/tests/integration/test_signing_xmlsec1.py`, `countries/do/testing/tests/test_{signatures, local_signer, xmlsec}.py`

**Interfaces:**
- Consumes: `adapters.xml.parsing` (`DSIG`, `SIGNATURE`, `parser`, `require_readable`, `MalformedDocumentError`) (Task 3); `domain.errors` (Task 3); `pki` (Task 1).
- Produces: `application.ports.XmlSigner` (`async sign_document(document) -> bytes`, `async sign_seed(seed) -> bytes`). `adapters.signing.profile`: `C14N`, `RSA_SHA256`, `SHA256`, `ENVELOPED`, `PROFILES` (the accepted `Algorithm` sequences), `signed_info(digest, in_scope=None) -> str`, `signature(digest, value, certificate) -> str`. `adapters.signing.signer`: `SEED_ROOT`, `SEED_CHILDREN`, `DocumentSignedError`, `SeedMalformedError`, `NotAnRsaKeyError`, `sign_enveloped(document, key, certificate, *, seed=False) -> bytes`. `adapters.signing.verifier`: `SignatureInvalidError` (`do_ecf_signature_invalid`), `VerifiedSignature(certificate, signature_value)`, `verify_enveloped(signed) -> VerifiedSignature`. `ledger_country_do_testing.signatures`: `SHAPES: Mapping[str, bytes]`, `OUTSIDE_ROOT`, `verify_with_signxml(signed, *, trusted=None) -> bool`, `sign_with_signxml(document, issued, *, prefixed=False) -> bytes`. `ledger_country_do_testing.local_signer`: `BundleIncompleteError`, `KeyPairSigner(key, certificate)` (implements `XmlSigner`) with `from_issued(issued)`, `from_pkcs12(data, password)`, `certificate`. `ledger_country_do_testing.xmlsec`: `XMLSEC_IMAGE`, `Verdict(verified, output)`, `Xmlsec.started()`, `Xmlsec.verify(signed, *, trusted, untrusted, at)`. `countries/do/tests/conftest.py` fixtures: `authority`, `intermediate`, `issued`, `signer` (session scope).

- [ ] **Step 1: Dependencies**

`countries/do/pyproject.toml`: `dependencies = ["cryptography>=50.0.2", "lxml>=6.1.3"]`. `countries/do/testing/pyproject.toml`: `dependencies = ["cryptography>=50.0.2", "ledger-country-do", "lxml>=6.1.3", "signxml>=5.1.0", "testcontainers>=4.15.0"]`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/tests/conftest.py`:

```python
"""Fixtures of the DO pack's tests: one test hierarchy and one signer per session (RSA keys are slow to make)."""

import pytest

from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Authority, Issued, person


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root()


@pytest.fixture(scope="session")
def intermediate(authority: Authority) -> Authority:
    return authority.intermediate()


@pytest.fixture(scope="session")
def issued(intermediate: Authority) -> Issued:
    return intermediate.issue(person())


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)
```

`countries/do/tests/unit/test_signing.py`:

```python
import base64
import hashlib
import re
from typing import Final
lazy from collections.abc import Callable

import pytest
from cryptography.hazmat.primitives.serialization import Encoding
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import (
    DocumentSignedError,
    NotAnRsaKeyError,
    SeedMalformedError,
    sign_enveloped,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import SignatureInvalidError, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, UnreadableDocumentError
from ledger_country_do_testing.pki import Authority, Issued, ec_key, organization
from ledger_country_do_testing.signatures import OUTSIDE_ROOT, SHAPES, sign_with_signxml, verify_with_signxml

GOLDEN_C14N_INPUT: Final = (
    b'<?xml version="1.0" encoding="utf-8"?>\n<?pi before?>\n<ECF  b="2" a="1&#9;&#10;x"'
    b' xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    b"<Empty/><!-- gone --><T>Pe&#241;a &lt;12&quot;&gt; \r\n&amp;</T>"
    b'<x:P xmlns:x="urn:x" xmlns="urn:d"><Q/></x:P></ECF>'
)
GOLDEN_C14N: Final = (
    b'<?pi before?>\n<ECF xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" a="1&#x9;&#xA;x" b="2"><Empty></Empty>'
    b'<T>Pe\xc3\xb1a &lt;12"&gt; \n&amp;</T><x:P xmlns="urn:d" xmlns:x="urn:x"><Q></Q></x:P></ECF>'
)
"""Inclusive C14N 1.0 without comments of the input above: attributes sorted, their whitespace as references,
CR LF read as LF, empty elements written out, comments dropped, the PI before the root kept with its line break,
namespace declarations first and sorted."""
GOLDEN_SIGNATURE: Final = (
    '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>'
    '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
    '<SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
    '<Reference URI=""><Transforms><Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
    '</Transforms><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
    "<DigestValue>{digest}</DigestValue></Reference></SignedInfo><SignatureValue>{value}</SignatureValue>"
    "<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo></Signature>"
)
"""DGII's shape (*Firmado de e-CF*), exactly, with its three values taken out."""
VALUES: Final = re.compile(r"<(DigestValue|SignatureValue|X509Certificate)>([^<]*)<")
ECF: Final = SHAPES["ecf"]


def sign(issued: Issued, document: bytes, *, seed: bool = False) -> bytes:
    return sign_enveloped(document, issued.key, issued.certificate, seed=seed)


def canonical(document: bytes) -> bytes:
    return etree.tostring(etree.fromstring(document).getroottree(), method="c14n", exclusive=False, with_comments=False)


def test_the_signature_has_exactly_dgii_s_shape(issued: Issued) -> None:
    signed = sign(issued, ECF)
    found = re.search(rb"<Signature .*</Signature>", signed)
    assert found is not None
    element = found.group().decode()
    values = dict(zip(("digest", "value", "certificate"), [m.group(2) for m in VALUES.finditer(element)], strict=True))
    assert element == GOLDEN_SIGNATURE.format(**values)
    assert base64.b64decode(values["digest"]) == hashlib.sha256(canonical(ECF)).digest()
    assert base64.b64decode(values["certificate"]) == issued.certificate.public_bytes(Encoding.DER)
    assert signed.endswith(GOLDEN_SIGNATURE.format(**values).encode() + b"</ECF>")


def test_the_canonical_form_is_c14n_1_0_inclusive_without_comments() -> None:
    assert canonical(GOLDEN_C14N_INPUT) == GOLDEN_C14N


@pytest.mark.parametrize("shape", list(SHAPES))
def test_our_verifier_accepts_our_signatures(issued: Issued, shape: str) -> None:
    signed = sign(issued, SHAPES[shape], seed=shape == "seed")
    verified = verify_enveloped(signed)
    assert verified.certificate == issued.certificate
    assert len(verified.signature_value) > 300


@pytest.mark.parametrize("shape", [shape for shape in SHAPES if shape != OUTSIDE_ROOT])
def test_signxml_verifies_our_signatures(issued: Issued, shape: str) -> None:
    assert verify_with_signxml(sign(issued, SHAPES[shape], seed=shape == "seed"), trusted=[issued.certificate])


@pytest.mark.parametrize("prefixed", [False, True], ids=["default-namespace", "ds-prefix"])
def test_our_verifier_accepts_signxml_s_signatures(issued: Issued, prefixed: bool) -> None:
    signed = sign_with_signxml(ECF, issued, prefixed=prefixed)
    assert verify_enveloped(signed).certificate == issued.certificate


def test_a_tampered_document_fails_both_verifiers(issued: Issued) -> None:
    tampered = sign(issued, ECF).replace(b"101850043", b"101850044")
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(tampered)
    assert not verify_with_signxml(tampered, trusted=[issued.certificate])


def test_whitespace_around_the_signature_is_the_document_s_own(issued: Issued) -> None:
    # The enveloped-signature transform removes the Signature element, not the text around it.
    for document in (b"<a><b/>\n</a>", b"<a>\n</a>"):
        root = etree.fromstring(sign(issued, document))
        signature = root[-1]
        previous = signature.getprevious()
        if previous is None:
            signature.tail, root.text = root.text, None
        else:
            signature.tail, previous.tail = previous.tail, None
        verify_enveloped(etree.tostring(root))


def _swap(pattern: bytes, replacement: bytes) -> Callable[[bytes], bytes]:
    return lambda signed: re.sub(pattern, replacement, signed, count=1, flags=re.DOTALL)


def _ec_certificate() -> bytes:
    issued = Authority.root().issue(organization(), key=ec_key())
    return base64.b64encode(issued.certificate.public_bytes(Encoding.DER))


MUTATIONS: Final[dict[str, tuple[Callable[[bytes], bytes], str]]] = {
    "sha1": (_swap(rb"xmldsig-more#rsa-sha256", b"xmldsig#rsa-sha1"), "not DGII's profile"),
    "reference": (_swap(rb'URI=""', b'URI="#x"'), "not DGII's profile"),
    "two-signatures": (_swap(rb"(<Signature .*</Signature>)", rb"\1\1"), "single signature"),
    "not-last": (_swap(rb"</Signature>", b"</Signature><After/>"), "single signature"),
    "no-value": (_swap(rb"<SignatureValue>.*</SignatureValue>", b""), "incomplete"),
    "no-signed-info": (_swap(rb"<SignedInfo>.*</SignedInfo>", b""), "incomplete"),
    "not-base64": (_swap(rb"<DigestValue>[^<]*<", b"<DigestValue>!!!<"), "not base64"),
    "ec-certificate": (lambda signed: _swap(rb"<X509Certificate>[^<]*<", b"<X509Certificate>" + _ec_certificate() + b"<")(signed), "not RSA"),
    "wrong-value": (_swap(rb"<SignatureValue>[^<]*<", b"<SignatureValue>" + base64.b64encode(bytes(256)) + b"<"), "does not match"),
}


@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_what_breaks_dgii_s_profile_is_refused(issued: Issued, mutation: str) -> None:
    mutate, reason = MUTATIONS[mutation]
    with pytest.raises(SignatureInvalidError, match=reason) as caught:
        verify_enveloped(mutate(sign(issued, ECF)))
    assert caught.value.code == "do_ecf_signature_invalid"
    assert "101850043" not in str(caught.value)


@pytest.mark.parametrize(
    ("document", "seed", "error"),
    [
        (b"<a>", False, UnreadableDocumentError),
        (b'<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>', False, UnreadableDocumentError),
        (b'<!DOCTYPE a SYSTEM "http://169.254.169.254/latest/meta-data/"><a/>', False, UnreadableDocumentError),
        (b"<SemillaModel><fecha/><valor/></SemillaModel>", True, SeedMalformedError),
        (b"<Semilla><valor/><fecha/></Semilla>", True, SeedMalformedError),
        (ECF, True, SeedMalformedError),
    ],
    ids=["unclosed", "entity", "external-dtd", "seed-order", "seed-root", "ecf-as-seed"],
)
def test_what_cannot_be_signed_is_refused_unread(issued: Issued, document: bytes, seed: bool, error: type[Exception]) -> None:
    with pytest.raises(error):
        sign(issued, document, seed=seed)
    with pytest.raises(UnreadableDocumentError):
        verify_enveloped(b"<a>")


def test_a_signed_document_is_not_signed_again(issued: Issued) -> None:
    with pytest.raises(DocumentSignedError):
        sign(issued, sign(issued, ECF))
    with pytest.raises(DocumentSignedError):
        sign(issued, f'<Signature xmlns="{DSIG}"/>'.encode())


def test_only_an_rsa_key_signs(issued: Issued) -> None:
    with pytest.raises(NotAnRsaKeyError):
        sign_enveloped(ECF, ec_key(), issued.certificate)


_NAMES: Final = st.sampled_from(["a", "b", "Item", "eNCF", "x-y", "z.1"])
_TEXT: Final = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc", "Co"), include_characters="\t\n\r"), max_size=12
).filter(lambda text: "￾" not in text and "￿" not in text)
NAMESPACES: Final[tuple[dict[str | None, str], ...]] = ({}, {"xsi": "urn:xsi"}, {None: "urn:d"})


@st.composite
def documents(draw: st.DrawFn) -> bytes:
    """Well-formed documents: nested elements, attributes, any text XML allows, comments, namespaces."""
    root = etree.Element(draw(_NAMES), nsmap=draw(st.sampled_from(NAMESPACES)))
    elements = [root]
    for _ in range(draw(st.integers(0, 8))):
        parent = draw(st.sampled_from(elements))
        namespace = draw(st.sampled_from([None, "urn:n"]))
        tag = draw(_NAMES) if namespace is None else f"{{{namespace}}}{draw(_NAMES)}"
        child = etree.SubElement(parent, tag, {draw(_NAMES): draw(_TEXT)} if draw(st.booleans()) else {})
        child.text = draw(_TEXT)
        if draw(st.booleans()):
            parent.append(etree.Comment(draw(st.sampled_from(["c", " a note "]))))
        elements.append(child)
    root.text = draw(_TEXT)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(documents())
def test_any_well_formed_document_round_trips_through_both_verifiers(issued: Issued, document: bytes) -> None:
    signed = sign(issued, document)
    assert verify_enveloped(signed).certificate == issued.certificate
    assert verify_with_signxml(signed, trusted=[issued.certificate])
```

`countries/do/tests/integration/test_signing_xmlsec1.py`:

```python
from datetime import UTC, datetime
lazy from collections.abc import Iterator

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do_testing.pki import Authority, Issued, pem
from ledger_country_do_testing.signatures import SHAPES
from ledger_country_do_testing.xmlsec import Xmlsec


@pytest.fixture(scope="module")
def xmlsec() -> Iterator[Xmlsec]:
    with Xmlsec.started() as started:
        yield started


@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_shape_we_sign_verifies_in_xmlsec1_with_its_chain(
    xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued, shape: str
) -> None:
    signed = sign_enveloped(SHAPES[shape], issued.key, issued.certificate, seed=shape == "seed")
    verdict = xmlsec.verify(
        signed, trusted=pem(authority.certificate), untrusted=pem(intermediate.certificate), at=datetime.now(UTC)
    )
    assert verdict.verified, verdict.output


def test_xmlsec1_refuses_a_tampered_one(xmlsec: Xmlsec, authority: Authority, intermediate: Authority, issued: Issued) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate).replace(b"101850043", b"101850044")
    verdict = xmlsec.verify(
        signed, trusted=pem(authority.certificate), untrusted=pem(intermediate.certificate), at=datetime.now(UTC)
    )
    assert not verdict.verified
```

`countries/do/testing/tests/test_signatures.py`:

```python
from ledger_country_do_testing.pki import Authority, organization
from ledger_country_do_testing.signatures import SHAPES, sign_with_signxml, verify_with_signxml


def test_signxml_signs_and_verifies_and_trusts_only_whom_it_is_told() -> None:
    issued = Authority.root().issue(organization())
    stranger = Authority.root().issue(organization())
    signed = sign_with_signxml(SHAPES["ecf"], issued)
    assert verify_with_signxml(signed)
    assert verify_with_signxml(signed, trusted=[issued.certificate])
    assert not verify_with_signxml(signed, trusted=[stranger.certificate])
    assert not verify_with_signxml(signed.replace(b"101850043", b"101850044"))
    assert not verify_with_signxml(SHAPES["ecf"])
```

`countries/do/testing/tests/test_local_signer.py`:

```python
import pytest
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do_testing.local_signer import BundleIncompleteError, KeyPairSigner
from ledger_country_do_testing.pki import OPENING_PHRASE, Authority, bundle, person
from ledger_country_do_testing.signatures import SHAPES


async def test_a_bundle_opens_into_a_signer_of_documents_and_seeds() -> None:
    issued = Authority.root().issue(person())
    signer = KeyPairSigner.from_pkcs12(bundle(issued), OPENING_PHRASE.encode())
    assert signer.certificate == issued.certificate
    assert verify_enveloped(await signer.sign_document(SHAPES["ecf"])).certificate == issued.certificate
    assert verify_enveloped(await signer.sign_seed(SHAPES["seed"])).certificate == issued.certificate


def test_a_bundle_without_its_key_signs_nothing() -> None:
    issued = Authority.root().issue(person())
    keyless = pkcs12.serialize_key_and_certificates(
        b"x", None, issued.certificate, None, BestAvailableEncryption(OPENING_PHRASE.encode())
    )
    with pytest.raises(BundleIncompleteError, match="holds no private key and certificate pair"):
        KeyPairSigner.from_pkcs12(keyless, OPENING_PHRASE.encode())


def test_a_wrong_password_opens_nothing_and_is_not_echoed() -> None:
    issued = Authority.root().issue(person())
    with pytest.raises(ValueError, match="password") as caught:
        KeyPairSigner.from_pkcs12(bundle(issued), b"not the password")
    assert "not the password" not in str(caught.value)
```

`countries/do/testing/tests/test_xmlsec.py`:

```python
from datetime import UTC, datetime

from ledger_country_do_testing.pki import Authority, pem
from ledger_country_do_testing.xmlsec import Xmlsec


def test_xmlsec1_refuses_a_document_without_a_signature() -> None:
    root = Authority.root()
    with Xmlsec.started() as xmlsec:
        verdict = xmlsec.verify(b"<a/>", trusted=pem(root.certificate), untrusted=b"", at=datetime.now(UTC))
    assert not verdict.verified
    assert "ignature" in verdict.output
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries -q --no-cov -k "signing or signatures or local_signer or xmlsec"`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.adapters.signing'`.

- [ ] **Step 4: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py` (Tasks 11 and 12 add the other ports):

```python
"""The ports do-ecf-v1's use cases need; adapters implement them (spec §4.5).

``XmlSigner`` becomes fiscal-kit's ``SignerPort`` (spec §8.4) at integration, implemented over the signer
module's gRPC client; in the spike a key held in the test or CLI process implements it.
"""

from abc import ABCMeta, abstractmethod
from typing import Protocol


class XmlSigner(Protocol, metaclass=ABCMeta):
    """Signs DGII's documents with the issuing company's certificate, in DGII's profile."""

    @abstractmethod
    async def sign_document(self, document: bytes) -> bytes:
        """``document`` with DGII's enveloped signature appended as its root's last child."""

    @abstractmethod
    async def sign_seed(self, seed: bytes) -> bytes:
        """DGII's or a receiver's authentication seed (``SemillaModel``), signed the same way."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/__init__.py`:

```python
"""XML signatures as DGII makes and checks them (*Firmado de e-CF*): the profile, signing, verifying."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/profile.py`:

```python
"""DGII's XML signature profile (*Firmado de e-CF*): enveloped XMLDSig, C14N 1.0, SHA-256, RSA-SHA256.

The ``Signature`` element, in the XMLDSig namespace as its default one, is the document root's last child::

    <Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>
      <CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
      <SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
      <Reference URI=""><Transforms>
        <Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
      </Transforms><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
      <DigestValue>...</DigestValue></Reference></SignedInfo>
      <SignatureValue>...</SignatureValue>
      <KeyInfo><X509Data><X509Certificate>...</X509Certificate></X509Data></KeyInfo></Signature>

written on one line, without the indentation. Not XAdES: DGII's documents and samples carry no XAdES
properties.
"""

from typing import Final
from xml.sax.saxutils import quoteattr
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG

C14N: Final = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA256: Final = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256: Final = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED: Final = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
_HEAD: Final = (("CanonicalizationMethod", C14N), ("SignatureMethod", RSA_SHA256), ("Transform", ENVELOPED))
PROFILES: Final = frozenset(
    {
        (*_HEAD, ("DigestMethod", SHA256)),
        (*_HEAD, ("Transform", C14N), ("DigestMethod", SHA256)),
    }
)
"""The ``Algorithm`` attributes of ``SignedInfo`` in document order, as DGII's profile allows them: DGII's own
shape, and the same with the canonicalization written out as a second transform (signxml, .NET)."""


def signed_info(digest: str, in_scope: Mapping[str, str] | None = None) -> str:
    """``SignedInfo`` as text: as it sits in the document (``in_scope`` is ``None``), or standalone.

    Standalone, for canonicalization, it declares what is in scope where it sits: the default namespace of
    ``Signature`` and ``in_scope``, the prefixed namespaces of the document's root. lxml's C14N of an element
    inside a document writes a spurious ``xmlns=""`` on grandchildren under a default namespace, which xmlsec1
    and .NET reject (v1 M3 Decision 13), hence the standalone form.
    """
    if in_scope is None:
        declarations = ""
    else:
        prefixed = "".join(f" xmlns:{prefix}={quoteattr(uri)}" for prefix, uri in sorted(in_scope.items()))
        declarations = f' xmlns="{DSIG}"{prefixed}'
    return (
        f"<SignedInfo{declarations}>"
        f'<CanonicalizationMethod Algorithm="{C14N}"/>'
        f'<SignatureMethod Algorithm="{RSA_SHA256}"/>'
        f'<Reference URI=""><Transforms><Transform Algorithm="{ENVELOPED}"/></Transforms>'
        f'<DigestMethod Algorithm="{SHA256}"/><DigestValue>{digest}</DigestValue></Reference>'
        "</SignedInfo>"
    )


def signature(digest: str, value: str, certificate: str) -> str:
    """The ``Signature`` element as text, the XMLDSig namespace its default one."""
    return (
        f'<Signature xmlns="{DSIG}">{signed_info(digest)}'
        f"<SignatureValue>{value}</SignatureValue>"
        f"<KeyInfo><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo>"
        "</Signature>"
    )
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/signer.py`:

```python
"""Signing in DGII's profile with a key object held by the caller (``profile``).

The spike's only signer (Coordination 8): v2-M2 moves this module to the signer module, where keys stay in the
signer process (spec rule 7). Nothing here loads a key from a file.

The reference covers the whole document less the signature: its digest is SHA-256 over the document's
inclusive canonical form (C14N 1.0, without comments), taken before the signature is added, which is the same
thing. ``SignatureValue`` is RSASSA-PKCS1-v1_5 with SHA-256 over ``SignedInfo``'s canonical form. Documents
are read hardened (``xml.parsing``); a document already signed is refused (an enveloped signature would leave it
unsigned by this one); a seed must be DGII's ``SemillaModel`` (``valor``, ``fecha``). The signed document is the
received one written again by lxml (UTF-8, with its declaration): equal under canonicalization, which is all a
signature covers.
"""

import base64
import hashlib
from typing import ClassVar, Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import signature, signed_info
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

SEED_ROOT: Final = "SemillaModel"
SEED_CHILDREN: Final = ("valor", "fecha")


class DocumentSignedError(EcfInvalidError):
    """A document that already holds a signature."""

    code: ClassVar[str] = "do_ecf_document_signed"

    def __init__(self) -> None:
        super().__init__("the document already holds an XML signature")


class SeedMalformedError(EcfInvalidError):
    """What was to be signed as a seed is not DGII's ``SemillaModel``."""

    code: ClassVar[str] = "do_ecf_seed_malformed"

    def __init__(self) -> None:
        super().__init__("a seed is a SemillaModel holding valor then fecha")


class NotAnRsaKeyError(EcfError):
    """A key that is not RSA reached the signer: DGII's profile signs with RSA only."""

    code: ClassVar[str] = "do_ecf_key_not_rsa"

    def __init__(self) -> None:
        super().__init__("DGII's profile signs with RSA keys only")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _c14n(document: bytes) -> bytes:
    return etree.tostring(etree.fromstring(document, parser()), method="c14n", exclusive=False, with_comments=False)


def sign_enveloped(
    document: bytes, key: PrivateKeyTypes, certificate: x509.Certificate, *, seed: bool = False
) -> bytes:
    """``document`` signed with ``key`` and ``certificate`` in DGII's profile (see the module)."""
    if not isinstance(key, RSAPrivateKey):
        raise NotAnRsaKeyError
    require_readable(document)
    try:
        root = etree.fromstring(document, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    if root.tag == SIGNATURE or root.find(f".//{SIGNATURE}") is not None:
        raise DocumentSignedError
    children = [child.tag for child in root if isinstance(child.tag, str)]
    if seed and (root.tag != SEED_ROOT or children != list(SEED_CHILDREN)):
        raise SeedMalformedError
    tree = root.getroottree()
    digest = _b64(hashlib.sha256(etree.tostring(tree, method="c14n", exclusive=False, with_comments=False)).digest())
    in_scope = {prefix: uri for prefix, uri in root.nsmap.items() if prefix is not None}
    value = _b64(key.sign(_c14n(signed_info(digest, in_scope).encode()), padding.PKCS1v15(), hashes.SHA256()))
    root.append(etree.fromstring(signature(digest, value, _b64(certificate.public_bytes(Encoding.DER))), parser()))
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/verifier.py`:

```python
"""Verifying a signature in DGII's profile: our own documents, and what DGII's counterparts sign (ARECF).

The document must hold exactly one ``Signature``, its root's last element. Its ``SignedInfo`` must be DGII's
profile (``profile.PROFILES``) with one ``Reference`` to the whole document (``URI=""``). The digest is checked
over the document's canonical form without the ``Signature`` element (the enveloped-signature transform removes
the element, never the text around it), and ``SignatureValue`` with the RSA key of the certificate in
``KeyInfo``. ``SignedInfo`` is canonicalized as a standalone copy declaring every namespace in scope where it
sits (``profile.signed_info``'s reason). Whether that certificate is one to trust (its chain to a DR
authority, its subject's RNC) is the caller's question: ``VerifiedSignature.certificate`` answers it; chain
validation is v2-M4's.
"""

import base64
import copy
import hashlib
import hmac
from dataclasses import dataclass
from typing import ClassVar, final

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from lxml import etree

from ledger_country_do.channels.ecf_v1.adapters.signing.profile import PROFILES
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import (
    DSIG,
    SIGNATURE,
    MalformedDocumentError,
    parser,
    require_readable,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

_DS = f"{{{DSIG}}}"


class SignatureInvalidError(EcfInvalidError):
    """A document whose XML signature does not hold; the reason never quotes the document."""

    code: ClassVar[str] = "do_ecf_signature_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"the XML signature does not hold: {reason}")


@final
@dataclass(frozen=True, slots=True)
class VerifiedSignature:
    """A signature that holds: the certificate that made it, and its value as written (whitespace removed)."""

    certificate: x509.Certificate
    signature_value: str


def _decoded(text: str) -> bytes:
    return base64.b64decode("".join(text.split()), validate=True)


def verify_enveloped(signed: bytes) -> VerifiedSignature:
    """The signature of ``signed``, once it holds (see the module); ``SignatureInvalidError`` otherwise."""
    require_readable(signed)
    try:
        root = etree.fromstring(signed, parser())
    except etree.XMLSyntaxError as error:
        raise MalformedDocumentError from error
    if len(root.findall(f".//{SIGNATURE}")) != 1 or len(root) == 0 or root[-1].tag != SIGNATURE:
        raise SignatureInvalidError("the document holds no single signature as its root's last element")
    signature = root[-1]
    signed_info = signature.find(f"{_DS}SignedInfo")
    digest_text = None if signed_info is None else signed_info.findtext(f"{_DS}Reference/{_DS}DigestValue")
    value_text = signature.findtext(f"{_DS}SignatureValue")
    certificate_text = signature.findtext(f"{_DS}KeyInfo/{_DS}X509Data/{_DS}X509Certificate")
    if signed_info is None or digest_text is None or value_text is None or certificate_text is None:
        raise SignatureInvalidError("the signature is incomplete")
    algorithms = tuple(
        (etree.QName(found).localname, algorithm)
        for found in signed_info.iter(f"{_DS}*")
        if (algorithm := found.get("Algorithm")) is not None
    )
    references = signed_info.findall(f"{_DS}Reference")
    if algorithms not in PROFILES or len(references) != 1 or references[0].get("URI") != "":
        raise SignatureInvalidError("the signature is not DGII's profile")
    try:
        digest, value = _decoded(digest_text), _decoded(value_text)
        certificate = x509.load_der_x509_certificate(_decoded(certificate_text))
    except ValueError as error:
        raise SignatureInvalidError("a value is not base64, or the certificate is not DER") from error
    standalone = etree.Element(signed_info.tag, dict(signed_info.attrib), nsmap=signed_info.nsmap)
    standalone.text = signed_info.text
    standalone.extend(copy.deepcopy(child) for child in signed_info)
    canonical_info = etree.tostring(standalone, method="c14n", exclusive=False, with_comments=False)
    tail, previous = signature.tail, signature.getprevious()
    root.remove(signature)
    if tail:
        if previous is None:
            root.text = (root.text or "") + tail
        else:
            previous.tail = (previous.tail or "") + tail
    canonical = etree.tostring(root.getroottree(), method="c14n", exclusive=False, with_comments=False)
    if not hmac.compare_digest(hashlib.sha256(canonical).digest(), digest):
        raise SignatureInvalidError("the document changed after it was signed")
    public_key = certificate.public_key()
    if not isinstance(public_key, RSAPublicKey):
        raise SignatureInvalidError("the certificate's key is not RSA")
    try:
        public_key.verify(value, canonical_info, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        raise SignatureInvalidError("the signature value does not match the certificate's key") from error
    return VerifiedSignature(certificate=certificate, signature_value="".join(value_text.split()))
```

`countries/do/testing/src/ledger_country_do_testing/signatures.py`:

```python
"""signxml as the second implementation of XML signatures: it verifies ours and signs what ours must verify.

signxml shares no code with ``adapters.signing``; the fake DGII uses it to check what it receives and to sign
the ARECFs it answers. ``excise_empty_xmlns_declarations`` makes signxml undo lxml's spurious ``xmlns=""`` (its
issue 193), without which it cannot verify a signature in the default namespace. signxml resolves ``URI=""`` to
the root element, so a document with nodes outside its root is verified by xmlsec1 instead (``OUTSIDE_ROOT``).

signxml 5.1 leaves parts of ``verify`` and ``sign`` unannotated, which basedpyright ``all`` reports wherever
they are touched; the two Protocols below state the calls made here and the ``cast`` binds them.
"""

import base64
from abc import ABCMeta, abstractmethod
from types import MappingProxyType
from typing import Final, Protocol, cast
lazy from collections.abc import Collection, Mapping

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from lxml import etree
from signxml import XMLSigner, XMLVerifier, namespaces
from signxml.algorithms import CanonicalizationMethod, DigestAlgorithm, SignatureConstructionMethod, SignatureMethod
from signxml.exceptions import InvalidInput, InvalidSignature
from signxml.verifier import SignatureConfiguration, VerifyResult

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, texts
lazy from ledger_country_do_testing.pki import Issued

OUTSIDE_ROOT: Final = "outside-root"
SHAPES: Final[Mapping[str, bytes]] = MappingProxyType(
    {
        "ecf": (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ECF xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            "<Encabezado><Version>1.0</Version><IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000001</eNCF></IdDoc>"
            "<Emisor><RNCEmisor>101850043</RNCEmisor>"
            "<RazonSocialEmisor>Ferretería Duarte &amp; Hijos</RazonSocialEmisor></Emisor></Encabezado>"
            "<!-- a comment --><DetallesItems><Item><NombreItem>Martillo &lt;12&quot;&gt;</NombreItem></Item>"
            "</DetallesItems><FechaHoraFirma>13-10-2026 09:30:00</FechaHoraFirma></ECF>"
        ).encode(),
        "seed": (
            b'<?xml version="1.0" encoding="utf-8"?><SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            b' xmlns:xsd="http://www.w3.org/2001/XMLSchema"><valor>NmE1ZDg2YzQtMTc5OS00ZmM4</valor>'
            b"<fecha>2026-10-13T09:00:00.0000000-04:00</fecha></SemillaModel>"
        ),
        "plain": b"<a><b>x</b></a>",
        "default-namespace": b'<Doc xmlns="urn:d" a="1"><x:Part xmlns:x="urn:example:x">t</x:Part></Doc>',
        "nested-defaults": b'<r><x xmlns="urn:a"><y><z q="1"/></y></x><w xmlns:p="urn:p"><p:v/></w></r>',
        "references": b'<r>Pe\xc3\xb1a &#241; &#x1F600; &lt; &gt; &amp; "q"<e a="&#9;tab&#10;nl&#13;"/></r>',
        OUTSIDE_ROOT: b'<?xml version="1.0"?><?pi before?><!-- c --><r><!-- in --><?pi x?>t</r><!-- after -->',
    }
)
"""Documents of every shape a signature must survive: DGII's, namespaces on the root, default and nested
namespaces, character references, CR and tabs in attributes, astral characters, nodes outside the root."""
_CONFIGURATION: Final = SignatureConfiguration(
    require_x509=True,
    location="./",
    expect_references=1,
    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
)


class _Verifies(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLVerifier`` as called here."""

    excise_empty_xmlns_declarations: bool

    @abstractmethod
    def verify(
        self, data: bytes, *, x509_cert: x509.Certificate, expect_config: SignatureConfiguration
    ) -> VerifyResult | list[VerifyResult]:
        """Verify ``data`` with ``x509_cert``."""


class _Signs(Protocol, metaclass=ABCMeta):
    """signxml's ``XMLSigner`` as called here."""

    namespaces: dict[str | None, str]

    @abstractmethod
    def sign(self, data: bytes, *, key: bytes, cert: bytes) -> etree.ElementBase:
        """``data`` with an enveloped signature, as an element."""


def verify_with_signxml(signed: bytes, *, trusted: Collection[x509.Certificate] | None = None) -> bool:
    """Whether signxml finds ``signed``'s signature good, by the certificate it carries, if ``trusted`` holds it.

    ``trusted`` ``None`` trusts the carried certificate (the local fake's mode: the owner's real certificate is
    unknown to it); the signature itself is always checked.
    """
    (carried,) = texts(signed, f"{{{DSIG}}}Signature/{{{DSIG}}}KeyInfo/{{{DSIG}}}X509Data/{{{DSIG}}}X509Certificate")
    if carried is None:
        return False
    certificate = x509.load_der_x509_certificate(base64.b64decode("".join(carried.split())))
    if trusted is not None and certificate not in trusted:
        return False
    verifier = cast("_Verifies", XMLVerifier())
    verifier.excise_empty_xmlns_declarations = True
    try:
        verifier.verify(signed, x509_cert=certificate, expect_config=_CONFIGURATION)
    except InvalidSignature, InvalidInput:
        return False
    return True


def sign_with_signxml(document: bytes, issued: Issued, *, prefixed: bool = False) -> bytes:
    """``document`` signed by signxml (enveloped, C14N 1.0, RSA-SHA256), the namespace default or ``ds:``."""
    signer = cast(
        "_Signs",
        XMLSigner(
            method=SignatureConstructionMethod.enveloped,
            signature_algorithm=SignatureMethod.RSA_SHA256,
            digest_algorithm=DigestAlgorithm.SHA256,
            c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0,
        ),
    )
    if not prefixed:
        signer.namespaces = {None: namespaces.ds}
    key = issued.key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    signed = signer.sign(document, key=key, cert=issued.pem())
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")
```

`countries/do/testing/src/ledger_country_do_testing/local_signer.py`:

```python
"""A signer that holds a key in this process: for tests, the fake DGII and the spike CLI, never production.

In production keys stay in the signer process (spec rule 7); this distribution is dev-only and never installed
in an image. A PKCS#12 bundle is read from bytes the caller loaded (the CLI reads it from the git-ignored
``.dgii/`` directory); its password is never echoed.
"""

import asyncio
from typing import final, override

from cryptography.hazmat.primitives.serialization import pkcs12

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do.channels.ecf_v1.application.ports import XmlSigner
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

lazy from ledger_country_do_testing.pki import Issued


class BundleIncompleteError(ValueError):
    """A PKCS#12 bundle without a private key and its certificate."""

    def __init__(self) -> None:
        super().__init__("the PKCS#12 bundle holds no private key and certificate pair")


@final
class KeyPairSigner(XmlSigner):
    """``XmlSigner`` over a key and certificate in memory; signing runs in a worker thread."""

    __slots__ = ("_certificate", "_key")

    def __init__(self, key: PrivateKeyTypes, certificate: x509.Certificate) -> None:
        self._key = key
        self._certificate = certificate

    @classmethod
    def from_issued(cls, issued: Issued) -> KeyPairSigner:
        """A signer of a test certificate."""
        return cls(issued.key, issued.certificate)

    @classmethod
    def from_pkcs12(cls, data: bytes, password: bytes) -> KeyPairSigner:
        """A signer of the key and certificate in a PKCS#12 bundle; ``ValueError`` on a wrong password."""
        loaded = pkcs12.load_pkcs12(data, password)
        if loaded.key is None or loaded.cert is None:
            raise BundleIncompleteError
        return cls(loaded.key, loaded.cert.certificate)

    @property
    def certificate(self) -> x509.Certificate:
        """The certificate the signatures carry."""
        return self._certificate

    @override
    async def sign_document(self, document: bytes) -> bytes:
        return await asyncio.to_thread(sign_enveloped, document, self._key, self._certificate)

    @override
    async def sign_seed(self, seed: bytes) -> bytes:
        return await asyncio.to_thread(sign_enveloped, seed, self._key, self._certificate, seed=True)
```

`countries/do/testing/src/ledger_country_do_testing/xmlsec1/Dockerfile` (v1 M3 Task 2's, verified there):

```dockerfile
# xmlsec1, the reference implementation of XML Signature, for the tests that verify the DO pack's signatures
# (ledger_country_do_testing.xmlsec). Base image and package pinned, so every machine verifies with the same
# libxml2 canonicalization and the same OpenSSL. Not a runtime image: tests only.
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
RUN apt-get update \
 && apt-get install --yes --no-install-recommends xmlsec1=1.2.41-1+b1 \
 && rm -rf /var/lib/apt/lists/* \
 && xmlsec1 --version
CMD ["sleep", "infinity"]
```

`countries/do/testing/src/ledger_country_do_testing/xmlsec.py`:

```python
"""xmlsec1, the reference implementation of XML Signature, as the independent verifier of our signatures.

The pack signs with lxml and cryptography; a test proves a signature right with an implementation sharing none
of that code: xmlsec1 1.2.41 of Debian trixie, in a container built from ``xmlsec1/Dockerfile`` (Docker keeps
the image: built once per machine). The document and certificates travel in the command, base64-encoded: no
host file is shared with the container, so a test runs the same on Windows and Linux.
"""

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final
lazy from collections.abc import Generator
lazy from datetime import datetime

from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage

XMLSEC_IMAGE: Final = "ledger-do-xmlsec1:1.2.41"
_CONTEXT: Final = Path(__file__).with_name("xmlsec1")
_VERIFY: Final = (
    'printf %s "$1" | base64 -d > /tmp/signed.xml'
    ' && printf %s "$2" | base64 -d > /tmp/trusted.pem'
    ' && printf %s "$3" | base64 -d > /tmp/untrusted.pem'
    " && exec xmlsec1 --verify --trusted-pem /tmp/trusted.pem --untrusted-pem /tmp/untrusted.pem"
    ' --verification-time "$4" /tmp/signed.xml'
)
_TIME: Final = "%Y-%m-%d %H:%M:%S"


@final
@dataclass(frozen=True, slots=True)
class Verdict:
    """What xmlsec1 said of a signature: whether it holds, and its own words (for a failing test's report)."""

    verified: bool
    output: str


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@final
class Xmlsec:
    """A running xmlsec1 container."""

    __slots__ = ("_container",)

    def __init__(self, container: DockerContainer) -> None:
        self._container = container

    @classmethod
    @contextmanager
    def started(cls) -> Generator[Xmlsec]:
        """xmlsec1, built if this machine lacks the image, for the length of the block."""
        with (
            DockerImage(path=_CONTEXT, tag=XMLSEC_IMAGE, clean_up=False),
            DockerContainer(XMLSEC_IMAGE) as container,
        ):
            yield cls(container)

    def verify(self, signed: bytes, *, trusted: bytes, untrusted: bytes, at: datetime) -> Verdict:
        """Whether ``signed`` holds an enveloped signature by a certificate chaining to ``trusted`` (PEM)."""
        arguments = [_encoded(signed), _encoded(trusted), _encoded(untrusted), at.strftime(_TIME)]
        result = self._container.exec(["sh", "-c", _VERIFY, "verify", *arguments])
        return Verdict(result.exit_code == 0, result.output.decode("utf-8", "replace"))
```

The Dockerfile is package data: `uv_build` ships every file under `src/ledger_country_do_testing/`; confirm with `uv run python -c "import ledger_country_do_testing.xmlsec as x; print(x._CONTEXT.joinpath('Dockerfile').exists())"` (prints `True`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries -q --no-cov -k "signing or signatures or local_signer or xmlsec"`
Expected: PASS (the first run builds the xmlsec1 image, about a minute on a cold cache). Every shape verifies in our verifier, in signxml (all but `outside-root`) and in xmlsec1 with its chain; signxml's signatures, default-namespace and `ds:`-prefixed, verify in ours; 60 generated documents round-trip.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (both whitespace branches of the verifier are covered by `test_whitespace_around_the_signature_is_the_document_s_own`).

- [ ] **Step 7: Commit**

```bash
git add uv.lock countries
git commit -m "feat(do): DGII's XML signature profile, signed and verified, proven by signxml and xmlsec1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Printed-representation data: the security code and the QR URLs

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/domain/{environments, printed}.py`, `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/stamp.py`
- Test: `countries/do/tests/unit/test_printed.py`

**Interfaces:**
- Consumes: `domain.encf`, `domain.notation`, `domain.errors` (Tasks 3-4); `adapters.xml.parsing.texts`, `adapters.signing.signer.sign_enveloped`, `adapters.signing.verifier.verify_enveloped` (Tasks 3, 6).
- Produces: `domain.environments`: `OFFICIAL_ECF_HOST`, `OFFICIAL_FC_HOST`, `Environment` (`TESTECF`, `CERTECF`, `ECF`) with `live`. `domain.printed`: `SECURITY_CODE_LENGTH`, `SecurityCodeError` (`do_ecf_security_code`), `security_code(signature_value) -> str`, `QrHosts(ecf, fc)`, `OFFICIAL_QR`, `qr_url(*, environment, issuer_rnc, buyer_rnc, encf, issued_on, total, signed_at, security_code, hosts=OFFICIAL_QR) -> str`, `rfce_qr_url(*, environment, issuer_rnc, encf, total, security_code, hosts=OFFICIAL_QR) -> str`. `adapters.signing.stamp`: `SignatureValueMissingError` (`do_ecf_signature_value_missing`), `signature_value(signed) -> str`.

- [ ] **Step 1: Write the failing test**

`countries/do/tests/unit/test_printed.py`:

```python
from decimal import Decimal

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do.channels.ecf_v1.adapters.signing.stamp import SignatureValueMissingError, signature_value
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.printed import (
    QrHosts,
    SecurityCodeError,
    qr_url,
    rfce_qr_url,
    security_code,
)
from ledger_country_do_testing.pki import Issued
from ledger_country_do_testing.samples import ISSUED_ON, SIGNED_AT
from ledger_country_do_testing.signatures import SHAPES


def test_the_security_code_is_the_signature_value_s_first_six_characters() -> None:
    assert security_code("dcp79qAbCdEf==") == "dcp79q"
    assert security_code(" dc\np7\t9q rest") == "dcp79q"
    with pytest.raises(SecurityCodeError):
        security_code("abc de")


def test_the_qr_of_an_e_cf_names_dgii_s_consultatimbre_with_it_s_seven_parameters() -> None:
    url = qr_url(
        environment=Environment.TESTECF,
        issuer_rnc="101850043",
        buyer_rnc="131098193",
        encf=Encf(EcfType.CREDIT_INVOICE, 1),
        issued_on=ISSUED_ON,
        total=Decimal("4887.58"),
        signed_at=SIGNED_AT,
        security_code="dcp79q",
    )
    assert url == (
        "https://ecf.dgii.gov.do/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131098193"
        "&ENCF=E310000000001&FechaEmision=13-10-2026&MontoTotal=4887.58&FechaFirma=13-10-2026%2009:30:00"
        "&CodigoSeguridad=dcp79q"
    )


def test_without_a_buyer_rnc_the_qr_leaves_it_out_and_base64_is_encoded() -> None:
    url = qr_url(
        environment=Environment.CERTECF,
        issuer_rnc="101850043",
        buyer_rnc=None,
        encf=Encf(EcfType.CONSUMER_INVOICE, 2),
        issued_on=ISSUED_ON,
        total=Decimal(295000),
        signed_at=SIGNED_AT,
        security_code="ab+/cd",
        hosts=QrHosts(ecf="http://127.0.0.1:8471", fc="http://127.0.0.1:8471"),
    )
    assert url.startswith("http://127.0.0.1:8471/certecf/consultatimbre?RncEmisor=101850043&ENCF=")
    assert "RncComprador" not in url
    assert url.endswith("MontoTotal=295000.00&FechaFirma=13-10-2026%2009:30:00&CodigoSeguridad=ab%2B%2Fcd")


def test_the_qr_of_an_rfce_names_consultatimbrefc_on_dgii_s_fc_host() -> None:
    url = rfce_qr_url(
        environment=Environment.TESTECF,
        issuer_rnc="101850043",
        encf=Encf(EcfType.CONSUMER_INVOICE, 1),
        total=Decimal("1180.00"),
        security_code="dcp79q",
    )
    assert url == (
        "https://fc.dgii.gov.do/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001"
        "&MontoTotal=1180.00&CodigoSeguridad=dcp79q"
    )


def test_the_signature_value_is_read_from_the_signed_document(issued: Issued) -> None:
    signed = sign_enveloped(SHAPES["ecf"], issued.key, issued.certificate)
    assert signature_value(signed) == verify_enveloped(signed).signature_value
    with pytest.raises(SignatureValueMissingError):
        signature_value(SHAPES["ecf"])
    empty = b'<a><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignatureValue> </SignatureValue></Signature></a>'
    with pytest.raises(SignatureValueMissingError):
        signature_value(empty)


def test_only_ecf_is_live() -> None:
    assert [environment.live for environment in Environment] == [False, False, True]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest countries/do/tests/unit/test_printed.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.domain.environments'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/environments.py`:

```python
"""DGII's environments and hosts (DT-DGII): ``testecf`` (pre-certification), ``certecf``, ``ecf`` (production).

Test mode uses ``testecf`` and ``certecf``; only ``ecf`` is live (spec §4.3 ``live_environments``), and the
spike never reaches it (Global Constraints). Paths are case-insensitive at DGII; the lower-case spelling of
DT-DGII's URL lists is used everywhere.
"""

from enum import StrEnum
from typing import Final

OFFICIAL_ECF_HOST: Final = "https://ecf.dgii.gov.do"
OFFICIAL_FC_HOST: Final = "https://fc.dgii.gov.do"
"""The host of the consumer-invoice summary (RFCE) services."""


class Environment(StrEnum):
    """A DGII environment, by its URL segment."""

    TESTECF = "testecf"
    CERTECF = "certecf"
    ECF = "ecf"

    @property
    def live(self) -> bool:
        """Whether documents sent there are legally issued."""
        return self is Environment.ECF
```

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/printed.py`:

```python
"""What a printed representation shows of a signed e-CF: its security code and its QR URL (IT §18.2.3).

The security code is the first six characters of the ``SignatureValue`` (base64, as written): IT's "los
primeros seis (6) dígitos del hash generado en el SignatureValue", as DGII's examples show (``dcp79q``). The QR
names DGII's ``consultatimbre`` with ``RncEmisor``, ``RncComprador`` (left out without one: F8), ``ENCF``,
``FechaEmision``, ``MontoTotal``, ``FechaFirma`` and ``CodigoSeguridad``; an RFCE's names ``consultatimbrefc``
on the ``fc`` host with ``RncEmisor``, ``ENCF``, ``MontoTotal`` and ``CodigoSeguridad``. Values are
percent-encoded except ``-``, ``:`` and ``.`` (IT's example writes ``dd-MM-yyyy%20HH:mm:ss``). QR version 8.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
from urllib.parse import quote
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import dgii_amount, dgii_date, dgii_moment
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

SECURITY_CODE_LENGTH: Final = 6
_KEPT: Final = "-:."


class SecurityCodeError(EcfError):
    """A signature value too short to give a security code: the signer answered something it never answers."""

    code: ClassVar[str] = "do_ecf_security_code"

    def __init__(self) -> None:
        super().__init__("a signature value holds at least six characters")


def security_code(signature_value: str) -> str:
    """The first six characters of ``signature_value``, whitespace removed."""
    compact = "".join(signature_value.split())
    if len(compact) < SECURITY_CODE_LENGTH:
        raise SecurityCodeError
    return compact[:SECURITY_CODE_LENGTH]


@final
@dataclass(frozen=True, slots=True)
class QrHosts:
    """Where the QR's queries are answered: DGII's two hosts, or a fake's."""

    ecf: str
    fc: str


OFFICIAL_QR: Final = QrHosts(ecf=OFFICIAL_ECF_HOST, fc=OFFICIAL_FC_HOST)


def _query(parameters: tuple[tuple[str, str], ...]) -> str:
    return "&".join(f"{name}={quote(value, safe=_KEPT)}" for name, value in parameters)


def qr_url(
    *,
    environment: Environment,
    issuer_rnc: str,
    buyer_rnc: str | None,
    encf: Encf,
    issued_on: date,
    total: Decimal,
    signed_at: datetime,
    security_code: str,
    hosts: QrHosts = OFFICIAL_QR,
) -> str:
    """The QR URL of an e-CF sent in full (see the module)."""
    parameters = (
        ("RncEmisor", issuer_rnc),
        *((("RncComprador", buyer_rnc),) if buyer_rnc is not None else ()),
        ("ENCF", str(encf)),
        ("FechaEmision", dgii_date(issued_on)),
        ("MontoTotal", dgii_amount(total)),
        ("FechaFirma", dgii_moment(signed_at)),
        ("CodigoSeguridad", security_code),
    )
    return f"{hosts.ecf}/{environment.value}/consultatimbre?{_query(parameters)}"


def rfce_qr_url(
    *,
    environment: Environment,
    issuer_rnc: str,
    encf: Encf,
    total: Decimal,
    security_code: str,
    hosts: QrHosts = OFFICIAL_QR,
) -> str:
    """The QR URL of a consumer invoice sent as an RFCE (see the module)."""
    parameters = (
        ("RncEmisor", issuer_rnc),
        ("ENCF", str(encf)),
        ("MontoTotal", dgii_amount(total)),
        ("CodigoSeguridad", security_code),
    )
    return f"{hosts.fc}/{environment.value}/consultatimbrefc?{_query(parameters)}"
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/signing/stamp.py`:

```python
"""The ``SignatureValue`` a signed document carries: where its security code comes from."""

from typing import ClassVar

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import DSIG, texts
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError


class SignatureValueMissingError(EcfError):
    """A document said to be signed holds no signature value: the signer answered something it never answers."""

    code: ClassVar[str] = "do_ecf_signature_value_missing"

    def __init__(self) -> None:
        super().__init__("the signed document holds no SignatureValue")


def signature_value(signed: bytes) -> str:
    """The signature value of ``signed``, whitespace removed."""
    (value,) = texts(signed, f".//{{{DSIG}}}SignatureValue")
    if value is None or not value.strip():
        raise SignatureValueMissingError
    return "".join(value.split())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest countries/do/tests/unit/test_printed.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add countries/do
git commit -m "feat(do): security code and QR URLs of e-CF and RFCE as DGII's technical report gives them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: DGII's status codes mapped: Aceptado Condicional, secuenciaUtilizada, exhaustiveness

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/domain/status_map.py`
- Test: `countries/do/tests/unit/test_status_map.py`

**Interfaces:**
- Consumes: `domain.errors` (Task 3).
- Produces: `domain.status_map`: `DgiiCode` (`NOT_FOUND = 0`, `ACCEPTED = 1`, `REJECTED = 2`, `IN_PROCESS = 3`, `ACCEPTED_CONDITIONALLY = 4`), `DOCUMENTED: Mapping[int, str]`, `RFCE_DOCUMENTED: Mapping[int, str]`, `EcfState`, `FiscalStatus`, `Severity`, `NumberFate` (`USED`, `BURNED`, `RELEASED`), `StatusEntry(state, status, severity, final)`, `STATUS_MAP: Mapping[DgiiCode, StatusEntry]`, `MAX_MESSAGES` (20), `MAX_MESSAGE_TEXT` (500), `DgiiMessage(code, text)`, `Outcome(code, entry, messages, number)` with `state`, `status`, `severity`, `final`, `UnknownDgiiCodeError` (`do_ecf_dgii_code_unknown`), `decide(code, *, sequence_used, messages, rfce=False) -> Outcome`.

- [ ] **Step 1: Write the failing test**

`countries/do/tests/unit/test_status_map.py`:

```python
import pytest

from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DOCUMENTED,
    MAX_MESSAGE_TEXT,
    MAX_MESSAGES,
    RFCE_DOCUMENTED,
    STATUS_MAP,
    DgiiCode,
    DgiiMessage,
    EcfState,
    FiscalStatus,
    NumberFate,
    Severity,
    UnknownDgiiCodeError,
    decide,
)

NOTE = (DgiiMessage("613", "La sumatoria de los montos ... excede la tolerancia"),)


def test_every_code_dgii_documents_is_mapped_and_nothing_else() -> None:
    assert set(STATUS_MAP) == set(DgiiCode)
    assert set(DOCUMENTED) == {code.value for code in DgiiCode}
    assert set(RFCE_DOCUMENTED) <= set(DOCUMENTED)
    assert DOCUMENTED[4] == "Aceptado Condicional"


def test_aceptado_condicional_is_valid_with_observations() -> None:
    outcome = decide(4, sequence_used=True, messages=NOTE)
    assert (outcome.state, outcome.status, outcome.severity) == (
        EcfState.ACCEPTED_CONDITIONALLY,
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        Severity.WARNING,
    )
    assert outcome.final
    assert outcome.number is NumberFate.USED
    assert outcome.messages == NOTE


def test_aceptado_uses_the_number() -> None:
    outcome = decide(1, sequence_used=None, messages=())
    assert (outcome.status, outcome.severity, outcome.final, outcome.number) == (
        FiscalStatus.ACCEPTED,
        Severity.INFO,
        True,
        NumberFate.USED,
    )


@pytest.mark.parametrize(
    ("sequence_used", "fate"),
    [(False, NumberFate.RELEASED), (True, NumberFate.BURNED), (None, NumberFate.BURNED)],
)
def test_a_rejection_burns_the_number_unless_dgii_releases_it(sequence_used: bool | None, fate: NumberFate) -> None:
    outcome = decide(2, sequence_used=sequence_used, messages=())
    assert (outcome.status, outcome.severity, outcome.final, outcome.number) == (
        FiscalStatus.REJECTED,
        Severity.ERROR,
        True,
        fate,
    )


@pytest.mark.parametrize(("code", "state"), [(3, EcfState.IN_PROCESS), (0, EcfState.OUTCOME_UNKNOWN)])
def test_en_proceso_and_no_encontrado_are_not_final(code: int, state: EcfState) -> None:
    outcome = decide(code, sequence_used=None, messages=())
    assert (outcome.state, outcome.status, outcome.final, outcome.number) == (state, FiscalStatus.PENDING, False, None)


@pytest.mark.parametrize(("code", "rfce"), [(5, False), (-1, False), (3, True), (0, True)])
def test_a_code_dgii_never_documented_for_the_service_is_an_error_not_a_guess(code: int, rfce: bool) -> None:
    with pytest.raises(UnknownDgiiCodeError) as caught:
        decide(code, sequence_used=None, messages=(), rfce=rfce)
    assert caught.value.kind == "unavailable"


def test_dgii_s_messages_are_kept_verbatim_within_bounds() -> None:
    many = tuple(DgiiMessage(str(number), "x" * 900) for number in range(30))
    outcome = decide(4, sequence_used=True, messages=many)
    assert len(outcome.messages) == MAX_MESSAGES
    assert all(len(message.text) == MAX_MESSAGE_TEXT for message in outcome.messages)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest countries/do/tests/unit/test_status_map.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.domain.status_map'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/domain/status_map.py`:

```python
"""DGII's status codes, each mapped to a channel state, a fiscal status and a severity (spec §8.6.4).

DT-DGII, "Consulta de resultado e-CF": 0 No encontrado, 1 Aceptado, 2 Rechazado, 3 En Proceso, 4 Aceptado
Condicional. Aceptado Condicional "implica la validez del e-CF": it is accepted, delivered like an accepted one,
its messages kept as warnings to fix in future documents, nothing re-issued ([06] §1). A rejection burns the
e-NCF unless DGII says ``secuenciaUtilizada = false`` (invalid certificate or signature, invalid XML, signer not
delegated, e-NCF not authorized or expired, RNC not an electronic issuer, unknown or inactive), which releases it
for reuse; an absent flag burns it (never reuse a number DGII may have recorded). The RFCE answers at once
with 1, 2 or 4 (F2). A code DGII never documented for the service is an error, never a guess. Messages are kept
verbatim, at most 20 of 500 characters (spec §8.4, kit events).

``FiscalStatus`` and ``Severity`` are provisional copies of fiscal-kit's (spec §8.4), swapped at integration.
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping, Sequence

from ledger_country_do.channels.ecf_v1.domain.errors import EcfUnavailableError, truncate

MAX_MESSAGES: Final = 20
MAX_MESSAGE_TEXT: Final = 500
_MAX_MESSAGE_CODE: Final = 64


class DgiiCode(IntEnum):
    """DGII's ``codigo`` of a validation result."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


DOCUMENTED: Final[Mapping[int, str]] = MappingProxyType(
    {0: "No encontrado", 1: "Aceptado", 2: "Rechazado", 3: "En Proceso", 4: "Aceptado Condicional"}
)
"""DT-DGII's codes and words for ``consultaresultado``, as DGII writes them."""
RFCE_DOCUMENTED: Final[Mapping[int, str]] = MappingProxyType({1: "Aceptado", 2: "Rechazado", 4: "Aceptado Condicional"})
"""What the RFCE reception answers (DT-DGII lists the words; the codes are assumed equal: F2)."""


class EcfState(StrEnum):
    """The channel states DGII's answers lead to (a subset of the e-CF state machine, spec §8.6.4)."""

    IN_PROCESS = "in_process"
    ACCEPTED = "accepted"
    ACCEPTED_CONDITIONALLY = "accepted_conditionally"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FiscalStatus(StrEnum):
    """The country-neutral status of a fiscal document (fiscal-kit's, spec §8.4)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_OBSERVATIONS = "accepted_with_observations"
    REJECTED = "rejected"
    VOIDED = "voided"


class Severity(StrEnum):
    """What an outcome asks of the platform: nothing, attention, or a person (spec §8.3)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NumberFate(StrEnum):
    """What became of the e-NCF (spec §8.6.3, number accounting)."""

    USED = "used"
    BURNED = "burned"
    RELEASED = "released"


@final
@dataclass(frozen=True, slots=True)
class StatusEntry:
    """Where a DGII code leads."""

    state: EcfState
    status: FiscalStatus
    severity: Severity
    final: bool


STATUS_MAP: Final[Mapping[DgiiCode, StatusEntry]] = MappingProxyType(
    {
        DgiiCode.NOT_FOUND: StatusEntry(EcfState.OUTCOME_UNKNOWN, FiscalStatus.PENDING, Severity.INFO, final=False),
        DgiiCode.ACCEPTED: StatusEntry(EcfState.ACCEPTED, FiscalStatus.ACCEPTED, Severity.INFO, final=True),
        DgiiCode.REJECTED: StatusEntry(EcfState.REJECTED, FiscalStatus.REJECTED, Severity.ERROR, final=True),
        DgiiCode.IN_PROCESS: StatusEntry(EcfState.IN_PROCESS, FiscalStatus.PENDING, Severity.INFO, final=False),
        DgiiCode.ACCEPTED_CONDITIONALLY: StatusEntry(
            EcfState.ACCEPTED_CONDITIONALLY, FiscalStatus.ACCEPTED_WITH_OBSERVATIONS, Severity.WARNING, final=True
        ),
    }
)


@final
@dataclass(frozen=True, slots=True)
class DgiiMessage:
    """One of DGII's messages: its code (a number or a word, as text) and its text, verbatim."""

    code: str
    text: str


@final
@dataclass(frozen=True, slots=True)
class Outcome:
    """DGII's answer about one document, mapped."""

    code: DgiiCode
    entry: StatusEntry
    messages: tuple[DgiiMessage, ...]
    number: NumberFate | None
    """``None`` while DGII has not decided."""

    @property
    def state(self) -> EcfState:
        """The channel state."""
        return self.entry.state

    @property
    def status(self) -> FiscalStatus:
        """The fiscal status."""
        return self.entry.status

    @property
    def severity(self) -> Severity:
        """The severity."""
        return self.entry.severity

    @property
    def final(self) -> bool:
        """Whether DGII has decided."""
        return self.entry.final


class UnknownDgiiCodeError(EcfUnavailableError):
    """DGII answered a code it never documented for the service: read as an outage, retried later."""

    code: ClassVar[str] = "do_ecf_dgii_code_unknown"

    def __init__(self, found: int) -> None:
        super().__init__(f"DGII answered code {found}, which its documentation does not define for this service")


def _fate(code: DgiiCode, sequence_used: bool | None) -> NumberFate | None:
    match code:
        case DgiiCode.ACCEPTED | DgiiCode.ACCEPTED_CONDITIONALLY:
            return NumberFate.USED
        case DgiiCode.REJECTED:
            return NumberFate.RELEASED if sequence_used is False else NumberFate.BURNED
        case DgiiCode.NOT_FOUND | DgiiCode.IN_PROCESS:
            return None


def decide(
    code: int, *, sequence_used: bool | None, messages: Sequence[DgiiMessage], rfce: bool = False
) -> Outcome:
    """DGII's ``code`` (of the RFCE service when ``rfce``) mapped (see the module)."""
    if code not in (RFCE_DOCUMENTED if rfce else DOCUMENTED):
        raise UnknownDgiiCodeError(code)
    dgii = DgiiCode(code)
    kept = tuple(
        DgiiMessage(truncate(message.code, _MAX_MESSAGE_CODE), truncate(message.text, MAX_MESSAGE_TEXT))
        for message in messages[:MAX_MESSAGES]
    )
    return Outcome(dgii, STATUS_MAP[dgii], kept, _fate(dgii, sequence_used))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest countries/do/tests/unit/test_status_map.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 % (the `match` in `_fate` is exhaustive over `DgiiCode`: mypy's `exhaustive-match` and basedpyright both check it).

- [ ] **Step 6: Commit**

```bash
git add countries/do
git commit -m "feat(do): DGII's status codes mapped, Aceptado Condicional valid, secuenciaUtilizada honored

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The fake DGII: authentication, reception, results, track ids, the RFCE and its query

**Files:**
- Create: `countries/do/testing/src/ledger_country_do_testing/{clock, answers}.py`, `countries/do/testing/src/ledger_country_do_testing/fake_dgii/{__init__, state, emitter, app}.py`
- Create: `countries/do/testing/tests/conftest.py`
- Modify: `countries/do/testing/pyproject.toml` (dependencies gain `"pydantic>=2.14.1"`, `"python-multipart>=0.0.32"`, `"starlette>=1.7.0"`, `"xmlschema>=4.3.2"`; a deptry rule for `python-multipart`)
- Test: `countries/do/testing/tests/test_fake_dgii_emitter.py`

**Interfaces:**
- Consumes: `adapters.xml.parsing.{texts, UnreadableDocumentError}`, `adapters.xml.schemas.corrected`, `adapters.xsd.published.Format`, `domain.notation.SANTO_DOMINGO` (Tasks 3-4); `signatures.verify_with_signxml`, `local_signer.KeyPairSigner`, `pki` (Tasks 1, 6); `adapters.xml.ecf`, `adapters.xml.rfce`, `domain.totals`, `samples` in tests (Tasks 4-5).
- Produces: `ledger_country_do_testing.clock.ManualClock(now)` with `__call__()`, `advance(delta)`. `ledger_country_do_testing.answers`: `json_object(response) -> dict[str, object]`, `json_list(response) -> list[dict[str, object]]`, `objects(value) -> list[dict[str, object]]`. `fake_dgii.state`: `TOKEN_LIFETIME`, `SEED_LIFETIME`, `Status`, `STATUS_WORDS`, `SignatureCheck` (`verify(signed) -> bool`), `SignxmlCheck(*, trusted)`, `Scripted(status, messages, processing, sequence_used)`, `Script(by_encf, default, outage)`, `Received(...)`, `DirectoryEntry(name, rnc, reception, approval, authentication)`, `Credentials` (`seed()`, `take_seed(value)`, `token()`, `valid(authorization)`, `revoke()`), `FakeState(*, check, receiver, clock, public_url)` with `script`, `received`, `directory`, `approvals`, `receiver_requires_auth`, `receiver_rncs`, `tamper_arecf`, `dgii`, `receiver_credentials`, `token_for_test()`. `fake_dgii.emitter`: `type Handler`, `ECF_FORMATS`, `NOT_A_FILE`, `BAD_SIGNATURE`, `SUMMARY_ONLY`, `SUMMARY_TOO_LARGE`, `schema_problems(format, document)`, `fields(document, *paths)`, `upload(request)`, `readable(document)`, `bad_request(message)`, `utc(moment)`, `seed_answer(value, at)`, `validate_seed(request, check, credentials)`, `EmitterServices(state)` with `handlers()`. `fake_dgii.app`: `ENVIRONMENTS`, `DEFAULT_PUBLIC_URL` (`http://127.0.0.1:8471`), `FakeDgii(*, check, receiver, clock=None, public_url=DEFAULT_PUBLIC_URL)` with `state`, `app`, `script`, `received`.

- [ ] **Step 1: Dependencies**

`countries/do/testing/pyproject.toml`:

```toml
dependencies = [
    "cryptography>=50.0.2",
    "ledger-country-do",
    "lxml>=6.1.3",
    "pydantic>=2.14.1",
    "python-multipart>=0.0.32",
    "signxml>=5.1.0",
    "starlette>=1.7.0",
    "testcontainers>=4.15.0",
    "xmlschema>=4.3.2",
]

[tool.deptry.per_rule_ignores]
DEP002 = ["python-multipart"]  # Starlette's form parser imports it; nothing here does by name
```

Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/testing/tests/conftest.py`:

```python
"""Fixtures of the testing distribution's tests: a test hierarchy, a signer, a fake DGII over ASGI."""

from datetime import UTC, datetime
lazy from collections.abc import AsyncIterator

import httpx
import pytest

from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.state import SignxmlCheck
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Authority, Issued, organization, person


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root()


@pytest.fixture(scope="session")
def issued(authority: Authority) -> Issued:
    return authority.issue(person())


@pytest.fixture(scope="session")
def receiver_identity(authority: Authority) -> Issued:
    return authority.issue(organization("131098193", name="CONSTRUCTORA DEL CARIBE SRL"))


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 10, 13, 13, 30, tzinfo=UTC))


@pytest.fixture
def fake(issued: Issued, receiver_identity: Issued, clock: ManualClock) -> FakeDgii:
    return FakeDgii(check=SignxmlCheck(trusted=[issued.certificate]), receiver=receiver_identity, clock=clock)


@pytest.fixture
async def http(fake: FakeDgii) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url=DEFAULT_PUBLIC_URL) as client:
        yield client
```

`countries/do/testing/tests/test_fake_dgii_emitter.py`:

```python
from datetime import timedelta
from typing import Final

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.answers import json_list, json_object, objects
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Authority, person
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice

AUTH: Final = "/testecf/autenticacion/api/autenticacion"
RECEPTION: Final = "/testecf/recepcion/api/facturaselectronicas"
RESULT: Final = "/testecf/consultaresultado/api/consultas/estado"
RFCE: Final = "/testecf/recepcionfc/api/recepcion/ecf"


def xml(document: bytes, name: str = "101850043E310000000001.xml") -> dict[str, tuple[str, bytes, str]]:
    return {"xml": (name, document, "text/xml")}


async def token(http: httpx.AsyncClient, signer: KeyPairSigner, environment: str = "testecf") -> str:
    seed = (await http.get(f"/{environment}/autenticacion/api/autenticacion/semilla")).content
    answer = await http.post(
        f"/{environment}/autenticacion/api/autenticacion/validarsemilla", files=xml(await signer.sign_seed(seed))
    )
    assert answer.status_code == 200, answer.text
    return str(json_object(answer)["token"])


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def signed_credit_invoice(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


async def test_a_signed_seed_buys_a_token_valid_an_hour(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    seed = await http.get(f"{AUTH}/semilla")
    assert seed.headers["content-type"].startswith("text/xml")
    answer = await http.post(f"{AUTH}/validarsemilla", files=xml(await signer.sign_seed(seed.content)))
    body = json_object(answer)
    assert (body["expedido"], body["expira"]) == ("2026-10-13T13:30:00Z", "2026-10-13T14:30:00Z")


async def test_a_seed_serves_once_and_for_five_minutes(
    http: httpx.AsyncClient, signer: KeyPairSigner, clock: ManualClock
) -> None:
    signed = await signer.sign_seed((await http.get(f"{AUTH}/semilla")).content)
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(signed))).status_code == 200
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(signed))).status_code == 400
    late = await signer.sign_seed((await http.get(f"{AUTH}/semilla")).content)
    clock.advance(timedelta(minutes=6))
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(late))).status_code == 400


async def test_a_seed_signed_by_a_stranger_or_not_xml_buys_nothing(http: httpx.AsyncClient) -> None:
    stranger = KeyPairSigner.from_issued(Authority.root().issue(person()))
    signed = await stranger.sign_seed((await http.get(f"{AUTH}/semilla")).content)
    refused = await http.post(f"{AUTH}/validarsemilla", files=xml(signed))
    assert (refused.status_code, json_object(refused)["mensaje"]) == (400, "La firma del XML no es válida")
    assert (await http.post(f"{AUTH}/validarsemilla", files=xml(b"not xml"))).status_code == 400
    assert (await http.post(f"{AUTH}/validarsemilla", data={"other": "x"})).status_code == 400


async def test_services_need_a_live_token(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    document = await signed_credit_invoice(signer)
    assert (await http.post(RECEPTION, files=xml(document))).status_code == 401
    assert (await http.post(RECEPTION, files=xml(document), headers={"Authorization": "Basic x"})).status_code == 401
    live = await token(http, signer)
    clock.advance(timedelta(hours=1))
    assert (await http.post(RECEPTION, files=xml(document), headers=bearer(live))).status_code == 401
    fresh = await token(http, signer)
    fake.state.dgii.revoke()
    assert (await http.post(RECEPTION, files=xml(document), headers=bearer(fresh))).status_code == 401


async def test_an_e_cf_is_received_with_a_track_id_and_accepted(http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    live = await token(http, signer)
    received = await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=bearer(live))
    track_id = str(json_object(received)["trackId"])
    result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
    assert (result["codigo"], result["estado"], result["eNCF"], result["secuenciaUtilizada"]) == (
        1,
        "Aceptado",
        "E310000000001",
        True,
    )
    (record,) = fake.received
    assert (record.kind, record.file_name, record.rnc) == ("ecf", "101850043E310000000001.xml", "101850043")


async def test_a_script_answers_en_proceso_then_aceptado_condicional(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.by_encf["E310000000002"] = Scripted(
        status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Monto fuera de tolerancia"),), processing=2
    )
    live = await token(http, signer)
    received = await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer, 2)), headers=bearer(live))
    track_id = str(json_object(received)["trackId"])
    codes = [json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live))) for _ in range(3)]
    assert [answer["codigo"] for answer in codes] == [3, 3, 4]
    assert codes[2]["mensajes"] == [{"valor": "Monto fuera de tolerancia", "codigo": 613}]


async def test_what_breaks_the_schema_or_the_signature_is_rejected_and_the_number_released(
    http: httpx.AsyncClient, signer: KeyPairSigner
) -> None:
    live = await token(http, signer)
    broken = (await signed_credit_invoice(signer)).replace(b"<TipoIngresos>01</TipoIngresos>", b"")
    unsigned_draft = credit_invoice(3)
    unsigned = ecf.render(unsigned_draft, compute_totals(unsigned_draft), signed_at=SIGNED_AT)
    stranger = KeyPairSigner.from_issued(Authority.root().issue(person()))
    by_a_stranger = await signed_credit_invoice(stranger, 4)
    for document, code in ((broken, 1), (unsigned, 1), (by_a_stranger, 2)):
        track_id = str(json_object(await http.post(RECEPTION, files=xml(document), headers=bearer(live)))["trackId"])
        result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
        assert (result["codigo"], result["secuenciaUtilizada"]) == (2, False)
        assert objects(result["mensajes"])[0]["codigo"] == code


async def test_a_scripted_rejection_says_whether_the_number_is_reusable(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    live = await token(http, signer)
    track_id = str(json_object(await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=bearer(live)))["trackId"])
    result = json_object(await http.get(RESULT, params={"trackid": track_id}, headers=bearer(live)))
    assert (result["codigo"], result["secuenciaUtilizada"]) == (2, False)


async def test_an_unknown_track_id_is_no_encontrado(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    result = json_object(await http.get(RESULT, params={"trackid": "nope"}, headers=bearer(await token(http, signer))))
    assert (result["codigo"], result["estado"]) == (0, "No encontrado")


async def test_reception_refuses_what_is_not_an_e_cf_it_takes(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    live = bearer(await token(http, signer))
    assert (await http.post(RECEPTION, files=xml(b"<a>"), headers=live)).status_code == 400
    other = (await signed_credit_invoice(signer)).replace(b"<TipoeCF>31</TipoeCF>", b"<TipoeCF>99</TipoeCF>")
    assert (await http.post(RECEPTION, files=xml(other), headers=live)).status_code == 400
    small_draft = consumer_invoice()
    small = await signer.sign_document(ecf.render(small_draft, compute_totals(small_draft), signed_at=SIGNED_AT))
    refused = await http.post(RECEPTION, files=xml(small), headers=live)
    assert (refused.status_code, json_object(refused)["trackId"]) == (400, None)


async def test_track_ids_of_an_e_ncf_except_in_certecf(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    live = bearer(await token(http, signer))
    await http.post(RECEPTION, files=xml(await signed_credit_invoice(signer)), headers=live)
    params = {"rncemisor": "101850043", "encf": "E310000000001"}
    found = json_list(await http.get("/testecf/consultatrackids/api/trackids/consulta", params=params, headers=live))
    assert [entry["estado"] for entry in found] == ["Aceptado"]


async def test_certecf_has_no_track_id_query(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    live = bearer(await token(http, signer, "certecf"))
    missing = await http.get("/certecf/consultatrackids/api/trackids/consulta", params={"encf": "x"}, headers=live)
    assert missing.status_code == 404


async def test_an_rfce_is_decided_at_once_on_the_fc_host(http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    live = bearer(await token(http, signer))
    draft = consumer_invoice()
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await http.post(f"https://fc.dgii.gov.do{RFCE}", files=xml(summary, "101850043E320000000001.xml"), headers=live)
    body = json_object(answer)
    assert (body["codigo"], body["estado"], body["encf"], body["secuenciaUtilizada"]) == (1, "Aceptado", "E320000000001", True)
    assert "trackId" not in body
    assert fake.received[-1].host == "fc.dgii.gov.do"
    query = {"RNC_Emisor": "101850043", "ENCF": "E320000000001", "Cod_Seguridad_eCF": "dcp79q"}
    status = json_object(await http.get("/testecf/consultarfce/api/Consultas/Consulta", params=query, headers=live))
    assert (status["codigo"], status["estado"]) == ("1", "Aceptado")
    wrong = json_object(
        await http.get("/testecf/consultarfce/api/consultas/consulta", params={**query, "Cod_Seguridad_eCF": "x"}, headers=live)
    )
    assert wrong["codigo"] == "0"


async def test_an_rfce_of_the_threshold_or_more_is_rejected(http: httpx.AsyncClient, signer: KeyPairSigner) -> None:
    live = bearer(await token(http, signer))
    draft = consumer_invoice(large=True)
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    body = json_object(await http.post(RFCE, files=xml(summary), headers=live))
    assert (body["codigo"], body["secuenciaUtilizada"]) == (2, True)
    assert (await http.post(RFCE, files=xml(b"x"), headers=live)).status_code == 400


async def test_outages_unknown_paths_and_dgii_s_case_insensitivity(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.script.outage = 2
    assert [(await http.get(f"{AUTH}/semilla")).status_code for _ in range(3)] == [503, 503, 200]
    assert (await http.get("/prod/autenticacion/api/autenticacion/semilla")).status_code == 404
    assert (await http.get("/testecf/nothing/here")).status_code == 404
    assert (await http.get("/TesteCF/Autenticacion/API/Autenticacion/Semilla")).status_code == 200
    fake.script.outage = -1
    assert (await http.get(f"{AUTH}/semilla")).status_code == 503
    fake.script.outage = 0
    assert fake.state.token_for_test()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries/do/testing/tests/test_fake_dgii_emitter.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do_testing.clock'`.

- [ ] **Step 4: Implement**

`countries/do/testing/src/ledger_country_do_testing/clock.py`:

```python
"""A clock a test moves by hand."""

from typing import final
lazy from datetime import datetime, timedelta


@final
class ManualClock:
    """``clock()`` is ``now`` until ``advance`` moves it."""

    __slots__ = ("now",)

    def __init__(self, now: datetime) -> None:
        self.now: datetime = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward by ``delta``."""
        self.now += delta
```

`countries/do/testing/src/ledger_country_do_testing/answers.py`:

```python
"""JSON answers read typed, for tests and the spike CLI (``response.json()`` is ``Any``)."""

from typing import Final

from pydantic import TypeAdapter
lazy import httpx

_OBJECT: Final = TypeAdapter(dict[str, object])
_LIST: Final = TypeAdapter(list[dict[str, object]])


def json_object(response: httpx.Response) -> dict[str, object]:
    """``response``'s body, a JSON object."""
    return _OBJECT.validate_json(response.content)


def json_list(response: httpx.Response) -> list[dict[str, object]]:
    """``response``'s body, a JSON list of objects."""
    return _LIST.validate_json(response.content)


def objects(value: object) -> list[dict[str, object]]:
    """A value of an answer already read, known to be a list of objects (DGII's ``mensajes``)."""
    return _LIST.validate_python(value)
```

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/__init__.py`:

```python
"""A fake of DGII's web services and of DGII's emisor-receptor simulator, written from DGII's documentation."""
```

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/state.py`:

```python
"""What the fake DGII holds, what it trusts, and how a test scripts its answers."""

import secrets
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from enum import IntEnum
from types import MappingProxyType
from typing import Final, Protocol, final, override
lazy from collections.abc import Callable, Collection, Mapping
lazy from datetime import datetime

lazy from cryptography import x509

from ledger_country_do_testing.signatures import verify_with_signxml
lazy from ledger_country_do_testing.pki import Issued

TOKEN_LIFETIME: Final = timedelta(hours=1)
"""DT-DGII: a token lasts one hour."""
SEED_LIFETIME: Final = timedelta(minutes=5)
"""The fake's choice (DGII does not document one); the receiver of spec §3.8 uses the same."""


class Status(IntEnum):
    """DGII's result codes (DT-DGII, "Consulta de resultado e-CF")."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


STATUS_WORDS: Final[Mapping[Status, str]] = MappingProxyType(
    {
        Status.NOT_FOUND: "No encontrado",
        Status.ACCEPTED: "Aceptado",
        Status.REJECTED: "Rechazado",
        Status.IN_PROCESS: "En Proceso",
        Status.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
    }
)


class SignatureCheck(Protocol, metaclass=ABCMeta):
    """Whether a document's enveloped signature holds, by a signer the fake trusts."""

    @abstractmethod
    def verify(self, signed: bytes) -> bool:
        """Whether ``signed``'s signature holds."""


@final
class SignxmlCheck(SignatureCheck):
    """signxml's verdict (``signatures.verify_with_signxml``); ``trusted`` ``None`` trusts any signer."""

    __slots__ = ("_trusted",)

    def __init__(self, *, trusted: Collection[x509.Certificate] | None) -> None:
        self._trusted = trusted

    @override
    def verify(self, signed: bytes) -> bool:
        return verify_with_signxml(signed, trusted=self._trusted)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Scripted:
    """What the fake answers for one e-NCF."""

    status: Status = Status.ACCEPTED
    messages: tuple[tuple[str, str], ...] = ()
    """``(code, text)`` pairs, as DGII words its messages."""
    processing: int = 0
    """How many result queries answer "En Proceso" first."""
    sequence_used: bool = True
    """DGII's ``secuenciaUtilizada``: ``False`` releases the number of a rejected document."""


@final
@dataclass(slots=True)
class Script:
    """What the fake answers, as a test sets it."""

    by_encf: dict[str, Scripted] = field(default_factory=dict[str, Scripted])
    default: Scripted = field(default_factory=Scripted)
    outage: int = 0
    """How many of the next requests answer 503; negative: every one, until set back to 0."""


@final
@dataclass(slots=True, kw_only=True)
class Received:
    """A document the fake received, and how it answers about it."""

    track_id: str | None
    environment: str
    host: str
    kind: str
    """``ecf`` (reception), ``rfce``, or ``buyer`` (the emisor-receptor simulator)."""
    file_name: str
    rnc: str
    encf: str
    security_code: str
    document: bytes = field(repr=False)
    received_at: datetime
    status: Status
    messages: tuple[tuple[str, str], ...]
    polls_left: int
    sequence_used: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryEntry:
    """A taxpayer of DGII's directory of electronic receivers (DT-DGII, "Consulta directorio")."""

    name: str
    rnc: str
    reception: str
    approval: str
    authentication: str | None


@final
class Credentials:
    """The seeds one authority (DGII, or a receiver) hands out and the tokens it issues."""

    __slots__ = ("_clock", "_seeds", "_tokens")

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._seeds: dict[str, datetime] = {}
        self._tokens: dict[str, datetime] = {}

    def seed(self) -> tuple[str, datetime]:
        """A new seed's value and the moment it was made."""
        value, now = secrets.token_urlsafe(36), self._clock()
        self._seeds[value] = now + SEED_LIFETIME
        return value, now

    def take_seed(self, value: str) -> bool:
        """Whether ``value`` is a seed handed out and not yet expired; it serves once."""
        expires = self._seeds.pop(value, None)
        return expires is not None and expires >= self._clock()

    def token(self) -> tuple[str, datetime, datetime]:
        """A new token, when it was issued and when it expires."""
        value, now = secrets.token_urlsafe(32), self._clock()
        self._tokens[value] = now + TOKEN_LIFETIME
        return value, now, now + TOKEN_LIFETIME

    def valid(self, authorization: str) -> bool:
        """Whether an ``Authorization`` header carries a live bearer token."""
        scheme, _, value = authorization.partition(" ")
        expires = self._tokens.get(value) if scheme.lower() == "bearer" else None
        return expires is not None and expires > self._clock()

    def revoke(self) -> None:
        """Forget every token issued."""
        self._tokens.clear()


@final
class FakeState:
    """Everything the fake holds; tests read and set it."""

    __slots__ = (
        "approvals",
        "check",
        "clock",
        "dgii",
        "directory",
        "public_url",
        "received",
        "receiver",
        "receiver_credentials",
        "receiver_requires_auth",
        "receiver_rncs",
        "script",
        "tamper_arecf",
    )

    def __init__(
        self, *, check: SignatureCheck, receiver: Issued, clock: Callable[[], datetime], public_url: str
    ) -> None:
        self.check: SignatureCheck = check
        self.receiver: Issued = receiver
        """The certificate the emisor-receptor simulator signs its ARECFs with."""
        self.clock: Callable[[], datetime] = clock
        self.public_url: str = public_url
        """Where the fake is reached from outside: the directory's URLs start with it."""
        self.script: Script = Script()
        self.received: list[Received] = []
        self.directory: dict[str, DirectoryEntry] = {}
        self.approvals: list[bytes] = []
        self.receiver_requires_auth: bool = True
        self.receiver_rncs: frozenset[str] | None = None
        """The buyers the simulator receives for; ``None``: any."""
        self.tamper_arecf: bool = False
        """Answer ARECFs whose signature does not hold (for the buyer client's tests)."""
        self.dgii: Credentials = Credentials(clock)
        self.receiver_credentials: Credentials = Credentials(clock)

    def token_for_test(self) -> str:
        """A live DGII token, as if a seed had been validated."""
        return self.dgii.token()[0]
```

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/emitter.py`:

```python
"""DGII's services for emitters, answered as DT-DGII (02-01-2026) documents them.

* ``GET {env}/autenticacion/api/autenticacion/semilla``: a ``SemillaModel``; ``POST .../validarsemilla`` takes it
  back signed (multipart ``xml``) and answers ``{token, expira, expedido}`` (one hour).
* ``POST {env}/recepcion/api/facturaselectronicas`` (bearer): ``{trackId, error, mensaje}``; a type 32 below
  RD$250,000 is refused there (F3: the fake answers 400).
* ``GET {env}/consultaresultado/api/consultas/estado?trackid=`` (bearer): ``{trackId, codigo, estado, rnc, eNCF,
  secuenciaUtilizada, fechaRecepcion, mensajes}``; ``GET {env}/consultatrackids/api/trackids/consulta`` (bearer;
  not in CerteCF).
* ``POST {env}/recepcionfc/api/recepcion/ecf`` (bearer; DGII's ``fc`` host): decided at once, ``{codigo, estado,
  mensajes, encf, secuenciaUtilizada}``, no track id; ``GET {env}/consultarfce/api/consultas/consulta``.

A fake with teeth: each document is checked against DGII's XSD by ``xmlschema`` (sharing no code with the pack's
libxml2 validation) and its signature by a ``SignatureCheck`` (signxml); a document that fails is rejected with
``secuenciaUtilizada = false``, as DGII rejects it. Otherwise it answers what the ``Script`` says.
"""

import secrets
from datetime import UTC
from decimal import Decimal, InvalidOperation
from functools import cache
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import datetime

import xmlschema
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse, Response
lazy from starlette.requests import Request

from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import UnreadableDocumentError, texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import corrected
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do_testing.fake_dgii.state import STATUS_WORDS, Received, Status
lazy from ledger_country_do_testing.fake_dgii.state import Credentials, FakeState, SignatureCheck

type Handler = Callable[[Request, str], Awaitable[Response]]

WITHOUT_TRACK_IDS: Final = "certecf"
RFCE_THRESHOLD: Final = Decimal("250000.00")
NOT_A_FILE: Final = "Tipo de archivo no válido, favor proveer un XML"
BAD_SIGNATURE: Final = "La firma del XML no es válida"
NOT_A_TYPE: Final = "El tipo de comprobante no es válido"
SUMMARY_ONLY: Final = "Las facturas de consumo menores a RD$250,000.00 se envían como resumen (RFCE)"
SUMMARY_TOO_LARGE: Final = "Las facturas de consumo de RD$250,000.00 o más se envían completas por recepción"
ECF_FORMATS: Final = {
    "31": Format.ECF_31,
    "32": Format.ECF_32,
    "33": Format.ECF_33,
    "34": Format.ECF_34,
    "41": Format.ECF_41,
    "43": Format.ECF_43,
    "44": Format.ECF_44,
    "45": Format.ECF_45,
    "46": Format.ECF_46,
    "47": Format.ECF_47,
}
_UTC: Final = "%Y-%m-%dT%H:%M:%SZ"
_REPORTED: Final = 5


@cache
def _schema(format_: Format) -> xmlschema.XMLSchema10:
    return xmlschema.XMLSchema10(corrected(format_).decode("utf-8"))


def schema_problems(format_: Format, document: bytes) -> tuple[tuple[str, str], ...]:
    """xmlschema's objections to ``document``, as DGII-like messages (code 1), at most five."""
    errors = list(_schema(format_).iter_errors(document.decode("utf-8")))[:_REPORTED]
    return tuple(("1", f"El XML no cumple con el XSD: {error.path}") for error in errors)


def fields(document: bytes, *paths: str) -> tuple[str, ...]:
    """The text at each path, stripped; empty where absent."""
    return tuple((found or "").strip() for found in texts(document, *paths))


def readable(document: bytes) -> bool:
    """Whether ``document`` is XML the hardened parser reads."""
    try:
        texts(document)
    except UnreadableDocumentError:
        return False
    return True


async def upload(request: Request) -> tuple[bytes, str] | None:
    """The multipart field ``xml`` (DGII's one input) and its file name; ``None`` without it."""
    async with request.form() as form:
        found = form.get("xml")
        if not isinstance(found, UploadFile):
            return None
        return await found.read(), found.filename or ""


def bad_request(message: str) -> Response:
    """DGII's 400 answer: ``{trackId: null, error, mensaje}``."""
    return JSONResponse({"trackId": None, "error": "400", "mensaje": message}, status_code=HTTPStatus.BAD_REQUEST)


def utc(moment: datetime) -> str:
    """DGII's token dates: UTC, ``yyyy-MM-ddTHH:mm:ssZ``."""
    return moment.astimezone(UTC).strftime(_UTC)


def seed_answer(value: str, at: datetime) -> Response:
    """A ``SemillaModel`` as DGII writes it."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SemillaModel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<valor>{value}</valor><fecha>{at.astimezone(SANTO_DOMINGO).isoformat()}</fecha></SemillaModel>"
    )
    return Response(body, media_type="text/xml")


async def validate_seed(request: Request, check: SignatureCheck, credentials: Credentials) -> Response:
    """A signed seed exchanged for a token (DGII's ``validarsemilla``, a receiver's ``validacioncertificado``)."""
    sent = await upload(request)
    if sent is None or not readable(sent[0]) or schema_problems(Format.SEED, sent[0]):
        return bad_request(NOT_A_FILE)
    (value,) = fields(sent[0], "valor")
    if not credentials.take_seed(value) or not check.verify(sent[0]):
        return bad_request(BAD_SIGNATURE)
    token, issued_at, expires = credentials.token()
    return JSONResponse({"token": token, "expira": utc(expires), "expedido": utc(issued_at)})


def _below_threshold(total: str) -> bool:
    try:
        return Decimal(total) < RFCE_THRESHOLD
    except InvalidOperation:
        return False


def _messages(received: Received) -> list[dict[str, object]]:
    return [{"valor": text, "codigo": int(code) if code.isdigit() else code} for code, text in received.messages]


@final
class EmitterServices:
    """DGII's emitter-facing services over a ``FakeState`` (see the module)."""

    __slots__ = ("_state",)

    def __init__(self, state: FakeState) -> None:
        self._state = state

    def handlers(self) -> dict[tuple[str, str], Handler]:
        """Each service's handler by method and path after the environment's segment (lower case)."""
        return {
            ("GET", "autenticacion/api/autenticacion/semilla"): self._seed,
            ("POST", "autenticacion/api/autenticacion/validarsemilla"): self._validate,
            ("POST", "recepcion/api/facturaselectronicas"): self._closed(self._reception),
            ("GET", "consultaresultado/api/consultas/estado"): self._closed(self._result),
            ("GET", "consultatrackids/api/trackids/consulta"): self._closed(self._track_ids),
            ("POST", "recepcionfc/api/recepcion/ecf"): self._closed(self._summary),
            ("GET", "consultarfce/api/consultas/consulta"): self._closed(self._summary_status),
        }

    def _closed(self, handler: Handler) -> Handler:
        async def authorized(request: Request, environment: str) -> Response:
            if not self._state.dgii.valid(request.headers.get("authorization", "")):
                return Response(status_code=HTTPStatus.UNAUTHORIZED)
            return await handler(request, environment)

        return authorized

    async def _seed(self, request: Request, environment: str) -> Response:
        del request, environment
        return seed_answer(*self._state.dgii.seed())

    async def _validate(self, request: Request, environment: str) -> Response:
        del environment
        return await validate_seed(request, self._state.check, self._state.dgii)

    def record(self, request: Request, environment: str, *, kind: str, format_: Format, sent: tuple[bytes, str]) -> Received:
        """``sent`` checked (schema, signature), scripted, and remembered."""
        document, file_name = sent
        problems = schema_problems(format_, document)
        if not problems and not self._state.check.verify(document):
            problems = (("2", BAD_SIGNATURE),)
        encf, rnc, code = fields(
            document, "Encabezado/IdDoc/eNCF", "Encabezado/Emisor/RNCEmisor", "Encabezado/CodigoSeguridadeCF"
        )
        scripted = self._state.script.by_encf.get(encf, self._state.script.default)
        rejected = bool(problems)
        received = Received(
            track_id=secrets.token_hex(16) if kind == "ecf" else None,
            environment=environment,
            host=request.headers.get("host", ""),
            kind=kind,
            file_name=file_name,
            rnc=rnc,
            encf=encf,
            security_code=code,
            document=document,
            received_at=self._state.clock(),
            status=Status.REJECTED if rejected else scripted.status,
            messages=problems or scripted.messages,
            polls_left=0 if rejected else scripted.processing,
            sequence_used=not rejected and scripted.sequence_used,
        )
        self._state.received.append(received)
        return received

    async def _reception(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        ecf_type, total = fields(sent[0], "Encabezado/IdDoc/TipoeCF", "Encabezado/Totales/MontoTotal")
        format_ = ECF_FORMATS.get(ecf_type)
        if format_ is None:
            return bad_request(NOT_A_TYPE)
        if format_ is Format.ECF_32 and _below_threshold(total):
            return bad_request(SUMMARY_ONLY)
        received = self.record(request, environment, kind="ecf", format_=format_, sent=sent)
        return JSONResponse({"trackId": received.track_id, "error": None, "mensaje": None})

    async def _result(self, request: Request, environment: str) -> Response:
        del environment
        track_id = request.query_params.get("trackid", "")
        found = next((each for each in self._state.received if each.track_id == track_id), None)
        if found is None:
            words = STATUS_WORDS[Status.NOT_FOUND]
            return JSONResponse({"trackId": track_id, "codigo": 0, "estado": words, "mensajes": []})
        status = found.status
        if found.polls_left:
            found.polls_left -= 1
            status = Status.IN_PROCESS
        settled = status is not Status.IN_PROCESS
        return JSONResponse(
            {
                "trackId": track_id,
                "codigo": int(status),
                "estado": STATUS_WORDS[status],
                "rnc": found.rnc,
                "eNCF": found.encf,
                "secuenciaUtilizada": found.sequence_used if settled else None,
                "fechaRecepcion": found.received_at.isoformat(),
                "mensajes": _messages(found) if settled else [],
            }
        )

    async def _track_ids(self, request: Request, environment: str) -> Response:
        if environment == WITHOUT_TRACK_IDS:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        wanted = ("ecf", request.query_params.get("rncemisor", ""), request.query_params.get("encf", ""))
        found = [each for each in self._state.received if (each.kind, each.rnc, each.encf) == wanted]
        return JSONResponse(
            [
                {"trackId": each.track_id, "estado": STATUS_WORDS[each.status], "fechaRecepcion": each.received_at.isoformat()}
                for each in found
            ]
        )

    async def _summary(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        (total,) = fields(sent[0], "Encabezado/Totales/MontoTotal")
        received = self.record(request, environment, kind="rfce", format_=Format.RFCE, sent=sent)
        if received.status is not Status.REJECTED and not _below_threshold(total):
            received.status, received.messages, received.sequence_used = (
                Status.REJECTED,
                (("3", SUMMARY_TOO_LARGE),),
                True,
            )
        return JSONResponse(
            {
                "codigo": int(received.status),
                "estado": STATUS_WORDS[received.status],
                "mensajes": [{"codigo": code, "valor": text} for code, text in received.messages],
                "encf": received.encf,
                "secuenciaUtilizada": received.sequence_used,
            }
        )

    async def _summary_status(self, request: Request, environment: str) -> Response:
        del environment
        rnc, encf = request.query_params.get("RNC_Emisor", ""), request.query_params.get("ENCF", "")
        code = request.query_params.get("Cod_Seguridad_eCF", "")
        found = next(
            (
                each
                for each in self._state.received
                if (each.kind, each.rnc, each.encf, each.security_code) == ("rfce", rnc, encf, code)
            ),
            None,
        )
        status = Status.NOT_FOUND if found is None else found.status
        return JSONResponse(
            {
                "rnc": rnc,
                "encf": encf,
                "secuenciaUtilizada": None if found is None else found.sequence_used,
                "codigo": str(int(status)),
                "estado": STATUS_WORDS[status],
                "mensajes": [] if found is None else _messages(found),
            }
        )
```

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/app.py` (Task 10 adds the receiver's handlers and `create_app`):

```python
"""The fake DGII as one ASGI application, for tests (in process, over ``httpx.ASGITransport``) and by hand.

Every request is routed by its lower-cased path, as DGII's services ignore case (DT-EMI): the first segment is
the environment (``testecf``, ``certecf``, ``ecf``), the rest names the service. One application answers for
both of DGII's hosts (``ecf.dgii.gov.do`` and ``fc.dgii.gov.do``): their paths never collide, and the ``Host``
each request was sent to is recorded.
"""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import Callable

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
lazy from starlette.requests import Request

from ledger_country_do_testing.fake_dgii.emitter import EmitterServices
from ledger_country_do_testing.fake_dgii.state import FakeState
lazy from ledger_country_do_testing.fake_dgii.emitter import Handler
lazy from ledger_country_do_testing.fake_dgii.state import Received, Script, SignatureCheck
lazy from ledger_country_do_testing.pki import Issued

ENVIRONMENTS: Final = frozenset({"testecf", "certecf", "ecf"})
DEFAULT_PUBLIC_URL: Final = "http://127.0.0.1:8471"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@final
class FakeDgii:
    """The fake (see the module); ``app`` is the ASGI application, ``state`` what tests read and set."""

    __slots__ = ("_handlers", "app", "state")

    def __init__(
        self,
        *,
        check: SignatureCheck,
        receiver: Issued,
        clock: Callable[[], datetime] | None = None,
        public_url: str = DEFAULT_PUBLIC_URL,
    ) -> None:
        self.state: FakeState = FakeState(
            check=check, receiver=receiver, clock=clock or _utc_now, public_url=public_url
        )
        self._handlers: dict[tuple[str, str], Handler] = EmitterServices(self.state).handlers()
        self.app: Starlette = Starlette(routes=[Route("/{path:path}", self._dispatch, methods=["GET", "POST"])])

    @property
    def script(self) -> Script:
        """What the fake answers."""
        return self.state.script

    @property
    def received(self) -> list[Received]:
        """Every document received, in order."""
        return self.state.received

    async def _dispatch(self, request: Request) -> Response:
        environment, _, service = request.url.path.lower().strip("/").partition("/")
        handler = self._handlers.get((request.method, service))
        if environment not in ENVIRONMENTS or handler is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        outage = self.state.script.outage
        if outage:
            self.state.script.outage = outage - 1 if outage > 0 else outage
            return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
        return await handler(request, environment)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/testing/tests/test_fake_dgii_emitter.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock countries/do/testing
git commit -m "test(do): a fake DGII from DGII's documentation: seed and token, reception, results, RFCE

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The fake DGII's directory, emisor-receptor simulator (signed ARECF), and `ledger-fake-dgii`

**Files:**
- Create: `countries/do/testing/src/ledger_country_do_testing/fake_dgii/{receiver, serve}.py`
- Modify: `countries/do/testing/src/ledger_country_do_testing/fake_dgii/app.py` (merge the receiver's handlers; `create_app`), `countries/do/testing/pyproject.toml` (dependency `"granian>=2.8.4"`; `[project.scripts] ledger-fake-dgii = "ledger_country_do_testing.fake_dgii.serve:main"`)
- Test: `countries/do/testing/tests/test_fake_dgii_receiver.py`, `countries/do/testing/tests/test_fake_dgii_serve.py`

**Interfaces:**
- Consumes: Task 9's `emitter` helpers and `state`; `signatures.sign_with_signxml` (Task 6); `pki` (Task 1).
- Produces: `fake_dgii.receiver`: `EMISOR_RECEPTOR` (`"emisorreceptor"`), `arecf(issuer_rnc, buyer_rnc, encf, *, reason, at) -> bytes`, `ReceiverServices(state)` with `handlers()` (`consultadirectorio/...` and `emisorreceptor/fe/...`). `fake_dgii.app`: `create_app() -> Starlette` (reads `LEDGER_FAKE_DGII_PUBLIC_URL`, default `DEFAULT_PUBLIC_URL`). `fake_dgii.serve`: `DEFAULT_HOST` (`127.0.0.1`), `DEFAULT_PORT` (8471), `RESERVED_PORT` (8000), `main(argv=None)`.

- [ ] **Step 1: Dependencies**

`countries/do/testing/pyproject.toml`: dependencies gain `"granian>=2.8.4"`, and:

```toml
[project.scripts]
ledger-fake-dgii = "ledger_country_do_testing.fake_dgii.serve:main"
```

Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/testing/tests/test_fake_dgii_receiver.py`:

```python
from typing import Final

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import SignatureInvalidError, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import violations
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.answers import json_list, json_object
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.state import DirectoryEntry
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import Issued
from ledger_country_do_testing.samples import SIGNED_AT, credit_invoice

SIMULATOR: Final = "/testecf/emisorreceptor/fe"
ACECF: Final = (
    b'<?xml version="1.0" encoding="utf-8"?><ACECF><DetalleAprobacionComercial><Version>1.0</Version>'
    b"<RNCEmisor>101850043</RNCEmisor><eNCF>E310000000001</eNCF><FechaEmision>13-10-2026</FechaEmision>"
    b"<MontoTotal>4887.58</MontoTotal><RNCComprador>131098193</RNCComprador><Estado>1</Estado>"
    b"<FechaHoraAprobacionComercial>13-10-2026 10:00:00</FechaHoraAprobacionComercial>"
    b"</DetalleAprobacionComercial></ACECF>"
)


def xml(document: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"xml": ("101850043E310000000001.xml", document, "text/xml")}


async def receiver_token(http: httpx.AsyncClient, signer: KeyPairSigner) -> dict[str, str]:
    seed = (await http.get(f"{SIMULATOR}/autenticacion/api/semilla")).content
    answer = await http.post(f"{SIMULATOR}/autenticacion/api/validacioncertificado", files=xml(await signer.sign_seed(seed)))
    return {"Authorization": f"Bearer {json_object(answer)['token']}"}


async def signed_credit_invoice(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


async def test_testecf_s_directory_answers_dgii_s_simulator_for_any_rnc(http: httpx.AsyncClient, fake: FakeDgii) -> None:
    (entry,) = json_list(await http.get("/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "131098193"}))
    base = f"{DEFAULT_PUBLIC_URL}/testecf/emisorreceptor"
    assert entry == {"nombre": "EMISOR RECEPTOR DGII", "rnc": "131098193", "urlRecepcion": base, "urlAceptacion": base, "urlOpcional": base}
    fake.state.receiver_requires_auth = False
    (open_entry,) = json_list(await http.get("/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "1"}))
    assert open_entry["urlOpcional"] is None


async def test_in_production_only_registered_receivers_are_listed_and_certecf_has_no_directory(
    http: httpx.AsyncClient, fake: FakeDgii
) -> None:
    fake.state.directory["131098193"] = DirectoryEntry(
        name="CONSTRUCTORA DEL CARIBE SRL", rnc="131098193", reception="https://r.example/a", approval="https://r.example/a", authentication=None
    )
    by_rnc = "/ecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc"
    assert json_list(await http.get(by_rnc, params={"RNC": "101850043"})) == []
    assert [entry["rnc"] for entry in json_list(await http.get(by_rnc, params={"RNC": "131098193"}))] == ["131098193"]
    assert len(json_list(await http.get("/ecf/consultadirectorio/api/consultas/listado"))) == 1
    assert (await http.get("/certecf/consultadirectorio/api/consultas/listado")).status_code == 404
    assert (await http.get(by_rnc.replace("/ecf/", "/certecf/"), params={"RNC": "1"})).status_code == 404


async def test_the_simulator_receives_an_e_cf_and_answers_a_signed_arecf(
    http: httpx.AsyncClient, signer: KeyPairSigner, receiver_identity: Issued, fake: FakeDgii
) -> None:
    answer = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(await signed_credit_invoice(signer)), headers=await receiver_token(http, signer))
    assert violations(Format.ARECF, answer.content) == ()
    assert verify_enveloped(answer.content).certificate == receiver_identity.certificate
    detail = "DetalleAcusedeRecibo/"
    found = texts(answer.content, f"{detail}RNCEmisor", f"{detail}RNCComprador", f"{detail}eNCF", f"{detail}Estado")
    assert found == ("101850043", "131098193", "E310000000001", "0")
    assert fake.received[-1].kind == "buyer"


async def test_the_simulator_says_why_it_did_not_receive(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    headers = await receiver_token(http, signer)
    document = await signed_credit_invoice(signer)
    reasons = []
    fake.state.receiver_rncs = frozenset({"131098193"})
    for sent in (document.replace(b"<TipoIngresos>01</TipoIngresos>", b""), document.replace(b"101850043", b"101850044"), document, document):
        answer = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(sent), headers=headers)
        reasons.append(texts(answer.content, "DetalleAcusedeRecibo/Estado", "DetalleAcusedeRecibo/CodigoMotivoNoRecibido"))
    assert reasons == [("1", "1"), ("1", "2"), ("0", None), ("1", "3")]
    fake.state.receiver_rncs = frozenset({"999999999"})
    other = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(await signed_credit_invoice(signer, 9)), headers=headers)
    assert texts(other.content, "DetalleAcusedeRecibo/CodigoMotivoNoRecibido") == ("4",)


async def test_the_simulator_needs_its_own_token_when_it_publishes_authentication(
    http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    document = await signed_credit_invoice(signer)
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document))).status_code == 401
    dgii_token = {"Authorization": f"Bearer {fake.state.token_for_test()}"}
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document), headers=dgii_token)).status_code == 401
    fake.state.receiver_requires_auth = False
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(document))).status_code == 200
    assert (await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(b"<a>"))).status_code == 400


async def test_a_tampered_arecf_does_not_verify(http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    fake.state.receiver_requires_auth = False
    fake.state.tamper_arecf = True
    answer = await http.post(f"{SIMULATOR}/recepcion/api/ecf", files=xml(await signed_credit_invoice(signer)))
    assert b"<Estado>1</Estado>" in answer.content
    with pytest.raises(SignatureInvalidError, match="changed after it was signed"):
        verify_enveloped(answer.content)


async def test_the_simulator_takes_a_signed_commercial_approval(http: httpx.AsyncClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    headers = await receiver_token(http, signer)
    approval = await signer.sign_document(ACECF)
    assert (await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(approval), headers=headers)).status_code == 200
    assert fake.state.approvals == [approval]
    assert (await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(ACECF), headers=headers)).status_code == 400
    assert (await http.post(f"{SIMULATOR}/aprobacioncomercial/api/ecf", files=xml(approval))).status_code == 401
```

`countries/do/testing/tests/test_fake_dgii_serve.py`:

```python
from typing import ClassVar, final

import httpx
import pytest

from ledger_country_do_testing.fake_dgii import serve
from ledger_country_do_testing.fake_dgii.app import create_app


@final
class Recorder:
    """Stands in for granian's server: records how it was built and that it was served."""

    built: ClassVar[list[dict[str, object]]] = []
    served: ClassVar[int] = 0

    def __init__(self, **options: object) -> None:
        Recorder.built.append(options)

    def serve(self) -> None:
        Recorder.served += 1


def test_the_fake_serves_on_loopback_8471_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder.built.clear()
    Recorder.served = 0
    monkeypatch.setattr(serve, "Granian", Recorder)
    serve.main([])
    serve.main(["--host", "0.0.0.0", "--port", "8472"])
    assert [(built["address"], built["port"], built["factory"]) for built in Recorder.built] == [
        ("127.0.0.1", 8471, True),
        ("0.0.0.0", 8472, True),
    ]
    assert Recorder.served == 2


def test_port_8000_is_never_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "Granian", Recorder)
    with pytest.raises(SystemExit) as caught:
        serve.main(["--port", "8000"])
    assert caught.value.code == 2


async def test_the_factory_builds_a_fake_that_trusts_any_signer_and_knows_its_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_FAKE_DGII_PUBLIC_URL", "http://127.0.0.1:9999")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1:9999") as http:
        seed = await http.get("/testecf/autenticacion/api/autenticacion/semilla")
        directory = await http.get("/testecf/consultadirectorio/api/consultas/obtenerdirectorioporrnc", params={"RNC": "131098193"})
    assert seed.status_code == 200
    assert b"http://127.0.0.1:9999/testecf/emisorreceptor" in directory.content
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries/do/testing/tests/test_fake_dgii_receiver.py countries/do/testing/tests/test_fake_dgii_serve.py -q --no-cov`
Expected: FAIL (`404` from the directory and the simulator; `ImportError: cannot import name 'serve'`).

- [ ] **Step 4: Implement**

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/receiver.py`:

```python
"""DGII's directory of electronic receivers and its emisor-receptor simulator, as DT-DGII and DT-EMI describe them.

* ``GET {env}/consultadirectorio/api/consultas/listado`` and ``.../obtenerdirectorioporrnc?RNC=``:
  ``[{nombre, rnc, urlRecepcion, urlAceptacion, urlOpcional}]``. In TesteCF DGII answers its own simulator's URLs
  ("se retornarán las URLs de prueba del servicio de emisor receptor habilitado por DGII"), so any RNC not
  registered with the fake resolves to it; in eCF only registered receivers exist; CerteCF has no directory
  (DT-DGII lists TesteCF and eCF only). The URLs are bases (F5).
* The simulator, under ``{env}/emisorreceptor``: ``GET /fe/autenticacion/api/semilla``, ``POST
  /fe/autenticacion/api/validacioncertificado`` (a token of its own, never DGII's), ``POST
  /fe/recepcion/api/ecf`` (bearer when it publishes authentication) answering a **signed ARECF** (*Formato Acuse
  de Recibo v1.0*: ``Estado`` 0 received, 1 not, with ``CodigoMotivoNoRecibido`` 1 specification error, 2 signature
  error, 3 duplicate, 4 buyer RNC not this receiver's), and ``POST /fe/aprobacioncomercial/api/ecf`` (a signed
  ACECF: 200, else 400).

ARECFs are written here as text and signed by signxml with the simulator's own certificate, so the pack's
verifier is tested against a signer that shares no code with its own.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Final, final
from xml.sax.saxutils import escape

from starlette.responses import JSONResponse, Response
lazy from starlette.requests import Request

from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
from ledger_country_do_testing.fake_dgii.emitter import (
    ECF_FORMATS,
    NOT_A_FILE,
    bad_request,
    fields,
    readable,
    schema_problems,
    seed_answer,
    upload,
    validate_seed,
)
from ledger_country_do_testing.fake_dgii.state import DirectoryEntry, Received, Status
from ledger_country_do_testing.signatures import sign_with_signxml
lazy from ledger_country_do_testing.fake_dgii.emitter import Handler
lazy from ledger_country_do_testing.fake_dgii.state import FakeState

EMISOR_RECEPTOR: Final = "emisorreceptor"
SIMULATOR_NAME: Final = "EMISOR RECEPTOR DGII"
WITHOUT_DIRECTORY: Final = "certecf"
"""DT-DGII publishes ``consultadirectorio`` (as ``consultatrackids``) for TesteCF and eCF only."""
SPECIFICATION_ERROR: Final = 1
SIGNATURE_ERROR: Final = 2
DUPLICATE: Final = 3
NOT_THIS_BUYER: Final = 4
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"


def arecf(issuer_rnc: str, buyer_rnc: str, encf: str, *, reason: int | None, at: datetime) -> bytes:
    """An unsigned ARECF: received when ``reason`` is ``None``, else not received for ``reason``."""
    state, why = ("0", "") if reason is None else ("1", f"<CodigoMotivoNoRecibido>{reason}</CodigoMotivoNoRecibido>")
    return (
        '<?xml version="1.0" encoding="utf-8"?><ARECF><DetalleAcusedeRecibo><Version>1.0</Version>'
        f"<RNCEmisor>{escape(issuer_rnc)}</RNCEmisor><RNCComprador>{escape(buyer_rnc)}</RNCComprador>"
        f"<eNCF>{escape(encf)}</eNCF><Estado>{state}</Estado>{why}"
        f"<FechaHoraAcuseRecibo>{at.astimezone(SANTO_DOMINGO).strftime(_MOMENT)}</FechaHoraAcuseRecibo>"
        "</DetalleAcusedeRecibo></ARECF>"
    ).encode()


@final
class ReceiverServices:
    """The directory and the simulator over a ``FakeState`` (see the module)."""

    __slots__ = ("_seen", "_state")

    def __init__(self, state: FakeState) -> None:
        self._state = state
        self._seen: set[tuple[str, str]] = set()

    def handlers(self) -> dict[tuple[str, str], Handler]:
        """Each service's handler by method and lower-case path after the environment's segment."""
        simulator = f"{EMISOR_RECEPTOR}/fe"
        return {
            ("GET", "consultadirectorio/api/consultas/listado"): self._listing,
            ("GET", "consultadirectorio/api/consultas/obtenerdirectorioporrnc"): self._by_rnc,
            ("GET", f"{simulator}/autenticacion/api/semilla"): self._seed,
            ("POST", f"{simulator}/autenticacion/api/validacioncertificado"): self._validate,
            ("POST", f"{simulator}/recepcion/api/ecf"): self._closed(self._reception),
            ("POST", f"{simulator}/aprobacioncomercial/api/ecf"): self._closed(self._approval),
        }

    def _closed(self, handler: Handler) -> Handler:
        async def authorized(request: Request, environment: str) -> Response:
            granted = self._state.receiver_credentials.valid(request.headers.get("authorization", ""))
            if self._state.receiver_requires_auth and not granted:
                return Response(status_code=HTTPStatus.UNAUTHORIZED)
            return await handler(request, environment)

        return authorized

    def _simulator(self, environment: str, rnc: str) -> DirectoryEntry:
        base = f"{self._state.public_url}/{environment}/{EMISOR_RECEPTOR}"
        authentication = base if self._state.receiver_requires_auth else None
        return DirectoryEntry(name=SIMULATOR_NAME, rnc=rnc, reception=base, approval=base, authentication=authentication)

    @staticmethod
    def _listed(entries: list[DirectoryEntry]) -> Response:
        return JSONResponse(
            [
                {
                    "nombre": entry.name,
                    "rnc": entry.rnc,
                    "urlRecepcion": entry.reception,
                    "urlAceptacion": entry.approval,
                    "urlOpcional": entry.authentication,
                }
                for entry in entries
            ]
        )

    async def _listing(self, request: Request, environment: str) -> Response:
        del request
        if environment == WITHOUT_DIRECTORY:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        return self._listed(list(self._state.directory.values()))

    async def _by_rnc(self, request: Request, environment: str) -> Response:
        if environment == WITHOUT_DIRECTORY:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        rnc = request.query_params.get("RNC", "")
        registered = self._state.directory.get(rnc)
        if registered is not None:
            return self._listed([registered])
        return self._listed([self._simulator(environment, rnc)] if environment == "testecf" else [])

    async def _seed(self, request: Request, environment: str) -> Response:
        del request, environment
        return seed_answer(*self._state.receiver_credentials.seed())

    async def _validate(self, request: Request, environment: str) -> Response:
        del environment
        return await validate_seed(request, self._state.check, self._state.receiver_credentials)

    def _reason(self, document: bytes, issuer: str, buyer: str, encf: str, ecf_type: str) -> int | None:
        format_ = ECF_FORMATS.get(ecf_type)
        if format_ is None or schema_problems(format_, document):
            return SPECIFICATION_ERROR
        if not self._state.check.verify(document):
            return SIGNATURE_ERROR
        if (issuer, encf) in self._seen:
            return DUPLICATE
        accepted = self._state.receiver_rncs
        if accepted is not None and buyer not in accepted:
            return NOT_THIS_BUYER
        return None

    async def _reception(self, request: Request, environment: str) -> Response:
        sent = await upload(request)
        if sent is None or not readable(sent[0]):
            return bad_request(NOT_A_FILE)
        document, file_name = sent
        ecf_type, issuer, buyer, encf = fields(
            document,
            "Encabezado/IdDoc/TipoeCF",
            "Encabezado/Emisor/RNCEmisor",
            "Encabezado/Comprador/RNCComprador",
            "Encabezado/IdDoc/eNCF",
        )
        reason = self._reason(document, issuer, buyer, encf, ecf_type)
        if reason is None:
            self._seen.add((issuer, encf))
        now = self._state.clock()
        self._state.received.append(
            Received(
                track_id=None,
                environment=environment,
                host=request.headers.get("host", ""),
                kind="buyer",
                file_name=file_name,
                rnc=issuer,
                encf=encf,
                security_code="",
                document=document,
                received_at=now,
                status=Status.ACCEPTED if reason is None else Status.REJECTED,
                messages=(),
                polls_left=0,
                sequence_used=True,
            )
        )
        signed = sign_with_signxml(arecf(issuer, buyer, encf, reason=reason, at=now), self._state.receiver)
        if self._state.tamper_arecf:
            signed = signed.replace(b"<Estado>0</Estado>", b"<Estado>1</Estado>")
        return Response(signed, media_type="text/xml")

    async def _approval(self, request: Request, environment: str) -> Response:
        del environment
        sent = await upload(request)
        if sent is None or not readable(sent[0]) or schema_problems(Format.ACECF, sent[0]):
            return Response(status_code=HTTPStatus.BAD_REQUEST)
        if not self._state.check.verify(sent[0]):
            return Response(status_code=HTTPStatus.BAD_REQUEST)
        self._state.approvals.append(sent[0])
        return Response(status_code=HTTPStatus.OK)
```

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/app.py` — in `FakeDgii.__init__`, the handlers become both services':

```python
        self._handlers: dict[tuple[str, str], Handler] = {
            **EmitterServices(self.state).handlers(),
            **ReceiverServices(self.state).handlers(),
        }
```

(import `ReceiverServices` from `ledger_country_do_testing.fake_dgii.receiver`), and append the factory:

```python
def create_app() -> Starlette:
    """Granian's factory for a fake run by hand (``ledger-fake-dgii``).

    Every document's schema and signature are checked, by any signer (the owner's real certificate is unknown
    to the fake); the simulator signs its ARECFs with a certificate made at start.
    ``LEDGER_FAKE_DGII_PUBLIC_URL`` is where clients reach it (default ``http://127.0.0.1:8471``).
    """
    public_url = os.environ.get("LEDGER_FAKE_DGII_PUBLIC_URL", DEFAULT_PUBLIC_URL)
    receiver = Authority.root("LEDGER FAKE DGII CA").issue(organization("131880738", name="EMISOR RECEPTOR DGII"))
    return FakeDgii(check=SignxmlCheck(trusted=None), receiver=receiver, public_url=public_url).app
```

(with `import os`, `from ledger_country_do_testing.fake_dgii.state import FakeState, SignxmlCheck` and `from ledger_country_do_testing.pki import Authority, organization` at the top of `app.py`).

`countries/do/testing/src/ledger_country_do_testing/fake_dgii/serve.py`:

```python
"""``ledger-fake-dgii``: the fake DGII served by granian, on 127.0.0.1:8471 unless told otherwise.

Port 8000 is never taken (another project of the owner's uses it). Stop it with Ctrl+C, or by the PID it was
started with; never by image name.
"""

import argparse
from typing import Final, final
lazy from collections.abc import Sequence

from granian import Granian
from granian.constants import Interfaces

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8471
RESERVED_PORT: Final = 8000
_TARGET: Final = "ledger_country_do_testing.fake_dgii.app:create_app"


@final
class _Options(argparse.Namespace):
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def main(argv: Sequence[str] | None = None) -> None:
    """Serve the fake DGII until interrupted."""
    parser = argparse.ArgumentParser(prog="ledger-fake-dgii", description="A fake of DGII's e-CF web services.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="address to listen on (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (default: %(default)s)")
    options = parser.parse_args(argv, namespace=_Options())
    if options.port == RESERVED_PORT:
        parser.error(f"port {RESERVED_PORT} is reserved on this machine")
    Granian(
        target=_TARGET, factory=True, address=options.host, port=options.port, interface=Interfaces.ASGI
    ).serve()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/testing -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Try it by hand (optional), then stop it by its PID**

Run in its own terminal: `uv run ledger-fake-dgii` → granian logs `Listening at: http://127.0.0.1:8471`. In another: `curl -s http://127.0.0.1:8471/testecf/autenticacion/api/autenticacion/semilla` prints a `SemillaModel`. Stop it with Ctrl+C in its terminal (or `taskkill /PID <pid>` with the PID granian printed; never by image name).

- [ ] **Step 7: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 8: Commit**

```bash
git add uv.lock countries/do/testing
git commit -m "test(do): the fake DGII's directory and emisor-receptor simulator with signed ARECFs; ledger-fake-dgii

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: DGII's client: seed and token, reception, results, track ids, the RFCE on `fc`, the directory

**Files:**
- Modify: `countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py` (the `Authority` port and its answers), `countries/do/pyproject.toml` (dependencies gain `"httpx>=0.28.1"`, `"pydantic>=2.14.1"`), `countries/do/tests/conftest.py` (fixtures `clock`, `fake`, `sleeps`, `dgii`)
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/dgii/{__init__, endpoints, wire, client}.py`, `countries/do/testing/src/ledger_country_do_testing/transports.py`
- Test: `countries/do/tests/unit/test_dgii_endpoints.py`, `countries/do/tests/integration/test_dgii_client.py`

**Interfaces:**
- Consumes: `XmlSigner` (Task 6); `domain.{environments, encf, status_map.DgiiMessage, errors}` (Tasks 3, 4, 7, 8); the fake (Tasks 9-10), `ManualClock`, `KeyPairSigner`, samples.
- Produces: `application.ports` adds `Received(track_id)`, `Refused(messages)`, `type Reception = Received | Refused`, `Answer(code, messages, sequence_used)`, `AuthorityUnavailableError` (`do_ecf_dgii_unavailable`), `OutcomeUnknownError` (`do_ecf_dgii_outcome_unknown`), `Authority` (`submit_ecf(environment, issuer_rnc, encf, signed) -> Reception`, `submit_rfce(...) -> Answer | Refused`, `result(environment, track_id) -> Answer`, `track_ids(environment, issuer_rnc, encf) -> tuple[str, ...] | None`). `adapters.dgii.endpoints`: `Service`, `PATHS`, `NOT_IN_CERTECF`, `offered(service, environment)`, `ServiceNotInEnvironmentError`, `LiveHostRefusedError`, `DgiiHosts(ecf, fc)` with `official` and `url(service, environment)`, `OFFICIAL_HOSTS`. `adapters.dgii.wire`: `TokenAnswer`, `MessageAnswer`, `ReceptionAnswer`, `ResultAnswer`, `TrackIdAnswer`, `DirectoryAnswer`. `adapters.dgii.client`: `MAX_ANSWER`, `TOKEN_MARGIN`, `TOKEN_CEILING`, `RETRY_DELAYS`, `type Sleep`, `DgiiTimeouts(connect, total)`, `DEFAULT_TIMEOUTS`, `DirectoryEntry(name, rnc, reception, approval, authentication)`, `read_answer(model, response, service)`, `DgiiClient(signer, *, hosts=OFFICIAL_HOSTS, timeouts=DEFAULT_TIMEOUTS, clock=..., sleep=asyncio.sleep, transport=None)` with `opened()`, `directory(environment, rnc) -> DirectoryEntry | None`, the `Authority` methods. `ledger_country_do_testing.transports`: `Counting(inner)` with `paths`, `BreaksAfterSending(inner, path_part)`.

- [ ] **Step 1: Dependencies**

`countries/do/pyproject.toml`: `dependencies = ["cryptography>=50.0.2", "httpx>=0.28.1", "lxml>=6.1.3", "pydantic>=2.14.1"]`. Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing tests**

`countries/do/tests/conftest.py` — append:

```python
from datetime import UTC, datetime
lazy from collections.abc import AsyncIterator

import httpx

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import SignxmlCheck
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.transports import FAKE_HOSTS


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 10, 13, 13, 30, tzinfo=UTC))


@pytest.fixture
def fake(authority: Authority, issued: Issued, clock: ManualClock) -> FakeDgii:
    receiver = authority.issue(organization("131098193", name="CONSTRUCTORA DEL CARIBE SRL"))
    return FakeDgii(
        check=SignxmlCheck(trusted=[issued.certificate]), receiver=receiver, clock=clock, public_url=FAKE_HOSTS.ecf
    )


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def dgii(signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]) -> AsyncIterator[DgiiClient]:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = DgiiClient(
        signer, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, transport=httpx.ASGITransport(app=fake.app)
    )
    async with client.opened():
        yield client
```

(Move the new imports to the top of the file with the others.)

`countries/do/tests/unit/test_dgii_endpoints.py`:

```python
import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    OFFICIAL_HOSTS,
    DgiiHosts,
    LiveHostRefusedError,
    Service,
    ServiceNotInEnvironmentError,
    offered,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment


def test_dgii_s_documented_urls() -> None:
    assert OFFICIAL_HOSTS.url(Service.RECEPTION, Environment.TESTECF) == (
        "https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas"
    )
    assert OFFICIAL_HOSTS.url(Service.RFCE, Environment.CERTECF) == (
        "https://fc.dgii.gov.do/certecf/recepcionfc/api/recepcion/ecf"
    )
    assert OFFICIAL_HOSTS.url(Service.VALIDATE_SEED, Environment.ECF) == (
        "https://ecf.dgii.gov.do/ecf/autenticacion/api/autenticacion/validarsemilla"
    )


def test_certecf_publishes_neither_track_ids_nor_the_directory() -> None:
    assert not offered(Service.TRACK_IDS, Environment.CERTECF)
    assert not offered(Service.DIRECTORY, Environment.CERTECF)
    assert offered(Service.TRACK_IDS, Environment.TESTECF)
    with pytest.raises(ServiceNotInEnvironmentError, match="no track_ids service in certecf"):
        OFFICIAL_HOSTS.url(Service.TRACK_IDS, Environment.CERTECF)


def test_the_live_environment_is_reached_on_dgii_s_hosts_only() -> None:
    fake = DgiiHosts(ecf="http://127.0.0.1:8471", fc="http://127.0.0.1:8471")
    assert fake.url(Service.SEED, Environment.TESTECF).startswith("http://127.0.0.1:8471/testecf/")
    with pytest.raises(LiveHostRefusedError):
        fake.url(Service.SEED, Environment.ECF)
    assert OFFICIAL_HOSTS.official
    assert not fake.official
```

`countries/do/tests/integration/test_dgii_client.py`:

```python
from datetime import timedelta
from typing import Final

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    AuthorityUnavailableError,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending, Counting

T: Final = Environment.TESTECF
ISSUER: Final = "101850043"
FIRST: Final = Encf(EcfType.CREDIT_INVOICE, 1)
SEED_PATH: Final = "/testecf/autenticacion/api/autenticacion/semilla"


async def signed_ecf(signer: KeyPairSigner, sequence: int = 1, *, small_consumer: bool = False) -> bytes:
    draft = consumer_invoice(sequence) if small_consumer else credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


def client_over(transport: httpx.AsyncBaseTransport, signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> DgiiClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return DgiiClient(signer, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, transport=transport)


async def test_an_e_cf_is_received_then_decided(dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    reception = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    assert fake.received[-1].file_name == "101850043E310000000001.xml"
    assert await dgii.result(T, reception.track_id) == Answer(code=1, messages=(), sequence_used=True)


async def test_dgii_s_messages_and_secuencia_utilizada_arrive_intact(dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, messages=(("145", "El RNC no es emisor"),), sequence_used=False)
    reception = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(reception, Received)
    answer = await dgii.result(T, reception.track_id)
    assert answer == Answer(code=2, messages=(DgiiMessage("145", "El RNC no es emisor"),), sequence_used=False)


async def test_a_refusal_at_the_door_is_an_answer_not_a_failure(dgii: DgiiClient, signer: KeyPairSigner) -> None:
    refused = await dgii.submit_ecf(T, ISSUER, Encf(EcfType.CONSUMER_INVOICE, 1), await signed_ecf(signer, small_consumer=True))
    assert isinstance(refused, Refused)
    assert refused.messages[0].code == "400"


async def test_the_rfce_is_answered_at_once_on_the_fc_host(dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    draft = consumer_invoice()
    summary = await signer.sign_document(rfce.render(draft, compute_totals(draft), security_code="dcp79q"))
    answer = await dgii.submit_rfce(T, ISSUER, draft.encf, summary)
    assert answer == Answer(code=1, messages=(), sequence_used=True)
    assert fake.received[-1].host == "fc.fake.test"
    refused = await dgii.submit_rfce(T, ISSUER, draft.encf, b"not xml")
    assert isinstance(refused, Refused)


async def test_track_ids_of_an_e_ncf_and_none_where_dgii_has_no_such_service(dgii: DgiiClient, signer: KeyPairSigner) -> None:
    first = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    second = await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert isinstance(first, Received)
    assert isinstance(second, Received)
    assert await dgii.track_ids(T, ISSUER, FIRST) == (first.track_id, second.track_id)
    assert await dgii.track_ids(Environment.CERTECF, ISSUER, FIRST) is None


async def test_the_token_is_cached_and_renewed_five_minutes_before_it_expires(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    counting = Counting(httpx.ASGITransport(app=fake.app))
    async with client_over(counting, signer, clock, sleeps).opened() as dgii:
        await dgii.result(T, "a")
        await dgii.result(T, "b")
        clock.advance(timedelta(minutes=54))
        await dgii.result(T, "c")
        clock.advance(timedelta(minutes=2))
        await dgii.result(T, "d")
    assert counting.paths.count(SEED_PATH) == 2


async def test_a_token_dgii_forgot_is_replaced_once(dgii: DgiiClient, fake: FakeDgii) -> None:
    await dgii.result(T, "a")
    fake.state.dgii.revoke()
    assert (await dgii.result(T, "a")).code == 0


async def test_what_cannot_change_dgii_s_state_is_retried_twice(dgii: DgiiClient, fake: FakeDgii, sleeps: list[float]) -> None:
    await dgii.result(T, "warm the token")
    fake.script.outage = 2
    assert (await dgii.result(T, "a")).code == 0
    assert sleeps == [0.2, 0.5]
    fake.script.outage = 3
    with pytest.raises(AuthorityUnavailableError, match="server_error"):
        await dgii.result(T, "a")


async def test_a_submission_is_never_retried(dgii: DgiiClient, signer: KeyPairSigner, fake: FakeDgii, sleeps: list[float]) -> None:
    await dgii.result(T, "warm the token")
    fake.script.outage = 1
    with pytest.raises(AuthorityUnavailableError):
        await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert sleeps == []


async def test_a_connection_broken_after_sending_leaves_the_outcome_unknown(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "facturaselectronicas")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(OutcomeUnknownError):
            await dgii.submit_ecf(T, ISSUER, FIRST, await signed_ecf(signer))
    assert [each.encf for each in fake.received] == ["E310000000001"]


SEED: Final = b"<SemillaModel><valor>v</valor><fecha>f</fecha></SemillaModel>"
GOOD_TOKEN: Final = b'{"token": "t", "expira": "2026-10-13T14:30:00Z"}'


def scripted_dgii(token: bytes, status: int, answer: bytes) -> httpx.MockTransport:
    """A DGII that hands out a seed, answers ``token`` to its validation and ``status``/``answer`` to the rest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=SEED)
        if request.url.path.endswith("validarsemilla"):
            return httpx.Response(200, content=token)
        return httpx.Response(status, content=answer)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("token", "status", "answer", "call", "failure"),
    [
        (b"not json", 200, b"{}", "submit", "unreadable"),
        (b'{"token": "t", "expira": "tomorrow"}', 200, b"{}", "submit", "unreadable"),
        (GOOD_TOKEN, 200, b"not json", "submit", "unreadable"),
        (GOOD_TOKEN, 200, b"[" + b" " * (1024 * 1024) + b"]", "submit", "unreadable"),
        (GOOD_TOKEN, 404, b"", "submit", "undocumented"),
        (GOOD_TOKEN, 200, b'{"trackId": null}', "submit", "undocumented"),
        (GOOD_TOKEN, 404, b"", "rfce", "undocumented"),
        (GOOD_TOKEN, 404, b"", "result", "undocumented"),
    ],
    ids=["token-not-json", "token-expiry", "not-json", "too-large", "not-found", "no-track-id", "rfce-404", "result-404"],
)
async def test_answers_dgii_does_not_document_are_failures(
    signer: KeyPairSigner,
    clock: ManualClock,
    sleeps: list[float],
    token: bytes,
    status: int,
    answer: bytes,
    call: str,
    failure: str,
) -> None:

    async with client_over(scripted_dgii(token, status, answer), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match=failure):
            match call:
                case "submit":
                    await dgii.submit_ecf(T, ISSUER, FIRST, b"<ECF/>")
                case "rfce":
                    await dgii.submit_rfce(T, ISSUER, FIRST, b"<RFCE/>")
                case _:
                    await dgii.result(T, "a")


async def test_a_broken_answer_to_a_query_is_retried_then_a_failure(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> None:
    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "consultaresultado")
    async with client_over(broken, signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="broken"):
            await dgii.result(T, "a")
    assert sleeps == [0.2, 0.5]


async def test_a_token_expiry_without_a_zone_is_read_as_utc(signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> None:
    naive = b'{"token": "t", "expira": "2026-10-13T14:30:00"}'
    async with client_over(scripted_dgii(naive, 200, b'{"codigo": 0}'), signer, clock, sleeps).opened() as dgii:
        assert (await dgii.result(T, "a")).code == 0
        assert (await dgii.result(T, "b")).code == 0


async def test_a_buyer_missing_from_the_directory_is_none(signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> None:
    async with client_over(scripted_dgii(GOOD_TOKEN, 200, b"[]"), signer, clock, sleeps).opened() as dgii:
        assert await dgii.directory(T, "131098193") is None


async def test_dgii_unreachable_and_a_client_not_opened_are_failures(signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with client_over(httpx.MockTransport(refuse), signer, clock, sleeps).opened() as dgii:
        with pytest.raises(AuthorityUnavailableError, match="unreachable"):
            await dgii.result(T, "a")
    with pytest.raises(AuthorityUnavailableError, match="not_open"):
        await dgii.result(T, "a")


async def test_the_directory_names_testecf_s_simulator(dgii: DgiiClient) -> None:
    entry = await dgii.directory(T, "131098193")
    assert entry is not None
    assert (entry.reception, entry.authentication) == (
        "https://ecf.fake.test/testecf/emisorreceptor",
        "https://ecf.fake.test/testecf/emisorreceptor",
    )
    assert await dgii.directory(Environment.TESTECF, "1") is not None
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest countries/do/tests/unit/test_dgii_endpoints.py countries/do/tests/integration/test_dgii_client.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.adapters.dgii'`.

- [ ] **Step 4: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py` — append (and add `from dataclasses import dataclass`, `from typing import ClassVar, final`, `from ledger_country_do.channels.ecf_v1.domain.errors import EcfUnavailableError`, and lazy imports of `Encf`, `Environment`, `DgiiMessage`):

```python
@final
@dataclass(frozen=True, slots=True)
class Received:
    """DGII took the document: its track id."""

    track_id: str


@final
@dataclass(frozen=True, slots=True)
class Refused:
    """DGII refused the document at the door (HTTP 400): it never reached validation."""

    messages: tuple[DgiiMessage, ...]


type Reception = Received | Refused


@final
@dataclass(frozen=True, slots=True)
class Answer:
    """DGII's verdict as it arrives: its code, its messages, ``secuenciaUtilizada``."""

    code: int
    messages: tuple[DgiiMessage, ...]
    sequence_used: bool | None


class AuthorityUnavailableError(EcfUnavailableError):
    """DGII could not be reached or answered something it does not document; ``failure`` says which."""

    code: ClassVar[str] = "do_ecf_dgii_unavailable"

    def __init__(self, failure: str, service: str) -> None:
        self.failure: str = failure
        super().__init__(f"DGII's {service}: {failure}")


class OutcomeUnknownError(EcfUnavailableError):
    """The connection broke after a document may have reached DGII: ask DGII before sending it again."""

    code: ClassVar[str] = "do_ecf_dgii_outcome_unknown"

    def __init__(self, service: str) -> None:
        super().__init__(f"DGII's {service}: the connection broke after sending; the outcome is unknown")


class Authority(Protocol, metaclass=ABCMeta):
    """DGII's services for an emitter (spec §8.6.3)."""

    @abstractmethod
    async def submit_ecf(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Reception:
        """Send a signed e-CF to reception; never retried (``OutcomeUnknownError`` when the connection broke)."""

    @abstractmethod
    async def submit_rfce(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Answer | Refused:
        """Send a signed RFCE; DGII decides at once."""

    @abstractmethod
    async def result(self, environment: Environment, track_id: str) -> Answer:
        """DGII's verdict on a received document."""

    @abstractmethod
    async def track_ids(self, environment: Environment, issuer_rnc: str, encf: Encf) -> tuple[str, ...] | None:
        """Every track id DGII gave the e-NCF, oldest first; ``None`` where DGII has no such service (CerteCF)."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/dgii/__init__.py`:

```python
"""DGII's web services for an emitter, over HTTPS: authentication, reception, results, the RFCE, the directory."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/dgii/endpoints.py`:

```python
"""Where DGII answers each service, per environment (DT-DGII, 02-01-2026).

A URL is a host, the environment's segment, the service and its resource:
``https://ecf.dgii.gov.do/testecf/recepcion/api/facturaselectronicas``. The RFCE has a host of its own,
``fc.dgii.gov.do``. CerteCF publishes neither ``consultatrackids`` nor ``consultadirectorio``. The live
environment is reached on DGII's own hosts only (``LiveHostRefusedError``): a fake can stand in for TesteCF and
CerteCF, never for production. DGII's paths ignore case; DT-DGII's lower-case spelling is used.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST, Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError


class Service(StrEnum):
    """DGII's services an emitter calls, as errors name them."""

    SEED = "seed"
    VALIDATE_SEED = "validate_seed"
    RECEPTION = "reception"
    RESULT = "result"
    TRACK_IDS = "track_ids"
    RFCE = "rfce"
    DIRECTORY = "directory"


PATHS: Final[Mapping[Service, str]] = MappingProxyType(
    {
        Service.SEED: "autenticacion/api/autenticacion/semilla",
        Service.VALIDATE_SEED: "autenticacion/api/autenticacion/validarsemilla",
        Service.RECEPTION: "recepcion/api/facturaselectronicas",
        Service.RESULT: "consultaresultado/api/consultas/estado",
        Service.TRACK_IDS: "consultatrackids/api/trackids/consulta",
        Service.RFCE: "recepcionfc/api/recepcion/ecf",
        Service.DIRECTORY: "consultadirectorio/api/consultas/obtenerdirectorioporrnc",
    }
)
NOT_IN_CERTECF: Final = frozenset({Service.TRACK_IDS, Service.DIRECTORY})


class ServiceNotInEnvironmentError(EcfConflictError):
    """DGII publishes no such service in the environment."""

    code: ClassVar[str] = "do_ecf_dgii_service_not_in_environment"

    def __init__(self, service: Service, environment: Environment) -> None:
        super().__init__(f"DGII has no {service.value} service in {environment.value}")


class LiveHostRefusedError(EcfError):
    """The live environment was configured on a host that is not DGII's: a defect of configuration."""

    code: ClassVar[str] = "do_ecf_live_host_refused"

    def __init__(self) -> None:
        super().__init__("the live environment is reached on DGII's own hosts only")


def offered(service: Service, environment: Environment) -> bool:
    """Whether DGII answers ``service`` in ``environment``."""
    return environment is not Environment.CERTECF or service not in NOT_IN_CERTECF


@final
@dataclass(frozen=True, slots=True)
class DgiiHosts:
    """DGII's two hosts, or a fake's."""

    ecf: str = OFFICIAL_ECF_HOST
    fc: str = OFFICIAL_FC_HOST

    @property
    def official(self) -> bool:
        """Whether these are DGII's own hosts."""
        return (self.ecf, self.fc) == (OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST)

    def url(self, service: Service, environment: Environment) -> str:
        """Where ``service`` answers in ``environment``."""
        if environment.live and not self.official:
            raise LiveHostRefusedError
        if not offered(service, environment):
            raise ServiceNotInEnvironmentError(service, environment)
        host = self.fc if service is Service.RFCE else self.ecf
        return f"{host}/{environment.value}/{PATHS[service]}"


OFFICIAL_HOSTS: Final = DgiiHosts()
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/dgii/wire.py`:

```python
"""DGII's JSON answers, read strictly enough to trust and loosely enough to survive DGII's additions.

Each model reads the fields the client uses by DGII's names and ignores the rest (DGII adds fields without
notice: ``secuenciaUtilizada`` arrived in 2022). DGII spells keys inconsistently (``eNCF``/``encf``) and types
``codigo`` as a number in one service and a string in another: codes are read in pydantic's lax mode. An answer
that does not fit is unreadable, retried later like an outage, never guessed.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class _Answer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class TokenAnswer(_Answer):
    """``validarsemilla``/``validacioncertificado``: ``{token, expira, expedido}`` (UTC)."""

    token: str = Field(min_length=1, max_length=4096)
    expires: str = Field(alias="expira", max_length=64)


class MessageAnswer(_Answer):
    """One of DGII's messages."""

    code: int | str = Field(default="", alias="codigo")
    text: str = Field(default="", alias="valor", max_length=2000)


class ReceptionAnswer(_Answer):
    """``facturaselectronicas``: a track id, or an error and its message."""

    track_id: str | None = Field(default=None, alias="trackId", max_length=128)
    error: str | None = Field(default=None, max_length=256)
    message: str | None = Field(default=None, alias="mensaje", max_length=2000)


class ResultAnswer(_Answer):
    """``consultaresultado`` and the RFCE's reception: the code, DGII's messages, ``secuenciaUtilizada``."""

    code: int = Field(alias="codigo")
    messages: list[MessageAnswer] = Field(default_factory=list[MessageAnswer], alias="mensajes", max_length=200)
    sequence_used: bool | None = Field(default=None, alias="secuenciaUtilizada")


class TrackIdAnswer(_Answer):
    """One entry of ``consultatrackids``."""

    track_id: str = Field(alias="trackId", min_length=1, max_length=128)
    received_at: str = Field(default="", alias="fechaRecepcion", max_length=64)


class DirectoryAnswer(_Answer):
    """One entry of ``consultadirectorio``: a receiver's service bases."""

    name: str = Field(default="", alias="nombre", max_length=300)
    rnc: str = Field(max_length=11)
    reception: str = Field(alias="urlRecepcion", min_length=1, max_length=2000)
    approval: str = Field(default="", alias="urlAceptacion", max_length=2000)
    authentication: str | None = Field(default=None, alias="urlOpcional", max_length=2000)
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/dgii/client.py`:

```python
"""DGII's web services for an emitter over HTTPS (httpx): the ``Authority`` port, and the directory.

* **Authentication.** DGII's seed, signed by the ``XmlSigner`` (``sign_seed``), is exchanged at
  ``validarsemilla`` for a token cached per environment until five minutes before it expires (never longer than
  an hour), fetched once at a time; a 401 drops it and the call is made once more with a new one. Tokens live in
  memory only: never stored, logged or put in an error.
* **Retries.** What cannot change DGII's state (seed, its validation, results, track ids, the directory) is
  retried twice, 0.2 s and 0.5 s apart. A document sent is never retried: when the connection breaks after its
  request may have left, ``OutcomeUnknownError`` tells the caller to ask DGII for the e-NCF's track ids first.
* **Failures.** No connection, a timeout, a 5xx or 429, an answer DGII does not document, unreadable, or larger
  than 1 MiB: ``AuthorityUnavailableError`` (``failure``: ``unreachable``, ``broken``, ``server_error``,
  ``undocumented``, ``unreadable``, ``not_open``). DGII's refusal of a document (400 at reception) is an answer:
  ``Refused``.
* **Transport.** Redirects are not followed; the environment's proxies and ``.netrc`` are ignored
  (``trust_env=False``); TLS is verified (httpx's default); the live environment only on DGII's hosts
  (``endpoints``). Every file sent is named ``RNC + e-NCF + .xml`` (DT-DGII).
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import OFFICIAL_HOSTS, Service, offered
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import (
    DirectoryAnswer,
    ReceptionAnswer,
    ResultAnswer,
    TokenAnswer,
    TrackIdAnswer,
)
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    AuthorityUnavailableError,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import MessageAnswer
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception, XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

MAX_ANSWER: Final = 1024 * 1024
TOKEN_MARGIN: Final = timedelta(minutes=5)
TOKEN_CEILING: Final = timedelta(hours=1)
RETRY_DELAYS: Final = (0.2, 0.5)
_SERVER_ERROR: Final = 500
_TOO_MANY: Final = 429
_UNAUTHORIZED: Final = 401
_BAD_REQUEST: Final = 400
_TOKEN: Final = TypeAdapter(TokenAnswer)
_RECEPTION: Final = TypeAdapter(ReceptionAnswer)
_RESULT: Final = TypeAdapter(ResultAnswer)
_TRACK_IDS: Final = TypeAdapter(list[TrackIdAnswer])
_DIRECTORY: Final = TypeAdapter(list[DirectoryAnswer])

type Sleep = Callable[[float], Awaitable[None]]


@final
@dataclass(frozen=True, slots=True)
class DgiiTimeouts:
    """How long a call may take: to connect, and in all (each attempt)."""

    connect: float = 5.0
    total: float = 20.0


DEFAULT_TIMEOUTS: Final = DgiiTimeouts()


@final
@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """A receiver's service bases, from DGII's directory; ``authentication`` ``None`` when it publishes none."""

    name: str
    rnc: str
    reception: str
    approval: str
    authentication: str | None


@final
@dataclass(slots=True)
class _Token:
    value: str
    good_until: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _messages(found: list[MessageAnswer]) -> tuple[DgiiMessage, ...]:
    return tuple(DgiiMessage(str(message.code), message.text) for message in found)


def read_answer[T](model: TypeAdapter[T], response: httpx.Response, service: Service) -> T:
    """``response``'s JSON as ``model``; ``AuthorityUnavailableError`` (``unreadable``) otherwise."""
    if len(response.content) > MAX_ANSWER:
        raise AuthorityUnavailableError("unreadable", service.value)
    try:
        return model.validate_json(response.content)
    except ValidationError as error:
        raise AuthorityUnavailableError("unreadable", service.value) from error


@final
class DgiiClient(Authority):
    """``Authority`` over HTTPS (see the module); ``opened`` holds its connections."""

    __slots__ = ("_clock", "_hosts", "_http", "_locks", "_signer", "_sleep", "_timeouts", "_tokens", "_transport")

    def __init__(
        self,
        signer: XmlSigner,
        *,
        hosts: DgiiHosts = OFFICIAL_HOSTS,
        timeouts: DgiiTimeouts = DEFAULT_TIMEOUTS,
        clock: Callable[[], datetime] = _now,
        sleep: Sleep = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._signer = signer
        self._hosts = hosts
        self._timeouts = timeouts
        self._clock = clock
        self._sleep = sleep
        self._transport = transport
        self._tokens: dict[Environment, _Token] = {}
        self._locks: dict[Environment, asyncio.Lock] = {}
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[DgiiClient]:
        """This client with its connections open, for the length of the block; tokens forgotten after."""
        timeout = httpx.Timeout(self._timeouts.total, connect=self._timeouts.connect)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=self._transport
        ) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None
                self._tokens.clear()

    def _client(self, service: Service) -> httpx.AsyncClient:
        if self._http is None:
            raise AuthorityUnavailableError("not_open", service.value)
        return self._http

    async def _attempt(self, request: httpx.Request, service: Service, *, idempotent: bool) -> httpx.Response | AuthorityUnavailableError:
        try:
            response = await self._client(service).send(request)
        except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
            return AuthorityUnavailableError("unreachable", service.value)
        except httpx.TransportError as error:
            if idempotent:
                return AuthorityUnavailableError("broken", service.value)
            raise OutcomeUnknownError(service.value) from error
        if response.status_code >= _SERVER_ERROR or response.status_code == _TOO_MANY:
            return AuthorityUnavailableError("server_error", service.value)
        return response

    async def _call(self, request: httpx.Request, service: Service, *, idempotent: bool) -> httpx.Response:
        delays = iter(RETRY_DELAYS if idempotent else ())
        while True:
            outcome = await self._attempt(request, service, idempotent=idempotent)
            if isinstance(outcome, httpx.Response):
                return outcome
            delay = next(delays, None)
            if delay is None:
                raise outcome
            await self._sleep(delay)

    def _request(
        self,
        method: str,
        url: str,
        service: Service,
        token: str | None,
        *,
        params: Mapping[str, str] | None = None,
        document: tuple[str, bytes] | None = None,
    ) -> httpx.Request:
        headers = {"Accept": "application/json"} | ({} if token is None else {"Authorization": f"Bearer {token}"})
        files = None if document is None else {"xml": (document[0], document[1], "text/xml")}
        return self._client(service).build_request(method, url, headers=headers, params=params, files=files)

    async def _token(self, environment: Environment) -> str:
        async with self._locks.setdefault(environment, asyncio.Lock()):
            cached = self._tokens.get(environment)
            if cached is not None and cached.good_until > self._clock():
                return cached.value
            seed_url = self._hosts.url(Service.SEED, environment)
            seed = await self._call(self._request("GET", seed_url, Service.SEED, None), Service.SEED, idempotent=True)
            signed = await self._signer.sign_seed(seed.content)
            validate_url = self._hosts.url(Service.VALIDATE_SEED, environment)
            request = self._request("POST", validate_url, Service.VALIDATE_SEED, None, document=("semilla.xml", signed))
            token = read_answer(_TOKEN, await self._call(request, Service.VALIDATE_SEED, idempotent=True), Service.VALIDATE_SEED)
            try:
                expires = datetime.fromisoformat(token.expires.strip())
            except ValueError as error:
                raise AuthorityUnavailableError("unreadable", Service.VALIDATE_SEED.value) from error
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            good_until = min(expires, self._clock() + TOKEN_CEILING) - TOKEN_MARGIN
            self._tokens[environment] = _Token(token.token, good_until)
            return token.token

    async def _authenticated(
        self, environment: Environment, service: Service, build: Callable[[str], httpx.Request], *, idempotent: bool
    ) -> httpx.Response:
        response = await self._call(build(await self._token(environment)), service, idempotent=idempotent)
        if response.status_code != _UNAUTHORIZED:
            return response
        self._tokens.pop(environment, None)
        return await self._call(build(await self._token(environment)), service, idempotent=idempotent)

    def _send(self, environment: Environment, service: Service, name: str, signed: bytes) -> Callable[[str], httpx.Request]:
        url = self._hosts.url(service, environment)
        return lambda token: self._request("POST", url, service, token, document=(name, signed))

    @override
    async def submit_ecf(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Reception:
        build = self._send(environment, Service.RECEPTION, f"{issuer_rnc}{encf}.xml", signed)
        response = await self._authenticated(environment, Service.RECEPTION, build, idempotent=False)
        if not response.is_success and response.status_code != _BAD_REQUEST:
            raise AuthorityUnavailableError("undocumented", Service.RECEPTION.value)
        answer = read_answer(_RECEPTION, response, Service.RECEPTION)
        if response.is_success and answer.track_id:
            return Received(answer.track_id)
        if response.is_success:
            raise AuthorityUnavailableError("undocumented", Service.RECEPTION.value)
        return Refused((DgiiMessage(answer.error or str(response.status_code), answer.message or ""),))

    @override
    async def submit_rfce(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Answer | Refused:
        build = self._send(environment, Service.RFCE, f"{issuer_rnc}{encf}.xml", signed)
        response = await self._authenticated(environment, Service.RFCE, build, idempotent=False)
        if response.status_code == _BAD_REQUEST:
            refusal = read_answer(_RECEPTION, response, Service.RFCE)
            return Refused((DgiiMessage(refusal.error or "400", refusal.message or ""),))
        if not response.is_success:
            raise AuthorityUnavailableError("undocumented", Service.RFCE.value)
        answer = read_answer(_RESULT, response, Service.RFCE)
        return Answer(answer.code, _messages(answer.messages), answer.sequence_used)

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        url = self._hosts.url(Service.RESULT, environment)
        response = await self._authenticated(
            environment,
            Service.RESULT,
            lambda token: self._request("GET", url, Service.RESULT, token, params={"trackid": track_id}),
            idempotent=True,
        )
        if not response.is_success:
            raise AuthorityUnavailableError("undocumented", Service.RESULT.value)
        answer = read_answer(_RESULT, response, Service.RESULT)
        return Answer(answer.code, _messages(answer.messages), answer.sequence_used)

    @override
    async def track_ids(self, environment: Environment, issuer_rnc: str, encf: Encf) -> tuple[str, ...] | None:
        if not offered(Service.TRACK_IDS, environment):
            return None
        url = self._hosts.url(Service.TRACK_IDS, environment)
        params = {"rncemisor": issuer_rnc, "encf": str(encf)}
        response = await self._authenticated(
            environment,
            Service.TRACK_IDS,
            lambda token: self._request("GET", url, Service.TRACK_IDS, token, params=params),
            idempotent=True,
        )
        entries = read_answer(_TRACK_IDS, response, Service.TRACK_IDS)
        return tuple(entry.track_id for entry in sorted(entries, key=lambda entry: entry.received_at))

    async def directory(self, environment: Environment, rnc: str) -> DirectoryEntry | None:
        """The receiver ``rnc`` registered in DGII's directory; ``None`` if it is no electronic receiver (F5, F6)."""
        url = self._hosts.url(Service.DIRECTORY, environment)
        response = await self._authenticated(
            environment,
            Service.DIRECTORY,
            lambda token: self._request("GET", url, Service.DIRECTORY, token, params={"RNC": rnc}),
            idempotent=True,
        )
        found = read_answer(_DIRECTORY, response, Service.DIRECTORY)
        if not found:
            return None
        entry = found[0]
        return DirectoryEntry(entry.name, entry.rnc, entry.reception, entry.approval, entry.authentication or None)
```

`countries/do/testing/src/ledger_country_do_testing/transports.py`:

```python
"""httpx transports that watch or break the fake's traffic, and the hosts tests give the fake."""

from typing import Final, final, override

import httpx

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts

FAKE_HOSTS: Final = DgiiHosts(ecf="https://ecf.fake.test", fc="https://fc.fake.test")
"""The hosts the DO pack's tests give DGII's client in front of the fake (``.test`` never resolves)."""


@final
class Counting(httpx.AsyncBaseTransport):
    """Passes every request on and records its path."""

    __slots__ = ("_inner", "paths")

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.paths: list[str] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return await self._inner.handle_async_request(request)


@final
class BreaksAfterSending(httpx.AsyncBaseTransport):
    """Delivers a request whose path holds ``path_part``, then breaks the connection before the answer."""

    __slots__ = ("_inner", "_path_part")

    def __init__(self, inner: httpx.AsyncBaseTransport, path_part: str) -> None:
        self._inner = inner
        self._path_part = path_part

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if self._path_part in request.url.path:
            await response.aread()
            raise httpx.ReadError("the connection was reset", request=request)
        return response
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/tests/unit/test_dgii_endpoints.py countries/do/tests/integration/test_dgii_client.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add uv.lock countries
git commit -m "feat(do): DGII's client: seed and token, reception, results, track ids, RFCE on fc, directory

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The emission use case end to end: sign, route, submit, poll, map

**Files:**
- Modify: `countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py` (the `EcfFormat` port)
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/application/emission.py`, `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/documents.py`
- Test: `countries/do/tests/unit/test_emission_flow.py`, `countries/do/tests/integration/test_emission.py`

**Interfaces:**
- Consumes: everything above: `XmlSigner`, `Authority`, `Received`, `Refused`, `Answer`, `OutcomeUnknownError` (Tasks 6, 11); `compute_totals`, `route`, `Route`, `decide`, `DgiiCode`, `Outcome`, `security_code`, `qr_url`, `rfce_qr_url`, `QrHosts`, `OFFICIAL_QR` (Tasks 4, 7, 8); `ecf.render`, `ecf.FORMAT_OF`, `rfce.render`, `schemas.checked`, `verify_enveloped` (Tasks 3, 5, 6).
- Produces: `application.ports.EcfFormat` (`ecf(draft, totals, *, signed_at) -> bytes`, `rfce(draft, totals, *, security_code) -> bytes`, `signed(draft, signed, *, rfce) -> str` returning the signature value), `CertificateValidity`, `XmlSigner.certificate_validity()`, `TrackId(track_id, received_at)`, `Authority.require_ready(environment)`, `Answer.rnc`/`Answer.encf`. `adapters.xml.documents.DgiiXml` (implements it). `application.emission`: `POLL_SCHEDULE`, `MAX_SIGNING_SKEW`, `SETTLE_DELAY`, `RECEIPT_TOLERANCE`, `SEND_BUDGET`, `type Clock`, `type Sleep`, `Signed(draft, totals, route, signed_at, signed_ecf, signed_rfce, security_code, qr)`, `Unknown(code, failure, sent)`, `Emitted(signed, track_id, outcome, unknown)` with `pending`, `Emitter(*, xml, signer, authority, **options)` (`EmitterOptions`: `environment`, `clock`, `sleep` required; `schedule`, `settle`, `budget`, `qr_hosts`, `regime`) with `sign(draft) -> Signed`, `send(signed) -> Emitted`, `emit(draft) -> Emitted`.

> **Revised by Task 12's reviews (binding; the committed code is the reference, the code blocks below are the first draft).**
> - **No automatic resend.** After `OutcomeUnknownError` the e-CF is never sent again: wait `SETTLE_DELAY` (more than the client's per-attempt time), ask `track_ids` (`TrackId(track_id, received_at)`), adopt the newest received no earlier than `signed_at - RECEIPT_TOLERANCE`; otherwise park it. An RFCE whose outcome is unknown is parked too (`consultarfce` recovery is v2-M3's).
> - **Two steps.** `sign(draft) -> Signed` (signed e-CF and RFCE, FechaHoraFirma, security code, QR) so a caller persists it before the first submission; `send(signed) -> Emitted` never raises a pack error: anything that stops it parks the document with `Unknown(code, failure, sent)` (`sent`: `False` certainly never left, `True` DGII answered about it, `None` it may be at DGII).
> - **Fail fast before signing.** `Authority.require_ready(environment)` when the emitter is built and before each signing (live host refused, client not open). The certificate must cover `signed_at` and `signed_at + MAX_SIGNING_SKEW`; a signature that comes back more than `MAX_SIGNING_SKEW` from `signed_at` is refused (`SigningSkewError`).
> - **Bounds and identity.** `SEND_BUDGET` bounds a send (calls, settle delay, polls). An answer naming another RNC or e-NCF than ours parks the document (`do_ecf_dgii_answer_mismatch`). The client sends no document with less than one attempt's time left before its call deadline.

- [ ] **Step 1: Write the failing tests**

`countries/do/tests/unit/test_emission_flow.py` (the use case over in-memory ports: the branches the fake cannot reach cheaply):

```python
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import final, override

import pytest

from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.emission import Emitter
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage, EcfState, NumberFate
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf


@final
@dataclass
class ScriptedAuthority(Authority):
    submissions: list[Reception | OutcomeUnknownError] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)
    known: tuple[str, ...] | None = ()
    sent: int = 0

    @override
    async def submit_ecf(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Reception:
        self.sent += 1
        found = self.submissions.pop(0)
        if isinstance(found, OutcomeUnknownError):
            raise found
        return found

    @override
    async def submit_rfce(self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes) -> Answer | Refused:
        return Refused((DgiiMessage("400", "Tipo de archivo no válido"),))

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        return self.answers.pop(0)

    @override
    async def track_ids(self, environment: Environment, issuer_rnc: str, encf: Encf) -> tuple[str, ...] | None:
        return self.known


def emitter(authority: Authority, signer: KeyPairSigner, schedule: tuple[float, ...] = (1.0, 2.0)) -> Emitter:
    async def sleep(seconds: float) -> None:
        del seconds

    return Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=authority,
        environment=Environment.TESTECF,
        clock=lambda: datetime(2026, 10, 13, 13, 30, tzinfo=UTC),
        sleep=sleep,
        schedule=schedule,
    )


IN_PROCESS = Answer(code=3, messages=(), sequence_used=None)
ACCEPTED = Answer(code=1, messages=(), sequence_used=True)


async def test_an_unknown_outcome_with_a_track_id_at_dgii_is_polled_not_resent(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[OutcomeUnknownError("reception")], answers=[ACCEPTED], known=("t1", "t2"))
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (authority.sent, emitted.track_id, emitted.pending) == (1, "t2", False)


async def test_an_unknown_outcome_dgii_never_recorded_is_sent_again(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[OutcomeUnknownError("reception"), Received("t3")], answers=[ACCEPTED])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert (authority.sent, emitted.track_id) == (2, "t3")


async def test_an_unknown_outcome_where_dgii_cannot_say_is_left_to_a_person(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[OutcomeUnknownError("reception")], known=None)
    with pytest.raises(OutcomeUnknownError):
        await emitter(authority, signer).emit(credit_invoice())
    assert authority.sent == 1


async def test_after_the_schedule_a_document_still_in_process_is_pending(signer: KeyPairSigner) -> None:
    authority = ScriptedAuthority(submissions=[Received("t")], answers=[IN_PROCESS, IN_PROCESS])
    emitted = await emitter(authority, signer).emit(credit_invoice())
    assert emitted.pending
    assert emitted.outcome is not None
    assert emitted.outcome.state is EcfState.IN_PROCESS
    nothing = await emitter(ScriptedAuthority(submissions=[Received("t")]), signer, schedule=()).emit(credit_invoice())
    assert (nothing.outcome, nothing.pending) == (None, True)


async def test_a_refusal_at_the_door_burns_the_number(signer: KeyPairSigner) -> None:
    refused = Refused((DgiiMessage("400", "x"),))
    emitted = await emitter(ScriptedAuthority(submissions=[refused]), signer).emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.state, emitted.outcome.number, emitted.track_id) == (EcfState.REJECTED, NumberFate.BURNED, None)
    summary = await emitter(ScriptedAuthority(), signer).emit(consumer_invoice())
    assert summary.outcome is not None
    assert summary.outcome.number is NumberFate.BURNED
```

`countries/do/tests/integration/test_emission.py` (the whole path against the fake DGII):

```python
from typing import Final

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.application.emission import Emitter
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import (
    DgiiMessage,
    EcfState,
    FiscalStatus,
    NumberFate,
    Severity,
    UnknownDgiiCodeError,
)
from ledger_country_do_testing.clock import ManualClock
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS, BreaksAfterSending

QR_HOSTS: Final = QrHosts(ecf=FAKE_HOSTS.ecf, fc=FAKE_HOSTS.fc)


@pytest.fixture
def emitter(dgii: DgiiClient, signer: KeyPairSigner, clock: ManualClock, sleeps: list[float]) -> Emitter:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return Emitter(
        xml=DgiiXml(), signer=signer, authority=dgii, environment=Environment.TESTECF, clock=clock, sleep=sleep, qr_hosts=QR_HOSTS
    )


async def test_a_credit_invoice_is_signed_sent_polled_and_accepted(emitter: Emitter, fake: FakeDgii, sleeps: list[float]) -> None:
    emitted = await emitter.emit(credit_invoice())
    assert emitted.route is Route.RECEPTION
    assert emitted.outcome is not None
    assert (emitted.outcome.status, emitted.outcome.number, emitted.pending) == (FiscalStatus.ACCEPTED, NumberFate.USED, False)
    assert fake.received[-1].document == emitted.signed_ecf
    assert verify_enveloped(emitted.signed_ecf).signature_value.startswith(emitted.security_code)
    assert emitted.qr.startswith("https://ecf.fake.test/testecf/consultatimbre?RncEmisor=101850043&RncComprador=131098193")
    assert sleeps == [1.0]


async def test_aceptado_condicional_after_en_proceso_is_valid_with_its_messages(emitter: Emitter, fake: FakeDgii, sleeps: list[float]) -> None:
    fake.script.default = Scripted(status=Status.ACCEPTED_CONDITIONALLY, messages=(("613", "Fuera de tolerancia"),), processing=2)
    emitted = await emitter.emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.state, emitted.outcome.status, emitted.outcome.severity) == (
        EcfState.ACCEPTED_CONDITIONALLY,
        FiscalStatus.ACCEPTED_WITH_OBSERVATIONS,
        Severity.WARNING,
    )
    assert emitted.outcome.messages == (DgiiMessage("613", "Fuera de tolerancia"),)
    assert sleeps == [1.0, 2.0, 5.0]


async def test_a_rejection_dgii_lets_reuse_releases_the_number(emitter: Emitter, fake: FakeDgii) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, sequence_used=False)
    emitted = await emitter.emit(credit_invoice())
    assert emitted.outcome is not None
    assert (emitted.outcome.status, emitted.outcome.number) == (FiscalStatus.REJECTED, NumberFate.RELEASED)


async def test_a_small_consumer_invoice_goes_as_an_rfce_to_fc_with_the_e_cf_s_security_code(
    emitter: Emitter, fake: FakeDgii, sleeps: list[float]
) -> None:
    emitted = await emitter.emit(consumer_invoice())
    assert (emitted.route, emitted.track_id, sleeps) == (Route.RFCE, None, [])
    assert emitted.signed_rfce is not None
    assert texts(emitted.signed_rfce, "Encabezado/CodigoSeguridadeCF") == (emitted.security_code,)
    assert verify_enveloped(emitted.signed_ecf).signature_value[:6] == emitted.security_code
    assert fake.received[-1].host == "fc.fake.test"
    assert emitted.outcome is not None
    assert emitted.outcome.status is FiscalStatus.ACCEPTED
    assert emitted.qr == (
        f"https://fc.fake.test/testecf/consultatimbrefc?RncEmisor=101850043&ENCF=E320000000001&MontoTotal=1180.00"
        f"&CodigoSeguridad={emitted.security_code.replace('+', '%2B').replace('/', '%2F')}"
    )


async def test_a_large_consumer_invoice_goes_in_full(emitter: Emitter, fake: FakeDgii) -> None:
    emitted = await emitter.emit(consumer_invoice(2, large=True))
    assert (emitted.route, emitted.signed_rfce) == (Route.RECEPTION, None)
    assert fake.received[-1].host == "ecf.fake.test"


async def test_an_rfce_code_dgii_does_not_document_is_not_guessed(emitter: Emitter, fake: FakeDgii) -> None:
    fake.script.default = Scripted(status=Status.IN_PROCESS)
    with pytest.raises(UnknownDgiiCodeError):
        await emitter.emit(consumer_invoice())


async def test_a_broken_connection_after_sending_is_resolved_by_track_ids_not_a_second_send(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock
) -> None:
    async def sleep(seconds: float) -> None:
        del seconds

    broken = BreaksAfterSending(httpx.ASGITransport(app=fake.app), "facturaselectronicas")
    async with DgiiClient(signer, hosts=FAKE_HOSTS, clock=clock, sleep=sleep, transport=broken).opened() as dgii:
        emitter = Emitter(xml=DgiiXml(), signer=signer, authority=dgii, environment=Environment.TESTECF, clock=clock, sleep=sleep)
        emitted = await emitter.emit(credit_invoice())
    assert len(fake.received) == 1
    assert emitted.track_id == fake.received[0].track_id
    assert emitted.outcome is not None
    assert emitted.outcome.status is FiscalStatus.ACCEPTED
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest countries/do/tests/unit/test_emission_flow.py countries/do/tests/integration/test_emission.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.adapters.xml.documents'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/application/ports.py` — append (lazy imports of `datetime`, `EcfDraft`, `Totals`):

```python
class EcfFormat(Protocol, metaclass=ABCMeta):
    """DGII's XML formats: documents written and checked against DGII's schemas."""

    @abstractmethod
    def ecf(self, draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
        """``draft``'s unsigned e-CF, checked as it will be once signed."""

    @abstractmethod
    def rfce(self, draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
        """``draft``'s unsigned RFCE, checked as it will be once signed."""

    @abstractmethod
    def signed(self, draft: EcfDraft, signed: bytes, *, rfce: bool) -> str:
        """``signed`` checked against its schema and its signature verified; its signature value."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/xml/documents.py`:

```python
"""The ``EcfFormat`` port over DGII's formats: written, checked against DGII's schema, signature verified.

A document is checked unsigned, as it will be once signed, before the signer sees it, and checked again once
signed, its signature verified by the pack's own verifier, before it is sent: a document DGII's schema refuses
never leaves, and neither does a signature that does not hold.
"""

from typing import final, override
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf, rfce
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import checked
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.application.ports import EcfFormat
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals


@final
class DgiiXml(EcfFormat):
    """DGII's formats (see the module)."""

    __slots__ = ()

    @override
    def ecf(self, draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
        return checked(ecf.FORMAT_OF[draft.ecf_type], ecf.render(draft, totals, signed_at=signed_at), unsigned=True)

    @override
    def rfce(self, draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
        return checked(Format.RFCE, rfce.render(draft, totals, security_code=security_code), unsigned=True)

    @override
    def signed(self, draft: EcfDraft, signed: bytes, *, rfce: bool) -> str:
        checked(Format.RFCE if rfce else ecf.FORMAT_OF[draft.ecf_type], signed, unsigned=False)
        return verify_enveloped(signed).signature_value
```

`countries/do/src/ledger_country_do/channels/ecf_v1/application/emission.py`:

```python
"""Emitting an e-CF end to end: total, route, write, sign, check, send, poll, map (spec §8.5, §8.6.3-§8.6.4).

1. Totals with the regime's rates on the issue date; the route (``RFCE`` for a type 32 under the threshold).
2. The e-CF written and checked, signed (``FechaHoraFirma`` is the moment of signing), checked again; its
   security code is the first six characters of its signature value.
3. RFCE route: the summary written with that code, signed, checked, sent to the ``fc`` host and decided at once.
   The full signed e-CF is kept by the caller (IT §9.1).
4. Reception route: sent once (superseded by the review: never sent again; see the note under Interfaces).
   If the connection broke after sending, DGII is asked for the e-NCF's track ids:
   the newest is polled; with none the document is sent again (DGII never recorded it); where DGII cannot say
   (CerteCF) the outcome stays unknown for a person (``OutcomeUnknownError``). Then polled on the schedule
   (1, 2, 5, 10, 30, 60 s) until a final code; after it, the document is still pending (v2-M3's job continues).
5. DGII's codes mapped by ``status_map.decide``; a refusal at the door burns the number (never reuse what DGII
   may have recorded).

The use case holds no state between documents: numbering, persistence and retries across processes are
v2-M3's (the spike's caller supplies each e-NCF).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Awaitable, Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.application.ports import OutcomeUnknownError, Received, Refused
from ledger_country_do.channels.ecf_v1.domain.printed import OFFICIAL_QR, qr_url, rfce_qr_url, security_code
from ledger_country_do.channels.ecf_v1.domain.routing import Route, route
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiCode, decide
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
lazy from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    EcfFormat,
    Reception,
    XmlSigner,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import Outcome
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_country_do.regime.definitions import RegimeDef

POLL_SCHEDULE: Final = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

type Clock = Callable[[], datetime]
type Sleep = Callable[[float], Awaitable[None]]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Emitted:
    """What emitting a document produced: what was signed and sent, and DGII's mapped answer."""

    draft: EcfDraft
    totals: Totals
    route: Route
    signed_at: datetime
    signed_ecf: bytes
    signed_rfce: bytes | None
    security_code: str
    qr: str
    track_id: str | None
    outcome: Outcome | None
    """``None`` if DGII was never asked (an empty schedule); a non-final outcome when still pending."""

    @property
    def pending(self) -> bool:
        """Whether DGII has not decided yet."""
        return self.outcome is None or not self.outcome.final


def _refused(refusal: Refused) -> Outcome:
    return decide(DgiiCode.REJECTED, sequence_used=None, messages=refusal.messages)


def _decided(answer: Answer, *, rfce: bool) -> Outcome:
    return decide(answer.code, sequence_used=answer.sequence_used, messages=answer.messages, rfce=rfce)


@final
class Emitter:
    """Emits one document at a time (see the module)."""

    __slots__ = ("_authority", "_clock", "_environment", "_qr_hosts", "_regime", "_schedule", "_signer", "_sleep", "_xml")

    def __init__(
        self,
        *,
        xml: EcfFormat,
        signer: XmlSigner,
        authority: Authority,
        environment: Environment,
        clock: Clock,
        sleep: Sleep,
        schedule: tuple[float, ...] = POLL_SCHEDULE,
        qr_hosts: QrHosts = OFFICIAL_QR,
        regime: RegimeDef = REGIME,
    ) -> None:
        self._xml = xml
        self._signer = signer
        self._authority = authority
        self._environment = environment
        self._clock = clock
        self._sleep = sleep
        self._schedule = schedule
        self._qr_hosts = qr_hosts
        self._regime = regime

    async def emit(self, draft: EcfDraft) -> Emitted:
        """``draft`` emitted (see the module)."""
        totals = compute_totals(draft, self._regime)
        where = route(draft, totals, self._regime)
        signed_at = self._clock()
        signed = await self._signer.sign_document(self._xml.ecf(draft, totals, signed_at=signed_at))
        code = security_code(self._xml.signed(draft, signed, rfce=False))
        issuer = draft.issuer.rnc
        if where is Route.RFCE:
            summary = await self._signer.sign_document(self._xml.rfce(draft, totals, security_code=code))
            self._xml.signed(draft, summary, rfce=True)
            answer = await self._authority.submit_rfce(self._environment, issuer, draft.encf, summary)
            outcome = _refused(answer) if isinstance(answer, Refused) else _decided(answer, rfce=True)
            qr = rfce_qr_url(
                environment=self._environment,
                issuer_rnc=issuer,
                encf=draft.encf,
                total=totals.total,
                security_code=code,
                hosts=self._qr_hosts,
            )
            return Emitted(
                draft=draft,
                totals=totals,
                route=where,
                signed_at=signed_at,
                signed_ecf=signed,
                signed_rfce=summary,
                security_code=code,
                qr=qr,
                track_id=None,
                outcome=outcome,
            )
        track_id, outcome = await self._submit(draft, signed)
        qr = qr_url(
            environment=self._environment,
            issuer_rnc=issuer,
            buyer_rnc=draft.buyer.rnc,
            encf=draft.encf,
            issued_on=draft.issued_on,
            total=totals.total,
            signed_at=signed_at,
            security_code=code,
            hosts=self._qr_hosts,
        )
        return Emitted(
            draft=draft,
            totals=totals,
            route=where,
            signed_at=signed_at,
            signed_ecf=signed,
            signed_rfce=None,
            security_code=code,
            qr=qr,
            track_id=track_id,
            outcome=outcome,
        )

    async def _reception(self, draft: EcfDraft, signed: bytes) -> Reception:
        issuer = draft.issuer.rnc
        try:
            return await self._authority.submit_ecf(self._environment, issuer, draft.encf, signed)
        except OutcomeUnknownError:
            known = await self._authority.track_ids(self._environment, issuer, draft.encf)
            if known is None:
                raise
            if known:
                return Received(known[-1])
            return await self._authority.submit_ecf(self._environment, issuer, draft.encf, signed)

    async def _submit(self, draft: EcfDraft, signed: bytes) -> tuple[str | None, Outcome | None]:
        reception = await self._reception(draft, signed)
        if isinstance(reception, Refused):
            return None, _refused(reception)
        outcome: Outcome | None = None
        for delay in self._schedule:
            await self._sleep(delay)
            outcome = _decided(await self._authority.result(self._environment, reception.track_id), rfce=False)
            if outcome.final:
                break
        return reception.track_id, outcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest countries/do/tests/unit/test_emission_flow.py countries/do/tests/integration/test_emission.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %; `lint-imports` keeps "Channel do-ecf-v1 is layered" (the application imports ports and the domain only; `DgiiXml` and the client are adapters).

- [ ] **Step 6: Commit**

```bash
git add countries/do
git commit -m "feat(do): emission end to end: sign, route, RFCE or reception, poll, Aceptado Condicional, unknown outcomes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Delivery to an electronic buyer: directory, the buyer's seed authentication, a verified ARECF

> **Revised by Task 13's reviews (binding; the committed code is the reference, the code blocks below are the
> first draft).** `BuyerClient(signer, directory, *, trust, timeouts, transport, resolve, clock, audit)`.
> Directory URLs are attacker-controlled: `adapters/buyers/endpoints.py` checks them (https, public host name,
> no userinfo or IP literal), resolves them, refuses any non-public address (IPv6 global unicast only, no
> 6to4/NAT64), and sends to the checked addresses in turn with the name kept for TLS and `Host`. The ARECF is
> schema-checked, verified (`INBOUND`), matched, refused when received with a reason, and its certificate must
> pass `adapters/signing/trust.CounterpartyTrust` (configured roots, validity at `FechaHoraAcuseRecibo` and at
> receipt, usage, EKU, identity: `serialNumber` bare/`IDCDO-`/`TINDO-`/`PNODO-`, `organizationIdentifier`
> `VATDO-`/`NTRDO-`). A 200 answer whose receipt fails is invalid (not retried). Every buyer seed signed is
> audited (`SeedSigned`). Rulings for later: the TesteCF simulator's identity (step 7 below); a duplicate
> ARECF (reason 3) means "already received" only after an earlier unknown-outcome delivery of the same e-CF,
> else a conflict (v2-M4); revocation blocks production delivery (v2-M4); buyer authentication is a
> per-company switch, off unless a buyer requires it (v2-M4); production needs an egress firewall.

**Files:**
- Create: `countries/do/src/ledger_country_do/channels/ecf_v1/adapters/buyers/{__init__, client}.py`
- Test: `countries/do/tests/integration/test_buyer_delivery.py`

**Interfaces:**
- Consumes: `XmlSigner` (Task 6); `DgiiClient.directory`, `DirectoryEntry`, `DgiiTimeouts`, `DEFAULT_TIMEOUTS`, `MAX_ANSWER`, `wire.TokenAnswer` (Task 11); `checked`, `texts`, `Format`, `verify_enveloped`, `SignatureInvalidError` (Tasks 3, 6); `EcfDraft`, `Environment` (Tasks 4, 7).
- Produces: `adapters.buyers.client`: `RECEPTION_PATH`, `SEED_PATH`, `VALIDATION_PATH`, `type DirectoryLookup = Callable[[Environment, str], Awaitable[DirectoryEntry | None]]`, `Delivered(arecf, received, reason, certificate)`, `NotApplicable(reason)`, `type Delivery = Delivered | NotApplicable`, `BuyerUnavailableError` (`do_ecf_buyer_unavailable`), `ArecfMismatchError` (`do_ecf_arecf_mismatch`), `BuyerClient(signer, directory, *, timeouts=DEFAULT_TIMEOUTS, transport=None)` with `opened()` and `deliver(environment, draft, signed) -> Delivery`.

- [ ] **Step 1: Write the failing test**

`countries/do/tests/integration/test_buyer_delivery.py`:

```python
from typing import Final
lazy from collections.abc import AsyncIterator

import httpx
import pytest

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import (
    ArecfMismatchError,
    BuyerClient,
    BuyerUnavailableError,
    Delivered,
    NotApplicable,
)
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient, DirectoryEntry
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import SignatureInvalidError
from ledger_country_do.channels.ecf_v1.adapters.xml import ecf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import SIGNED_AT, consumer_invoice, credit_invoice
from ledger_country_do_testing.transports import FAKE_HOSTS

T: Final = Environment.TESTECF
SIMULATOR: Final = f"{FAKE_HOSTS.ecf}/testecf/emisorreceptor"


async def nobody(environment: Environment, rnc: str) -> DirectoryEntry | None:
    del environment, rnc
    return None


async def simulator(environment: Environment, rnc: str) -> DirectoryEntry | None:
    del environment
    return DirectoryEntry("EMISOR RECEPTOR DGII", rnc, SIMULATOR, SIMULATOR, SIMULATOR)


@pytest.fixture
async def buyers(signer: KeyPairSigner, dgii: DgiiClient, fake: FakeDgii) -> AsyncIterator[BuyerClient]:
    async with BuyerClient(signer, dgii.directory, transport=httpx.ASGITransport(app=fake.app)).opened() as client:
        yield client


async def signed(signer: KeyPairSigner, sequence: int = 1) -> bytes:
    draft = credit_invoice(sequence)
    return await signer.sign_document(ecf.render(draft, compute_totals(draft), signed_at=SIGNED_AT))


async def test_an_accepted_e_cf_reaches_the_buyer_who_signs_its_receipt(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    delivery = await buyers.deliver(T, credit_invoice(), await signed(signer))
    assert isinstance(delivery, Delivered)
    assert (delivery.received, delivery.reason) == (True, None)
    assert delivery.certificate == fake.state.receiver.certificate
    assert (fake.received[-1].kind, fake.received[-1].file_name) == ("buyer", "101850043E310000000001.xml")


async def test_without_the_buyer_s_authentication_and_a_second_time(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    fake.state.receiver_requires_auth = False
    document = await signed(signer)
    first = await buyers.deliver(T, credit_invoice(), document)
    again = await buyers.deliver(T, credit_invoice(), document)
    assert isinstance(first, Delivered)
    assert isinstance(again, Delivered)
    assert (first.received, again.received, again.reason) == (True, False, 3)


async def test_a_receipt_whose_signature_fails_is_refused(buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii) -> None:
    fake.state.tamper_arecf = True
    with pytest.raises(SignatureInvalidError):
        await buyers.deliver(T, credit_invoice(), await signed(signer))


async def test_a_receipt_about_another_document_is_refused(buyers: BuyerClient, signer: KeyPairSigner) -> None:
    with pytest.raises(ArecfMismatchError):
        await buyers.deliver(T, credit_invoice(2), await signed(signer, 1))


async def test_delivery_does_not_apply_without_an_identified_electronic_buyer(
    buyers: BuyerClient, signer: KeyPairSigner, fake: FakeDgii
) -> None:
    assert await buyers.deliver(T, consumer_invoice(), b"") == NotApplicable("the buyer has no RNC or cédula")
    async with BuyerClient(signer, nobody, transport=httpx.ASGITransport(app=fake.app)).opened() as client:
        found = await client.deliver(T, credit_invoice(), b"")
    assert found == NotApplicable("the buyer is not an electronic receiver")


async def test_a_buyer_that_fails_is_unavailable(signer: KeyPairSigner, fake: FakeDgii) -> None:
    async with BuyerClient(signer, simulator, transport=httpx.ASGITransport(app=fake.app)).opened() as client:
        fake.script.outage = 1
        with pytest.raises(BuyerUnavailableError, match="its seed"):
            await client.deliver(T, credit_invoice(), await signed(signer))


@pytest.mark.parametrize(
    ("answer", "step"),
    [(b"not json", "its authentication"), (None, "its seed")],
    ids=["token-not-json", "unreachable"],
)
async def test_a_buyer_whose_answers_cannot_be_read_or_reached_is_unavailable(
    signer: KeyPairSigner, answer: bytes | None, step: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if answer is None:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/semilla"):
            return httpx.Response(200, content=b"<SemillaModel><valor>v</valor><fecha>f</fecha></SemillaModel>")
        return httpx.Response(200, content=answer)

    async with BuyerClient(signer, simulator, transport=httpx.MockTransport(handler)).opened() as client:
        with pytest.raises(BuyerUnavailableError, match=step):
            await client.deliver(T, credit_invoice(), await signed(signer))


async def test_a_client_not_opened_delivers_nothing(signer: KeyPairSigner) -> None:
    with pytest.raises(BuyerUnavailableError, match="not opened"):
        await BuyerClient(signer, simulator).deliver(T, credit_invoice(), await signed(signer))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest countries/do/tests/integration/test_buyer_delivery.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger_country_do.channels.ecf_v1.adapters.buyers'`.

- [ ] **Step 3: Implement**

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/buyers/__init__.py`:

```python
"""Buyers' receivers: delivering accepted e-CF to electronic buyers and checking their signed receipts."""
```

`countries/do/src/ledger_country_do/channels/ecf_v1/adapters/buyers/client.py`:

```python
"""Delivering an accepted e-CF to an electronic buyer (spec §8.6.5 steps 2-4; DT-EMI; DT-DGII's directory).

The buyer is looked up in DGII's directory; no entry means it is no electronic receiver, and delivery does not
apply (the client hands it the printed representation). When the buyer publishes an authentication URL, its seed
is signed and exchanged for its token (``validacioncertificado``: issuer to buyer, DGII is not in the middle).
The signed e-CF is posted to its reception (multipart ``xml``, named RNC + e-NCF), and the answer must be a
**signed ARECF**: valid for DGII's schema, its signature holding (``verify_enveloped``), and naming this very
document (issuer, buyer, e-NCF). ``Estado`` 0 is received; 1 is not, with DGII's reason code. The directory's
URLs are bases to which DGII's standard paths are appended (F5). The receipt is the caller's to archive; the
``delivering`` state machine and its retries are v2-M4's.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
from pydantic import TypeAdapter, ValidationError

from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DEFAULT_TIMEOUTS, MAX_ANSWER
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import TokenAnswer
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import checked
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError, EcfUnavailableError
lazy from cryptography import x509

lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiTimeouts, DirectoryEntry
lazy from ledger_country_do.channels.ecf_v1.application.ports import XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

RECEPTION_PATH: Final = "/fe/recepcion/api/ecf"
SEED_PATH: Final = "/fe/autenticacion/api/semilla"
VALIDATION_PATH: Final = "/fe/autenticacion/api/validacioncertificado"
_OK: Final = 200
_RECEIVED: Final = "0"
_TOKEN: Final = TypeAdapter(TokenAnswer)
_DETAIL: Final = "DetalleAcusedeRecibo/"

type DirectoryLookup = Callable[[Environment, str], Awaitable[DirectoryEntry | None]]


@final
@dataclass(frozen=True, slots=True)
class Delivered:
    """The buyer answered a signed receipt: received, or not with DGII's reason code."""

    arecf: bytes
    received: bool
    reason: int | None
    certificate: x509.Certificate


@final
@dataclass(frozen=True, slots=True)
class NotApplicable:
    """Delivery does not apply, and why."""

    reason: str


type Delivery = Delivered | NotApplicable


class BuyerUnavailableError(EcfUnavailableError):
    """The buyer's receiver failed or answered something other than what DGII's format says: retried later."""

    code: ClassVar[str] = "do_ecf_buyer_unavailable"

    def __init__(self, step: str) -> None:
        super().__init__(f"the buyer's receiver failed at {step}")


class ArecfMismatchError(EcfInvalidError):
    """A receipt about another document than the one delivered."""

    code: ClassVar[str] = "do_ecf_arecf_mismatch"

    def __init__(self) -> None:
        super().__init__("the buyer's receipt names another issuer, buyer or e-NCF")


@final
class BuyerClient:
    """Delivers to buyers' receivers (see the module); ``opened`` holds its connections."""

    __slots__ = ("_directory", "_http", "_signer", "_timeouts", "_transport")

    def __init__(
        self,
        signer: XmlSigner,
        directory: DirectoryLookup,
        *,
        timeouts: DgiiTimeouts = DEFAULT_TIMEOUTS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._signer = signer
        self._directory = directory
        self._timeouts = timeouts
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[BuyerClient]:
        """This client with its connections open, for the length of the block."""
        timeout = httpx.Timeout(self._timeouts.total, connect=self._timeouts.connect)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=self._transport
        ) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None

    def _opened(self) -> httpx.AsyncClient:
        if self._http is None:
            raise BuyerUnavailableError("a client not opened")
        return self._http

    async def _send(self, request: httpx.Request, step: str) -> httpx.Response:
        try:
            response = await self._opened().send(request)
        except httpx.TransportError as error:
            raise BuyerUnavailableError(step) from error
        if response.status_code != _OK or len(response.content) > MAX_ANSWER:
            raise BuyerUnavailableError(step)
        return response

    async def _token(self, base: str) -> str:
        http = self._opened()
        seed = await self._send(http.build_request("GET", base + SEED_PATH), "its seed")
        signed = await self._signer.sign_seed(seed.content)
        request = http.build_request(
            "POST",
            base + VALIDATION_PATH,
            files={"xml": ("semilla.xml", signed, "text/xml")},
            headers={"Accept": "application/json"},
        )
        answer = await self._send(request, "its authentication")
        try:
            return _TOKEN.validate_json(answer.content).token
        except ValidationError as error:
            raise BuyerUnavailableError("its authentication") from error

    async def deliver(self, environment: Environment, draft: EcfDraft, signed: bytes) -> Delivery:
        """``signed``, ``draft``'s accepted e-CF, delivered to its buyer (see the module)."""
        buyer = draft.buyer.rnc
        if buyer is None:
            return NotApplicable("the buyer has no RNC or cédula")
        entry = await self._directory(environment, buyer)
        if entry is None:
            return NotApplicable("the buyer is not an electronic receiver")
        http = self._opened()
        token = None if entry.authentication is None else await self._token(entry.authentication)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        files = {"xml": (f"{draft.issuer.rnc}{draft.encf}.xml", signed, "text/xml")}
        request = http.build_request("POST", entry.reception + RECEPTION_PATH, files=files, headers=headers)
        arecf = (await self._send(request, "its reception")).content
        checked(Format.ARECF, arecf, unsigned=False)
        verified = verify_enveloped(arecf)
        issuer, receiver, encf, state, reason = texts(
            arecf,
            f"{_DETAIL}RNCEmisor",
            f"{_DETAIL}RNCComprador",
            f"{_DETAIL}eNCF",
            f"{_DETAIL}Estado",
            f"{_DETAIL}CodigoMotivoNoRecibido",
        )
        if (issuer, receiver, encf) != (draft.issuer.rnc, buyer, str(draft.encf)):
            raise ArecfMismatchError
        return Delivered(
            arecf=arecf,
            received=state == _RECEIVED,
            reason=None if reason is None else int(reason),
            certificate=verified.certificate,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest countries/do/tests/integration/test_buyer_delivery.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Gate**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add countries/do
git commit -m "feat(do): delivery to electronic buyers: directory, buyer's seed auth, signed ARECF verified and matched

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `ledger-dgii-spike`, the TesteCF runbook, and the findings record

**Files:**
- Create: `countries/do/testing/src/ledger_country_do_testing/spike.py`
- Create: `countries/do/docs/runbooks/testecf-spike.md`, `countries/do/docs/spike-findings.md`
- Modify: `countries/do/testing/pyproject.toml` (`[project.scripts]` gains `ledger-dgii-spike = "ledger_country_do_testing.spike:main"`; dependencies gain `"httpx>=0.28.1"`), `_typos.toml` (only the Spanish words `typos` reports in the two documents)
- Test: `countries/do/testing/tests/test_spike_cli.py`

**Interfaces:**
- Consumes: everything of Tasks 1-13: `Emitter`, `DgiiXml`, `DgiiClient`, `DgiiHosts`, `BuyerClient`, `Delivered`, `KeyPairSigner.from_pkcs12`, `samples`, `Environment`, `Encf`, `EcfType`, `Issuer`, `Buyer`, `EcfError`, `DOCUMENTED`, `QrHosts`, `local_date`.
- Produces: `ledger_country_do_testing.spike`: `PREFIX` (`LEDGER_DO_ECF_SPIKE_`), `USED_FILE` (`used-encf.txt`), `SettingsError`, `SpikeSettings.from_environ(environ)`, `run(argv, environ, *, out, transport=None, sleep=asyncio.sleep, clock=...) -> int` (0 accepted, 1 rejected/pending/unavailable, 2 usage or settings), `main(argv=None)`.

- [ ] **Step 1: Dependencies and script**

`countries/do/testing/pyproject.toml`: dependencies gain `"httpx>=0.28.1"`; `[project.scripts]` becomes:

```toml
[project.scripts]
ledger-fake-dgii = "ledger_country_do_testing.fake_dgii.serve:main"
ledger-dgii-spike = "ledger_country_do_testing.spike:main"
```

Run: `uv lock && uv sync`.

- [ ] **Step 2: Write the failing test**

`countries/do/testing/tests/test_spike_cli.py`:

```python
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
import pytest

from ledger_country_do_testing import spike
from ledger_country_do_testing.fake_dgii.app import DEFAULT_PUBLIC_URL, FakeDgii
from ledger_country_do_testing.fake_dgii.state import Scripted, Status
from ledger_country_do_testing.pki import Issued, bundle

PASSWORD: Final = "a password nobody prints"


@pytest.fixture
def environ(tmp_path: Path, issued: Issued) -> dict[str, str]:
    (tmp_path / "certificate.p12").write_bytes(bundle(issued, password=PASSWORD))
    (tmp_path / "certificate.password").write_text(PASSWORD + "\n", encoding="utf-8")
    return {
        "LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "testecf",
        "LEDGER_DO_ECF_SPIKE_P12": str(tmp_path / "certificate.p12"),
        "LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": str(tmp_path / "certificate.password"),
        "LEDGER_DO_ECF_SPIKE_RNC": "101850043",
        "LEDGER_DO_ECF_SPIKE_LEGAL_NAME": "FERRETERIA DUARTE SRL",
        "LEDGER_DO_ECF_SPIKE_ADDRESS": "AV. DUARTE 100, SANTO DOMINGO",
        "LEDGER_DO_ECF_SPIKE_BUYER_RNC": "131098193",
        "LEDGER_DO_ECF_SPIKE_BUYER_NAME": "CONSTRUCTORA DEL CARIBE SRL",
        "LEDGER_DO_ECF_SPIKE_ECF_HOST": DEFAULT_PUBLIC_URL,
        "LEDGER_DO_ECF_SPIKE_FC_HOST": "http://fc.fake.test",
        "LEDGER_DO_ECF_SPIKE_OUT": str(tmp_path / "out"),
    }


async def run(fake: FakeDgii, environ: dict[str, str], *argv: str) -> tuple[int, str]:
    async def sleep(seconds: float) -> None:
        del seconds

    out = io.StringIO()
    code = await spike.run(
        list(argv),
        environ,
        out=out,
        transport=httpx.ASGITransport(app=fake.app),
        sleep=sleep,
        clock=lambda: datetime(2026, 10, 13, 13, 30, tzinfo=UTC),
    )
    return code, out.getvalue()


def assert_no_secret(output: str) -> None:
    assert PASSWORD not in output
    assert "Bearer" not in output
    assert "BEGIN" not in output


async def test_auth_obtains_a_token_and_shows_none_of_it(fake: FakeDgii, environ: dict[str, str]) -> None:
    code, output = await run(fake, environ, "auth")
    assert code == 0
    assert "authenticated with testecf" in output
    assert_no_secret(output)


async def test_a_credit_invoice_is_emitted_written_and_recorded(fake: FakeDgii, environ: dict[str, str]) -> None:
    code, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "1")
    assert code == 0
    assert "E310000000001" in output
    assert "codigo 1 Aceptado" in output
    assert "consultatimbre?RncEmisor=101850043&RncComprador=131098193" in output
    out = Path(environ["LEDGER_DO_ECF_SPIKE_OUT"])
    assert (out / "101850043E310000000001.xml").read_bytes() == fake.received[-1].document
    assert (out / "used-encf.txt").read_text(encoding="utf-8").split() == ["E310000000001"]
    assert '"codigo": 1' in (out / "101850043E310000000001.json").read_text(encoding="utf-8")
    assert_no_secret(output)
    again, refusal = await run(fake, environ, "emit", "--type", "31", "--sequence", "1")
    assert (again, "E310000000001 was already used" in refusal) == (2, True)


async def test_a_small_consumer_invoice_goes_as_an_rfce(fake: FakeDgii, environ: dict[str, str]) -> None:
    code, output = await run(fake, environ, "emit", "--type", "32", "--sequence", "1")
    assert code == 0
    assert "route: rfce" in output
    assert "consultatimbrefc?" in output
    assert (Path(environ["LEDGER_DO_ECF_SPIKE_OUT"]) / "101850043E320000000001.rfce.xml").exists()


async def test_a_large_or_named_consumer_invoice_names_its_buyer(fake: FakeDgii, environ: dict[str, str]) -> None:
    large, output = await run(fake, environ, "emit", "--type", "32", "--sequence", "2", "--large")
    assert (large, "route: reception" in output) == (0, True)
    named, _ = await run(fake, environ, "emit", "--type", "32", "--sequence", "3", "--named")
    assert named == 0


async def test_an_accepted_credit_invoice_is_delivered_to_its_buyer(fake: FakeDgii, environ: dict[str, str]) -> None:
    code, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "4", "--deliver")
    assert code == 0
    assert "buyer: received" in output
    assert (Path(environ["LEDGER_DO_ECF_SPIKE_OUT"]) / "101850043E310000000004.arecf.xml").exists()
    anonymous, summary = await run(fake, environ, "emit", "--type", "32", "--sequence", "4", "--deliver")
    assert (anonymous, "buyer: the buyer has no RNC or cédula" in summary) == (0, True)


async def test_a_rejection_and_a_pending_document_exit_one(fake: FakeDgii, environ: dict[str, str]) -> None:
    fake.script.default = Scripted(status=Status.REJECTED, messages=(("145", "El RNC no es emisor"),), sequence_used=False)
    rejected, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "5")
    assert (rejected, "number released" in output, "145: El RNC no es emisor" in output) == (1, True, True)
    fake.script.default = Scripted(processing=99)
    pending, waiting = await run(fake, environ, "emit", "--type", "31", "--sequence", "6", "--deliver")
    assert (pending, "still pending" in waiting, "buyer:" in waiting) == (1, True, False)


async def test_dgii_unavailable_exits_one_with_the_error_s_code(fake: FakeDgii, environ: dict[str, str]) -> None:
    fake.script.outage = -1
    code, output = await run(fake, environ, "emit", "--type", "31", "--sequence", "7")
    assert code == 1
    assert "do_ecf_dgii_unavailable" in output


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "ecf"}, "never reaches the live environment"),
        ({"LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "prod"}, "is testecf or certecf"),
        ({"LEDGER_DO_ECF_SPIKE_RNC": ""}, "LEDGER_DO_ECF_SPIKE_RNC is required"),
        ({"LEDGER_DO_ECF_SPIKE_RNC": "12"}, "LEDGER_DO_ECF_SPIKE_RNC"),
        ({"LEDGER_DO_ECF_SPIKE_SEQUENCE_EXPIRES": "2028-12-31"}, "dd-mm-yyyy"),
        ({"LEDGER_DO_ECF_SPIKE_P12": "missing.p12"}, "names no readable file"),
        ({"LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": "missing.txt"}, "names no readable file"),
    ],
    ids=["live", "unknown", "no-rnc", "bad-rnc", "bad-expiry", "no-bundle", "no-password"],
)
async def test_settings_are_checked_and_named_never_echoed(
    fake: FakeDgii, environ: dict[str, str], change: dict[str, str], expected: str
) -> None:
    code, output = await run(fake, {**environ, **change}, "auth")
    assert (code, expected in output) == (2, True)
    assert_no_secret(output)


async def test_a_wrong_password_is_reported_without_it(fake: FakeDgii, environ: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "wrong.password").write_text("not it", encoding="utf-8")
    code, output = await run(fake, {**environ, "LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE": str(tmp_path / "wrong.password")}, "auth")
    assert (code, "does not open" in output, "not it" in output) == (2, True, False)


async def test_documents_the_draft_rules_refuse_exit_two(fake: FakeDgii, environ: dict[str, str]) -> None:
    no_buyer = {key: value for key, value in environ.items() if not key.startswith("LEDGER_DO_ECF_SPIKE_BUYER")}
    code, output = await run(fake, no_buyer, "emit", "--type", "31", "--sequence", "8")
    assert (code, "do_ecf_draft_invalid" in output) == (2, True)
    zero, _ = await run(fake, environ, "emit", "--type", "31", "--sequence", "0")
    assert zero == 2


def test_main_runs_the_cli_and_exits_with_its_code(monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]) -> None:
    for key, value in {**environ, "LEDGER_DO_ECF_SPIKE_ENVIRONMENT": "ecf"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as caught:
        spike.main(["auth"])
    assert caught.value.code == 2
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest countries/do/testing/tests/test_spike_cli.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'spike'`.

- [ ] **Step 4: Implement**

`countries/do/testing/src/ledger_country_do_testing/spike.py`:

```python
"""``ledger-dgii-spike``: the spike's emission, run by hand against DGII's TesteCF or CerteCF, or the fake.

Read ``countries/do/docs/runbooks/testecf-spike.md`` first. Settings come from ``LEDGER_DO_ECF_SPIKE_*``
environment variables; the certificate and its password come from files (under the git-ignored ``.dgii/``)
whose paths two of them name. The CLI prints what DGII answered and where the documents were written; it never
prints a token, a password, a key or a certificate, and it refuses the live environment. Every e-NCF it sends
is recorded before it is sent (``used-encf.txt``) and never sent again.

Exit codes: 0 accepted (or accepted with observations), 1 rejected, still pending, or DGII unavailable, 2 a
setting or an argument refused.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, TextIO, final
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence

import httpx

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient, Delivered
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
from ledger_country_do.channels.ecf_v1.adapters.xml.documents import DgiiXml
from ledger_country_do.channels.ecf_v1.application.emission import Emitter
from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer, Issuer
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST, Environment
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
from ledger_country_do.channels.ecf_v1.domain.notation import local_date
from ledger_country_do.channels.ecf_v1.domain.printed import QrHosts
from ledger_country_do.channels.ecf_v1.domain.status_map import DOCUMENTED, FiscalStatus
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice
lazy from ledger_country_do.channels.ecf_v1.application.emission import Emitted

PREFIX: Final = "LEDGER_DO_ECF_SPIKE_"
USED_FILE: Final = "used-encf.txt"
DEFAULT_OUT: Final = ".dgii/out"
DEFAULT_EXPIRY: Final = "31-12-2028"
_ALLOWED: Final = frozenset({Environment.TESTECF, Environment.CERTECF})
_ACCEPTED: Final = frozenset({FiscalStatus.ACCEPTED, FiscalStatus.ACCEPTED_WITH_OBSERVATIONS})
_USAGE: Final = 2
_NOT_ACCEPTED: Final = 1


class SettingsError(Exception):
    """A setting refused; the message names the variable, never its value."""


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(PREFIX + name, "")
    if not value.strip():
        raise SettingsError(f"{PREFIX}{name} is required")
    return value


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SpikeSettings:
    """What the spike needs, read from the environment (see the module)."""

    environment: Environment
    bundle: Path
    password_file: Path
    issuer: Issuer
    buyer: Buyer
    hosts: DgiiHosts
    out: Path
    sequence_expires_on: date

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> SpikeSettings:
        """The settings; ``SettingsError`` naming the first variable refused."""
        try:
            environment = Environment(environ.get(PREFIX + "ENVIRONMENT", Environment.TESTECF.value))
        except ValueError as error:
            raise SettingsError(f"{PREFIX}ENVIRONMENT is testecf or certecf") from error
        if environment not in _ALLOWED:
            raise SettingsError(f"{PREFIX}ENVIRONMENT is testecf or certecf: the spike never reaches the live environment")
        try:
            issuer = Issuer(
                rnc=_required(environ, "RNC"),
                legal_name=_required(environ, "LEGAL_NAME"),
                address=_required(environ, "ADDRESS"),
            )
            buyer = Buyer(rnc=environ.get(PREFIX + "BUYER_RNC"), name=environ.get(PREFIX + "BUYER_NAME"))
        except EcfError as error:
            raise SettingsError(f"{PREFIX}RNC, LEGAL_NAME, ADDRESS or BUYER_*: {error}") from error
        try:
            expires = datetime.strptime(environ.get(PREFIX + "SEQUENCE_EXPIRES", DEFAULT_EXPIRY), "%d-%m-%Y").date()
        except ValueError as error:
            raise SettingsError(f"{PREFIX}SEQUENCE_EXPIRES is a date written dd-mm-yyyy") from error
        return cls(
            environment=environment,
            bundle=Path(_required(environ, "P12")),
            password_file=Path(_required(environ, "P12_PASSWORD_FILE")),
            issuer=issuer,
            buyer=buyer,
            hosts=DgiiHosts(
                ecf=environ.get(PREFIX + "ECF_HOST", OFFICIAL_ECF_HOST),
                fc=environ.get(PREFIX + "FC_HOST", OFFICIAL_FC_HOST),
            ),
            out=Path(environ.get(PREFIX + "OUT", DEFAULT_OUT)),
            sequence_expires_on=expires,
        )

    def signer(self) -> KeyPairSigner:
        """The signer of the certificate the two files hold; ``SettingsError`` if they do not open."""
        try:
            data = self.bundle.read_bytes()
        except OSError as error:
            raise SettingsError(f"{PREFIX}P12 names no readable file") from error
        try:
            password = self.password_file.read_text(encoding="utf-8").strip("\r\n").encode()
        except OSError as error:
            raise SettingsError(f"{PREFIX}P12_PASSWORD_FILE names no readable file") from error
        try:
            return KeyPairSigner.from_pkcs12(data, password)
        except ValueError as error:
            raise SettingsError("the PKCS#12 bundle does not open with the password file's password") from error


@final
class _Options(argparse.Namespace):
    command: str = ""
    ecf_type: str = "31"
    sequence: int = 0
    large: bool = False
    named: bool = False
    deliver: bool = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger-dgii-spike", description="The DGII spike, run by hand.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="obtain a DGII token (not shown)")
    emit = commands.add_parser("emit", help="emit one e-CF of a sample")
    emit.add_argument("--type", dest="ecf_type", choices=["31", "32"], required=True)
    emit.add_argument("--sequence", type=int, required=True, help="the e-NCF's sequence: never one used before")
    emit.add_argument("--large", action="store_true", help="type 32 of DOP 295,000 (sent in full)")
    emit.add_argument("--named", action="store_true", help="type 32 naming the configured buyer")
    emit.add_argument("--deliver", action="store_true", help="once accepted, deliver to the buyer's receiver")
    return parser


def _now() -> datetime:
    return datetime.now(UTC)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _report(emitted: Emitted, settings: SpikeSettings, out: TextIO) -> None:
    signed = emitted.signed
    stem = f"{settings.issuer.rnc}{signed.draft.encf}"
    print(f"e-NCF: {signed.draft.encf}  route: {signed.route.value}  track id: {emitted.track_id}", file=out)
    outcome = emitted.outcome
    if emitted.unknown is not None:
        sent = {True: "DGII answered about it", False: "it never left", None: "it may be at DGII"}
        print(f"parked: {emitted.unknown.code} ({sent[emitted.unknown.sent]}); never resend this e-NCF", file=out)
    elif outcome is None or not outcome.final:
        print("DGII: still pending after the polling schedule; ask consultaresultado for the track id later", file=out)
    else:
        number = "" if outcome.number is None else f" (number {outcome.number.value})"
        print(f"DGII: codigo {outcome.code.value} {DOCUMENTED[outcome.code.value]} -> {outcome.status.value}{number}", file=out)
        for message in outcome.messages:
            print(f"  {message.code}: {message.text}", file=out)
    print(f"security code: {signed.security_code}", file=out)
    print(f"QR: {signed.qr}", file=out)
    written: list[Path] = []
    summary = {
        "encf": str(signed.draft.encf),
        "route": signed.route.value,
        "track_id": emitted.track_id,
        "parked": None if emitted.unknown is None else emitted.unknown.code,
        "codigo": None if outcome is None else outcome.code.value,
        "status": None if outcome is None else outcome.status.value,
        "messages": [] if outcome is None else [[message.code, message.text] for message in outcome.messages],
        "security_code": signed.security_code,
        "qr": signed.qr,
    }
    written.append(_write(settings.out / f"{stem}.json", json.dumps(summary, indent=2).encode()))
    for path in written:
        print(f"written: {path}", file=out)


async def _emit(
    options: _Options,
    settings: SpikeSettings,
    signer: KeyPairSigner,
    dgii: DgiiClient,
    *,
    out: TextIO,
    transport: httpx.AsyncBaseTransport | None,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], datetime],
) -> int:
    ecf_type = EcfType(options.ecf_type)
    encf = Encf(ecf_type, options.sequence)
    used = settings.out / USED_FILE
    already = used.read_text(encoding="utf-8").split() if used.exists() else []
    if str(encf) in already:
        print(f"error: {encf} was already used; pick a sequence never sent", file=out)
        return _USAGE
    if ecf_type is EcfType.CREDIT_INVOICE:
        draft = replace(
            credit_invoice(options.sequence, issuer=settings.issuer, buyer=settings.buyer),
            issued_on=local_date(clock()),
            sequence_expires_on=settings.sequence_expires_on,
        )
    else:
        buyer = settings.buyer if options.large or options.named else Buyer()
        draft = replace(
            consumer_invoice(options.sequence, large=options.large, buyer=buyer, issuer=settings.issuer),
            issued_on=local_date(clock()),
        )
    _write(used, "\n".join([*already, str(encf)]).encode() + b"\n")
    emitter = Emitter(
        xml=DgiiXml(),
        signer=signer,
        authority=dgii,
        environment=settings.environment,
        clock=clock,
        sleep=sleep,
        qr_hosts=QrHosts(ecf=settings.hosts.ecf, fc=settings.hosts.fc),
    )
    signed = await emitter.sign(draft)
    stem = f"{settings.issuer.rnc}{draft.encf}"
    _write(settings.out / f"{stem}.xml", signed.signed_ecf)
    if signed.signed_rfce is not None:
        _write(settings.out / f"{stem}.rfce.xml", signed.signed_rfce)
    emitted = await emitter.send(signed)
    _report(emitted, settings, out)
    if emitted.outcome is None or emitted.outcome.status not in _ACCEPTED:
        return _NOT_ACCEPTED
    if options.deliver:
        async with BuyerClient(signer, dgii.directory, transport=transport).opened() as buyers:
            delivery = await buyers.deliver(settings.environment, draft, signed.signed_ecf)
        if isinstance(delivery, Delivered):
            path = _write(settings.out / f"{settings.issuer.rnc}{draft.encf}.arecf.xml", delivery.arecf)
            state = "received" if delivery.received else f"not received (reason {delivery.reason})"
            print(f"buyer: {state}, receipt signed by {delivery.certificate.subject.rfc4514_string()}", file=out)
            print(f"written: {path}", file=out)
        else:
            print(f"buyer: {delivery.reason}", file=out)
    return 0


async def run(
    argv: Sequence[str],
    environ: Mapping[str, str],
    *,
    out: TextIO,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] = _now,
) -> int:
    """The CLI (see the module); returns its exit code."""
    options = _parser().parse_args(argv, namespace=_Options())
    try:
        settings = SpikeSettings.from_environ(environ)
        signer = settings.signer()
    except SettingsError as error:
        print(f"error: {error}", file=out)
        return _USAGE
    print(f"environment: {settings.environment.value}", file=out)
    try:
        async with DgiiClient(signer, hosts=settings.hosts, clock=clock, sleep=sleep, transport=transport).opened() as dgii:
            if options.command == "auth":
                answer = await dgii.result(settings.environment, "ledger-spike-authentication-check")
                print(
                    f"authenticated with {settings.environment.value}: a token was issued (not shown); "
                    f"consultaresultado answered codigo {answer.code}",
                    file=out,
                )
                return 0
            return await _emit(options, settings, signer, dgii, out=out, transport=transport, sleep=sleep, clock=clock)
    except EcfError as error:
        print(f"error: {error.code}: {error}", file=out)
        return _USAGE if error.kind == "invalid" else _NOT_ACCEPTED


def main(argv: Sequence[str] | None = None) -> None:
    """``ledger-dgii-spike``: run the CLI with this process's environment and exit with its code."""
    raise SystemExit(asyncio.run(run(sys.argv[1:] if argv is None else argv, os.environ, out=sys.stdout)))
```

The auth check asks `consultaresultado` for a track id that does not exist: it needs a token, so it proves seed, signature and token end to end, and DGII answers `codigo 0`.

`countries/do/docs/runbooks/testecf-spike.md`:

````markdown
# Runbook: the DGII spike against TesteCF

Runs the spike's emission (`ledger-dgii-spike`) against DGII's pre-certification environment, TesteCF, and
records what DGII's documentation left open (`../spike-findings.md`). Nothing here reaches production: the CLI
refuses the `ecf` environment.

## 1. What the owner provides (once)

- **A taxpayer enrolled for TesteCF**: DGII's portal (Oficina Virtual) → Facturación Electrónica → the
  postulación as electronic issuer; DGII then opens TesteCF to that RNC (spec §19, Q2).
- **A user with the signer's role** for that RNC (DGII's *Instructivo Delegaciones de Roles de Facturación
  Electrónica*): DGII rejects documents signed by someone not delegated (`secuenciaUtilizada = false`).
- **The certificate** of that user, a `.p12`/`.pfx` from an INDOTEL-authorized certification entity, and its
  password. A certificate made by the spike's test PKI is refused by DGII (F9).

## 2. Secrets, kept out of the repository

```powershell
New-Item -ItemType Directory -Force .dgii | Out-Null
Copy-Item <path-to-the-owner's-file>.p12 .dgii/certificate.p12
Set-Content -NoNewline -Encoding utf8 .dgii/certificate.password -Value (Read-Host -AsSecureString "password" | ConvertFrom-SecureString -AsPlainText)
git status --short .dgii   # prints nothing: .dgii/ is ignored; never add it, never commit a .p12
```

Settings for the shell session (names only; never print them back with `Get-ChildItem env:` or `echo`):

```powershell
$env:LEDGER_DO_ECF_SPIKE_ENVIRONMENT = "testecf"
$env:LEDGER_DO_ECF_SPIKE_P12 = ".dgii/certificate.p12"
$env:LEDGER_DO_ECF_SPIKE_P12_PASSWORD_FILE = ".dgii/certificate.password"
$env:LEDGER_DO_ECF_SPIKE_RNC = "<the enrolled RNC>"
$env:LEDGER_DO_ECF_SPIKE_LEGAL_NAME = "<its legal name, as in DGII's registry>"
$env:LEDGER_DO_ECF_SPIKE_ADDRESS = "<its address>"
$env:LEDGER_DO_ECF_SPIKE_BUYER_RNC = "<an RNC to bill in TesteCF, e.g. the owner's other company>"
$env:LEDGER_DO_ECF_SPIKE_BUYER_NAME = "<its legal name>"
$env:LEDGER_DO_ECF_SPIKE_OUT = ".dgii/out"
```

TesteCF accepts sequences 1 to 10,000,000 of every type (type 32 to 50,000,000) expiring 31-12-2028 (the
default of `LEDGER_DO_ECF_SPIKE_SEQUENCE_EXPIRES`), and keeps submissions 60 days ([06]). The CLI records every
e-NCF it sends in `.dgii/out/used-encf.txt` and refuses to send one twice; pick sequences far from any a pilot
uses.

## 3. Dry run against the fake DGII

Terminal 1 (note the PID granian prints; stop it with Ctrl+C or `Stop-Process -Id <pid>`, never by name):

```powershell
uv run ledger-fake-dgii
```

Terminal 2, with the settings above plus the fake's hosts:

```powershell
$env:LEDGER_DO_ECF_SPIKE_ECF_HOST = "http://127.0.0.1:8471"
$env:LEDGER_DO_ECF_SPIKE_FC_HOST = "http://127.0.0.1:8471"
uv run ledger-dgii-spike auth
uv run ledger-dgii-spike emit --type 31 --sequence 9000001 --deliver
uv run ledger-dgii-spike emit --type 32 --sequence 9000001
Remove-Item env:LEDGER_DO_ECF_SPIKE_ECF_HOST, env:LEDGER_DO_ECF_SPIKE_FC_HOST
Remove-Item .dgii/out -Recurse   # the fake's e-NCF ledger must not block TesteCF's sequences
```

Expected: `authenticated with testecf`, then `codigo 1 Aceptado` for the 31 (and `buyer: received`) and
`route: rfce` with `codigo 1` for the 32.

## 4. TesteCF: authentication (F9)

```powershell
uv run ledger-dgii-spike auth
```

Expected: `authenticated with testecf: a token was issued (not shown); consultaresultado answered codigo 0`.
A refusal at `validarsemilla` means the certificate or its delegation: record DGII's message in F9.

## 5. TesteCF: a credit invoice, and a consumer invoice as an RFCE (F1, F2, F11, F12)

```powershell
uv run ledger-dgii-spike emit --type 31 --sequence 1
uv run ledger-dgii-spike emit --type 32 --sequence 1
```

Expected: the 31 gets a track id and `codigo 1 Aceptado` (or `4 Aceptado Condicional`: copy its messages into
F11); the 32 goes `route: rfce` and is answered at once. Its answer settles F1 (the `ecf` host's token works on
`fc`: any 401 there means it does not) and F2 (the code DGII returns), and F12 (a 1,180.00 invoice goes as an
RFCE).

If a run prints `parked:`, its outcome is unknown or it was stopped after signing: **never run that sequence
again**. The signed documents are already in `.dgii/out`; ask DGII by hand (`consultatrackids` for a 31,
`consultarfce` for an RFCE, with its security code) and record what DGII says. The spike never resends.

## 6. TesteCF: an anonymous consumer invoice and the QR codes (F4, F8)

The 32 of step 5 is anonymous (`<Comprador />`): its acceptance settles F4 (an empty required element is
accepted); a rejection naming `Comprador` settles it the other way. Open each printed `QR:` URL in a browser:
DGII's `consultatimbre`/`consultatimbrefc` page must show the document as accepted (F8 for the credit invoice's
buyer parameter).

## 7. TesteCF: the directory and delivery to DGII's simulator (F5, F6)

```powershell
uv run ledger-dgii-spike emit --type 31 --sequence 2 --deliver
```

Expected: `buyer: received, receipt signed by ...` (TesteCF's directory names DGII's emisor-receptor
simulator). A 404 at reception settles F5 the other way (the directory's URLs are full endpoints); a 401 at
`consultadirectorio` would contradict F6's reading.

Expect, too, that the receipt may be **refused** with `do_ecf_counterparty_certificate_refused` ("not the
buyer's"): if DGII's simulator signs its ARECFs with DGII's own certificate rather than one naming the buyer,
the identity check refuses it (and "does not chain to a trusted authority" if DGII's authority is not among
the configured roots). Record which (F15) and the subject's identity attributes (`serialNumber`,
`organizationIdentifier`: their prefixes, never the certificate). If it is DGII's own certificate, the fix is
an explicit stand-in identity limited to TesteCF/CerteCF (refused in production, the chain still required),
not a looser check.

Ask DGII (F14): a buyer's seed has DGII's own `SemillaModel` format, so a hostile buyer endpoint could relay
DGII's seed for our RNC, have us sign it, and obtain a DGII token as us. Does DGII bind its seeds to their
audience, or recommend issuers refuse to sign a buyer seed that also validates at DGII? Every buyer seed the
spike signs is audit-logged (`ledger_country_do.audit.buyer_seeds`: issuer, buyer, endpoint host, time).

## 8. TesteCF: a large consumer invoice, in full

```powershell
uv run ledger-dgii-spike emit --type 32 --sequence 2 --large
```

Expected: `route: reception`, a track id, `codigo 1`.

## 9. Record, then clean up

Fill in `countries/do/docs/spike-findings.md` (one line per F row: settled, how, DGII's words) and commit it.
Copy no secret into it: no token, no password, no certificate, no full XML (DGII's messages and codes are
fine). `.dgii/out` keeps the signed documents and DGII's answers locally; delete it when the findings are
recorded. Never commit `.dgii/`.
````

`countries/do/docs/spike-findings.md`:

```markdown
# DGII spike: findings

What DGII's documentation left open (plan `docs/superpowers/plans/2026-10-13-v2-dgii-spike.md`), what the code
assumes, and what TesteCF answered (runbook `runbooks/testecf-spike.md`). Fill the last two columns from a
TesteCF run; never paste a token, a password, a certificate or a whole document.

| # | Question | Assumed | TesteCF answered (date, step) | Settled |
|---|---|---|---|---|
| F1 | Is the `ecf` host's token accepted by `fc.dgii.gov.do` (RFCE)? | Yes (same JWT in DT-DGII's examples) | | |
| F2 | RFCE reception `codigo` values | 1, 2, 4 as `consultaresultado` | | |
| F3 | A type 32 below RD$250,000 sent to `recepcion` | Refused (never sent by the code) | not exercised | — |
| F4 | An anonymous type 32's empty `<Comprador />` | Accepted | | |
| F5 | Directory URLs: bases or endpoints? | Bases (`/fe/...` appended) | | |
| F6 | `consultadirectorio` authenticated? | Bearer sent anyway | | |
| F7 | Type 32 line limit | 1,000 (XSD) | not exercised | — |
| F8 | `RncComprador` in the QR without a buyer RNC | Left out | | |
| F9 | TesteCF checks the signer's chain and delegation | Yes | | |
| F10 | ITBIS rates before 2019 | Not asserted | not DGII's to answer | — |
| F11 | Conditional-acceptance codes and messages | Kept verbatim | | |
| F12 | `MontoTotal` (ITBIS included) is compared with RD$250,000 | Yes | | |
| F13 | A refusal at the door (HTTP 400) burns the number | Burned (never reuse what DGII may have recorded) | | |
| F14 | Can a buyer relay DGII's seed for the issuer to sign (a signing oracle)? Does DGII bind seeds to their audience? | Not bound: every buyer seed signed is audit-logged; buyer authentication off unless a buyer requires it (v2-M4) | | |
| F15 | Whose certificate signs the TesteCF simulator's ARECFs, and which identity attributes it carries | The buyer's (`serialNumber`/`organizationIdentifier` naming its RNC); DGII's own would be refused | | |

## Known gaps before production (not settled by TesteCF)

- **Egress firewall.** The buyer client refuses non-public addresses after DNS and pins the checked address,
  but a NAT64 gateway with an operator-chosen prefix makes an internal IPv4 address look like a public IPv6
  one, undetectably. The process that delivers needs an egress firewall blocking internal ranges.
- **Revocation.** A buyer's certificate is never checked against CRL or OCSP; production delivery is blocked
  until it is (v2-M4).
- **Buyer authentication** (signing a buyer's seed) is a per-company switch, off unless a buyer requires it
  (v2-M4), because of F14.
- **Duplicate receipts.** An ARECF "not received, duplicate" (reason 3) means "already received" only when the
  same e-CF had an earlier delivery whose outcome is unknown; otherwise it is a conflict (v2-M4).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest countries/do/testing/tests/test_spike_cli.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Gate, then the whole spike once more**

Run: `uv run poe fmt && uv run poe check`
Expected: every step passes, coverage 100 %, `lint-imports` keeps all four DO contracts. Then the dry run of the runbook's §3 by hand (optional; stop the fake by its PID).

- [ ] **Step 7: Commit**

```bash
git add uv.lock _typos.toml countries
git commit -m "feat(do): ledger-dgii-spike, the TesteCF runbook and the findings record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Merge into the local `main`**

```bash
git switch main
git merge --no-ff v2-dgii-spike -m "Merge v2-M1b: the DGII spike (countries/do)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
uv run poe check
```

If the restructure merged first, resolve `pyproject.toml` as Coordination 1 says (keep its generated form, add the spike's entries it does not generate), run `uv lock`, and run the gate before committing the merge. Never push.

---

## After the spike (not in this plan)

- **v2-M2 (signer)** takes `adapters/signing/{profile,signer}.py` into `modules/signer` as profile `do-ecf-v1`; the channel keeps `verifier.py` and `stamp.py`; `KeyPairSigner` stays a test double.
- **v2-M3 (emission)** replaces the provisional regime types and error bases with the kernel's (Coordination 4-5), lifts the "imports nothing of the workspace" contract to allow `ledger_kernel`, `ledger_platform` and `ledger_fiscal_kit`, adds `CHANNEL`/`assembly`, persistence, numbering with released numbers, the e-CF state machine on jobs (the `Emitter`'s steps become `do_ecf.advance` steps), the circuit breaker, the eight other types, ANECF.
- **v2-M4** builds the receiver app on `fake_dgii.receiver`'s shapes, delivery as a sub-flow over `BuyerClient`, ACECF both ways, the printed representation over `domain.printed`, contingency.
- **Spec edits the owner should approve** (raised by this plan, not made by it): §16.3's v2-M1b row (kept code under the gate instead of throwaway); §4.1/ADR 0009's nested-member layout needs the generator rule of Coordination 2; §8.6.3's directory and track-id services are absent in CerteCF.

## Self-review (for the plan author; applied)

1. **Spec coverage.** §8.6.2 regime (tax ids with check digits reported, ITBIS dated, ten types, rounding and tolerance, parameters): Tasks 1-2. §8.6.3 channel (endpoints, seed/token cached with margin, one retry on 401, retries only for what cannot change state, never a blind resubmission, track ids, RFCE on `fc`, directory, live-host refusal): Tasks 11-12. §8.6.4 status map with Aceptado Condicional and `secuenciaUtilizada`: Task 8. §8.6.5 delivery steps 2-4 (directory, buyer auth, signed ARECF verified): Task 13. §8.6.7 security code and QR (consultatimbre/consultatimbrefc): Task 7. §8.4 hardened XML and pinned XSDs with corrections: Task 3. §8.7 golden documents validated by libxml2, signer round trip by xmlsec1, every DGII code mapped, fake-DGII flows (En Proceso loops, conditional acceptance, unknown outcomes, `secuenciaUtilizada` both ways, buyer receiver with and without auth): Tasks 5, 6, 8, 9-13. Out of scope by the brief: types 33-47 builders, numbering/persistence, state machine on jobs, breaker, contingency, printed PDF, ANECF/ACECF sending, receiver app.
2. **Placeholders.** None: every step carries its code, command and expected result.
3. **Type consistency.** `XmlSigner.sign_document/sign_seed`, `Authority.submit_ecf/submit_rfce/result/track_ids`, `Answer(code, messages, sequence_used)`, `Received(track_id)`, `Refused(messages)`, `EcfFormat.ecf/rfce/signed`, `Emitted(...)`, `DirectoryEntry(name, rnc, reception, approval, authentication)`, `FakeDgii(check=, receiver=, clock=, public_url=)` and `FAKE_HOSTS` are spelled the same in every task that uses them.
4. **Review Focus.** Each of its five lines has its tests in the named tasks.
