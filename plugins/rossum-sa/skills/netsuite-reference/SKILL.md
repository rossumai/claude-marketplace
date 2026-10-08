---
name: netsuite-reference
description: Rossum NetSuite integration reference. Covers the Rossum-hosted SOAP connector (svc/netsuite-v3) — import webhooks (search / getAll payloads, basic vs advanced searches, advanced_search_internal_id_jmespath, {last_modified_date} incremental sync, async_settings), the export webhook (upsert VendorBill / VendorCredit by externalId, File Cabinet upload, attach via pipeline_context), TBA secrets and netsuite_settings — and the newer NetSuite REST master-data import (netsuite_rest_import job hook from Store template 56, one SuiteQL query per dataset, ${window_start}/${window_end} windows, checkpoint_strategy, job_run_settings) with what it does NOT do yet. Also the dataset shapes each import writes, how MDH matching reads them, SOAP-to-REST migration, and Oracle's SOAP removal timeline. Use when building, debugging, migrating, or explaining a Rossum-NetSuite integration — even if the user only says "NetSuite import", "SuiteQL", "SuiteTalk", or "NS datasets".
user-invocable: false
---

# NetSuite Integration Reference

Rossum integrates with NetSuite through two Rossum-hosted services:

- **SOAP connector** (`svc/netsuite-v3`) — webhook hooks for both directions: importing
  NetSuite records into Master Data Hub datasets, and exporting vendor bills / credits
  (with the source PDF attached) back to NetSuite. Production-proven.
- **REST import** (`netsuite_rest_import`) — a `job` hook that imports one dataset per hook
  with a SuiteQL query. Released for acceptance in October 2026; several features are still
  missing (see [reference.md → REST import: what is not there yet](reference.md#rest-import-what-is-not-there-yet)).
  There is no REST export — export stays on the SOAP connector.

Oracle removes SOAP web services with NetSuite release **2028.2**, and new TBA (and therefore
new SOAP) integrations are blocked from **2027.1**. New work should plan for the REST import,
and for OAuth 2.0 on it.

For wiring, payloads, dataset shapes, the export mapping, the REST hook, SuiteQL rules,
migration and gotchas, see [reference.md](reference.md).

Use this knowledge when:

- Wiring or reviewing a NetSuite import or export hook
  (`https://<region host>/svc/netsuite-v3/api/v1/import|export`, or a `netsuite_rest_import` job hook)
- Writing SOAP search payloads (`*SearchBasic`, `*SearchAdvanced` with joins) or SuiteQL queries
- Reading NetSuite datasets in MDH matching (`internalId`, nested `RecordRef`s, advanced-search
  `basic.<column>[0].searchValue` rows, flat lowercase REST rows)
- Building the export mapping — `RecordRef`s, item vs expense lines, attachments
- Migrating a SOAP import to REST, or planning around the SOAP removal

Related packs: `workday-reference` documents the mapping template language the SOAP export
shares (`@{…}`, `$IF_SCHEMA_ID$`, `$FOR_EACH_SCHEMA_ID$`, `$DATAPOINT_MAPPING$`);
`mdh-reference` covers the matching that reads the imported datasets.
