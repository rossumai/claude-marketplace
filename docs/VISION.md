# Vision: Claude Code + Rossum SA Plugin

> Deliverable for the acquirer-meeting one-slide. Section 1 is the slide content; Section 2 is what the presenter says when the slide is up; Section 3 is the full appendix for follow-up questions.

---

## Section 1 — The slide

**Title:** Claude Code + Rossum SA Plugin — *game-changing efficiency for customer implementations*
**Subtitle:** *Same Solution Architect headcount. Multiples more customers shipped. Higher safety on every change.*
**Tech-stack band (small, under the subtitle):**
> *Built on Anthropic's latest stack — Claude Code · Model Context Protocol · Skills · Claude Opus 4.7 (1M-token context)*

**Proof chip (top-right corner of slide, prominent):**
> 🎯 *Real example, this week: SAP BPT integration drafted in **30 minutes** (vs. 2–3 days of SA time).*

**Three-column body — what the agent does for the SA:**

| **Working today (in production)** | **Shipping this quarter** | **1–2 quarter horizon** |
|---|---|---|
| **Knows the entire Rossum platform** out of the box | **Closes the inner build-test loop** — change, re-run, verify, repeat (shipped this week) | **Designs the implementation** from SOW + sample documents — reviewable blueprint before code |
| **Audits & documents** existing customer setups end-to-end | **Reads customer documents ~20–50× cheaper** — long iteration chains become affordable | **Builds autonomously** with goal-driven iteration across many documents |
| **Drives the standard implementation playbook** scoping → deployment | **Proven live this week:** changed a business rule + a data field, re-extracted a real document, verified the right value landed | **Specialist matching skill** — designs and tunes the vendor / PO / GL lookups that drive automation rate |
| **Modernizes legacy configurations** — every change request runs full regression before it ships | | **Spins up new customer environments** end-to-end (today: 2-day manual setup) |
| **Operates the live customer system** — 58 native tools read and modify the configuration directly | | **Co-runs UAT, promotes to production safely** |
| | | **Production self-healing** — monitors live, detects drift, proposes fixes (with approval) |
| | | **Drafts customer communication** — status updates, exception reports, UAT findings; SA reviews and sends |
| | | **ERP-specific connectors** (SAP, Coupa, NetSuite, Oracle, Workday) — every customer makes the next one faster |

**Footer line (the take-away):**
> **Why we win:** Knowledge of the platform + live system access + verification loop + **limitless attention span**, on Anthropic's frontier AI. Already running in Solution Architect terminals — not slideware.

---

## Section 2 — Speaker notes (~60–90 seconds at the podium)

*(Written for an audience that doesn't know the Rossum implementation process. Lead with the business problem, translate jargon, save acronyms for the appendix.)*

> **The problem.** Every Rossum customer needs a Solution Architect to configure the platform for their specific documents, ERPs, master data, and approval processes. Today that takes **six weeks per customer**. Solution Architects are the single biggest bottleneck on how fast we onboard customers — which is the single biggest constraint on growth.
>
> **The vision.** Today the SA *types every detail* by hand. Tomorrow the SA *steers* — an AI agent does the implementation. Same SA headcount, multiples more customers in flight in parallel, and **higher safety on every change** because every change request runs full regression before it ships.
>
> **Proof, this week, not theoretical.** The agent took an SAP BPT integration brief and produced a complete first-pass integration draft in **30 minutes**. Against a baseline of 2–3 days of Solution Architect work. Reviewable, modifiable, deployable. The SA still owns the final cut — but the starting point is now an hour, not a week.
>
> **The vehicle.** Claude Code with the Rossum SA plugin. Built on Anthropic's latest stack — Claude Code (their developer agent), Model Context Protocol (the new open standard for connecting AI to live customer systems), Skills (composable agent capabilities), and Claude Opus 4.7 with a one-million-token context window. Frontier AI tooling, with our plugin already running on top of it in production.
>
> **Left column — what works today.** The agent already knows the Rossum platform end-to-end. It audits existing customer implementations and tells you what's wrong. It auto-generates customer documentation. It runs the standard 7-phase implementation playbook from scoping to deployment. It modernizes legacy customer setups — and every change request goes through full regression before it ships, so confidence is high and rollback is rare. The agent doesn't *suggest* — through 58 native tools, it *reads and modifies the live customer configuration directly*, just like the SA would.
>
> **Middle column — what shipped this week.** The build-test inner loop is now closed. The SA points the agent at one real document, states the goal in plain English, and the agent changes the configuration, re-runs the extraction, reads the result, compares against the goal, and iterates until it matches. We proved this end-to-end live this week: changed a vendor-matching rule, modified a data field, re-extracted a real customer invoice, verified the right value was stored. Reading each result is 20–50× cheaper than before, so long iteration chains are now economically viable.
>
> **Right column — the next 1–2 quarters.** The leap to autonomous delivery. Given a SOW + master-data sample + sample documents, the agent designs the implementation as a reviewable blueprint, builds and tests across many documents, spins up the customer environment (today's manual 2-day setup), co-runs user-acceptance testing with the customer, promotes safely to production, monitors live, and **proactively self-heals** when it detects drift (subject to SA approval). It also **drafts customer-facing communication** — status updates, exception reports, UAT findings — so SAs spend their time on customer judgment, not routine writing.
>
> **Tomorrow versus today.** Today's SA gets a SOW, spends 2 days standing up the environment, a week designing the data structure, another week tuning the matching, two weeks driving UAT — six weeks per customer. Tomorrow's SA hands the SOW to the agent. Within an hour: implementation blueprint on the screen for review. SA approves architecture. Agent builds and tests overnight against sample documents. SA arrives Monday to a working draft, refines with the customer through UAT, signs off. **Two weeks per customer instead of six.**
>
> **Why we win.** LLMs can write code. Our stack writes code, **operates live customer systems**, **iterates against real documents**, and **verifies the result** — with **limitless attention span** the agent doesn't tire when checking the 500th invoice for an inconsistent vendor code. Knowledge + live access + verification + endurance, on Anthropic's frontier AI. Eleven skills, 58 live tools, a verification loop, all running in SA terminals today. Not slideware.

---

## Section 3 — Appendix (full vision)

### The shift

Today the Solution Architect *configures* Rossum implementations by hand.
Tomorrow the Solution Architect *supervises* an agent that designs, builds, tests, deploys, and shepherds customer integrations through UAT.

Claude Code with the Rossum SA plugin is how we get there. The plugin gives Claude:

- **Knowledge** — 10 autoloaded reference packs covering the Rossum stack end-to-end (platform, API, schemas, hooks, formulas, MDH, MongoDB, Data Storage, SAP, Coupa, SFI, TxScript)
- **Hands** — 58 MCP tools for live read/write access to the Rossum core platform, Data Storage service, and MDH datasets
- **Workflows** — 11 invocable skills that codify SA playbooks
- **Modern foundation** — built on Anthropic's latest primitives: Claude Code, Model Context Protocol (the emerging open standard for AI-to-system integration), Skills (composable agent capabilities), and Claude Opus 4.7 with a 1M-token context window

The result: an AI that doesn't just write Rossum config — it **operates** Rossum.

The bottleneck on customer onboarding today is SA capacity. This plugin attacks that bottleneck directly.

### Bucket 1 — Working today (production)

| Bucket | Skills | What it does |
|---|---|---|
| **Discovery & audit** | `analyze`, `dead-code`, `document`, `evaluate-namings` | Audit an existing implementation for config errors, unused hooks/formulas/rules, naming inconsistency. Generate queue-focused reference docs from a live org. |
| **Scoping** | `write-sow` | Generate a Statement of Work from project requirements. |
| **Migration** | `upgrade`, `refine-deployment`, `test-behavioral-equivalence` | Modernize deprecated extensions into formulas. Auto-fill `prd2` deploy files. Run full snapshot-replay-diff regression to verify upgrades preserve behavior across environments. |
| **Delivery** | `implement` | 7-phase guided implementation from scoping through production deployment. |
| **Live API surface** | 58 MCP tools | list/get/patch/create across queues, schemas, hooks, annotations, MDH datasets, hook logs, audit logs, users, organizations. |

### Bucket 2 — Under development (this quarter)

- **`iterate` skill — shipped this week.** After any code change, the SA points Claude at an annotation. Claude fires one MCP call that wraps start → validate → cancel safely, reads the result against a stated goal, and loops until met. Compact merged annotation view delivers ~20–50× token reduction on reads; raw payload cached to disk for deep-dive when needed.
- **Live end-to-end change-test-verify**, proven this week: Claude patched an MDH hook config + a schema, triggered fresh extraction of the document, pulled the result, and verified the expected value was stored — all from one Claude Code session. This is the foundational building block for the next bucket.

### Bucket 3 — 1–2 quarter roadmap (the leap to delivery engine)

The unlock: Claude receives **dataset + SOW + sample documents** and delivers a working integration end-to-end.

**Design & planning**
- `design-architecture` — produce an implementation blueprint (queue topology, schema design, MDH structure, hook chain, rules, export pipeline) reviewable *before* any code is written.
- `plan-development` — turn the blueprint into a phased work plan with milestones, dependencies, risk callouts.

**Advanced iteration**
- `iterate-advanced` — multi-deliverable goal-driven loops. Picks the right document from a corpus, identifies the right field, performs the change, iterates to pass, moves on.
- `debug` — hypothesis-driven root-cause walks from observed-vs-expected through hook logs, `validation_sources`, MDH dataset state, rule definitions, schema constraints.

**Specialist matching skill**
- `design-matching` — designs and tunes MDH matching configurations (the highest-value, most-complex part of most implementations). Picks the right fuzzy/exact strategy, sets thresholds against a sample dataset, iterates against real documents.

**Org-level provisioning**
- `provision-customer-org` — given SOW + target environment, Claude creates the org / workspaces / queues / users / hooks / schemas / datasets and runs initial smoke tests. (Currently a manual ~2-day setup.)
- `setup-integration-test-harness` — automated test corpora wired to UAT, continuous regression on every `prd2 deploy`.

**UAT & promotion**
- `uat-coordinator` — manage UAT cycles with the customer: collect test results, triage defects, prioritize fixes, drive iteration to sign-off.
- `promote-to-prod` — gated promotion (UAT → preprod → prod) with diff review, automation-rate baselines, and rollback playbook.

**Production self-healing**
- `monitor` — watches confirmation rates, exception types, hook failure rates.
- `self-heal` — when drift or a fixable problem is detected, proposes (or auto-applies, subject to approval) the fix. Bulk-of-implementation problems show up post-go-live; the agent catches them before customers do.

**Customer communication deload**
- `draft-customer-comms` — drafts status updates, exception reports, UAT findings, change summaries. SA reviews, edits, sends. Reclaims the routine-writing time SAs lose today.

**ERP-specific connectors** (continuously growing library)
- SAP (IDOC, BPT, S/4HANA), Coupa, NetSuite, Workday, Oracle, sector-specific patterns — codified once, reused across customers. Each engagement makes the next one faster.

### The competitive moat

LLMs can write code. **This plugin** writes code, **operates live customer systems**, **iterates against real documents**, and **verifies the result** — with **limitless attention span** that doesn't degrade on the 500th consistency check the way human attention does. That combination — knowledge + live access + verification + endurance — is what turns "AI writes config" into "AI delivers implementations."

It's not slideware: 11 skills + 58 MCP tools + reference packs are already running in SA terminals daily.

### Proof point — SAP BPT in 30 minutes

This week, the agent took an SAP BPT integration brief and produced a complete first-pass integration draft in **30 minutes**, against a baseline of 2–3 days of Solution Architect time. Reviewable, modifiable, deployable. The Architect still owns the final cut and the customer relationship; the starting point is now an hour, not a week. This is the velocity multiplier that scales across customers.

### The role of the Solution Architect

| | Today | Tomorrow |
|---|---|---|
| **Types config** | Yes | Agent does this |
| **Runs regressions** | Yes | Agent does this |
| **Drives UAT** | Manually | Supervises agent-driven UAT |
| **Architecture decisions** | Yes | Yes — *more time for this* |
| **Customer relationship** | Yes | Yes — *more time for this* |
| **Supervises milestones** | N/A | Primary activity |

The SA's *judgment* becomes more valuable, not less. The *typing* gets automated.
