---
name: export-pipeline-reference
description: Request Processor (export pipeline) configuration reference for Rossum. Covers the multi-stage JSON-based engine for integrating Rossum with external APIs — stages, variable templating, evaluate conditions, get_content sources, call_api with auth/iteration/file uploads, response handlers (JMESPath/XPath/regex), SFTP export via file-storage-export, migration from Pipeline v1, and hook-level prerequisites and common gotchas (external egress, schema field bindings, sideloading, token ownership). Use when building, debugging, or explaining export pipeline configurations.
user-invocable: false
---

# Export Pipeline — Request Processor Reference

This skill provides the complete configuration reference for the **Request Processor**, Rossum's multi-stage export pipeline engine. It replaces the legacy multi-hook pipeline with a single serverless function that executes complex API workflows from pure JSON settings — no code required.

For the full configuration guide, field reference, patterns, and examples, see [reference.md](reference.md).

**IMPORTANT — hook setup:** The Request Processor runs as a single serverless function hook. Create it via the Rossum API (or prd2), then configure the `settings` JSON. The function code lives in `elis-serverless-functions/generic-functions/experimental/attachment_processor/`.

Use this knowledge when:
- Building a new export pipeline that sends data to external APIs (Coupa, SAP, NetSuite, custom REST)
- Configuring SFTP/S3 file export via the `file-storage-export` service
- Writing the `settings` JSON for a Request Processor hook (stages, auth, requests, response handlers)
- Using variable templating (`{field.*}`, `{payload.*}`, `{property.*}`, `{token}`, `{sequence}`)
- Setting up OAuth authentication with token caching
- Configuring conditional stage execution (evaluate phase with MongoDB-style filters)
- Fetching document relations or explicit data in `get_content` phase
- Extracting values from API responses using JMESPath, XPath, or regex
- Iterating over line items or lists of related documents
- Uploading files (PDF scans, email EML, attachments) via multipart or files content types
- Migrating from the legacy Pipeline v1 (multi-hook chain) to the Request Processor (single hook)
- Debugging export pipeline issues (URL not fetching, token caching, handler conditions)
- Understanding the difference between `document_relation` and `document_relation_content` sources
- Diagnosing prerequisite/setup failures: TCP timeouts to external hosts, missing `rossum_authorization_token`, `Schema sideloading must be enabled` errors, generic "Some exception occurred" export failures

Note: The `rossum-reference` skill covers the legacy 6-step export pipeline chain (Custom Format Templating, REST API Export, Data Value Extractor, Export Evaluator, SFTP Export). This skill covers the **Request Processor**, which is the modern replacement that consolidates all steps into a single configurable hook.
