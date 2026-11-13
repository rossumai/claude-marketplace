---
name: sfi-reference
description: Structured Formats Import (SFI) reference for Rossum. Covers end-to-end setup of the SFI extension for processing XML, JSON, and e-invoice documents (ZUGFeRD, X-Rechnung UBL/CII). Includes MIME type configuration, hook setup with environment URLs, field mapping with XPath/JSONPath selectors, document splitting, PDF rendering, value transformation mappings, date parsing, address concatenation patterns, and complete production-ready configuration examples for German e-invoicing. Use when setting up, configuring, or debugging Structured Formats Import. For choosing an e-invoicing route, or for the B2Brouter E-Invoicing extension, see einvoicing-reference.
user-invocable: false
---

# Structured Formats Import (SFI) Reference

> For choosing how e-invoices should enter Rossum (B2Brouter E-Invoicing extension, hosted e-invoice upload import, plain SFI), and for the B2Brouter extension itself, see `einvoicing-reference`.

This skill provides the complete setup and configuration reference for **Structured Formats Import (SFI)**, Rossum's extension for processing non-visual structured documents (XML, JSON, EDI) and rendering them as PDFs for human review.

For the full configuration guide, selector syntax, feature reference, and production-ready examples, see [reference.md](reference.md).

Use this knowledge when:
- Setting up SFI for the first time on a queue (MIME types, hook creation, environment URLs)
- Writing field mappings with XPath selectors for XML documents
- Writing field mappings with JSONPath/JMESPath selectors for JSON documents
- Configuring German e-invoicing (ZUGFeRD, X-Rechnung) — both UBL and CII formats
- Building address concatenation selectors using XPath 1.0 patterns
- Setting up value transformation mappings (e.g., invoice type codes to enum values)
- Configuring date parsing with custom formats
- Setting up document splitting for multi-document files
- Configuring PDF rendering from embedded base64 content or generated from extracted data
- Handling non-existing schema IDs gracefully with `skip_non_existing_schema_ids`
- Debugging XPath selector issues or testing selectors locally
- Understanding trigger conditions and how multiple configurations are matched
- Upper/lower case conversions in XPath 1.0 and JSON selectors
- String concatenation in XPath and JMESPath selectors
