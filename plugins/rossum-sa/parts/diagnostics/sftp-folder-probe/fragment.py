"""SFTP folder-structure probe — Rossum custom function hook body.

A throwaway diagnostic, pointed at whichever SFTP account is named in settings. It exists because
customer SFTP hosts are routinely IP-allowlisted: a laptop cannot connect (the TCP session dies
during banner exchange) but Rossum's own Lambda egress IS allowlisted. So seeing the tree, proving
an export landed, reading a file back, staging a test file, or quarantining a poison file all have
to happen from inside Rossum.

Answers, per run:
  1. Where does the new entity's master data land, so the import extension can be pointed at it?
  2. Do the export target folders the client named already exist?
  3. Is that data being dropped into folders an ACTIVE incumbent importer already watches?

Question 3 is the reason this reports file extension CASE. An incumbent importer matching
`.*\\.CSV` (upper) ignores `.csv` (lower). That single letter is all that separates "inert files
sitting in a folder" from "the new entity's master data silently replacing the incumbent's". A
probe that does not report casing cannot see the difference.

Design notes:
  - Reports the Lambda's public egress IP first. If the connection fails, that IP is what the
    customer needs to allowlist, and it is the single most useful thing to know before debugging
    anything else.
  - Reports the login's landing directory. A chrooted account sees a different root, so absolute
    paths from one account's map do not transfer to another's.
  - Walks breadth-first with a hard node budget. An SFTP tree can be enormous; an unbounded walk
    times out the hook and returns nothing at all, which is worse than a partial map.
  - Scans EVERY filename for the entity marker, not just the newest few. Sampling answers "what
    arrived recently"; only a full scan answers "is it here at all".

Four capabilities, each load-bearing:
  - `probe_paths`  — direct listdir of named paths, skipping the walk (`skip_walk: true`).
  - `cat_paths`    — read a file back. Listing proves an export RAN; only content proves its FORMAT.
  - `write_files`  — stage a known-good file, to prove the import pipeline end to end.
  - `move_files`   — quarantine a file an importer keeps retrying. The SFTP import method schema
                     has no failure action, so a poison file is retried on every cron tick forever;
                     moving it out of the watched folder is the only remedy.

The walk and the probes are read-only; `write_files` and `move_files` act only when those settings
are present, so a run that omits them touches nothing.

Credentials come from settings; the password comes from the hook SECRET so it never appears in
config, git, or a transcript.
"""
import datetime
import socket
import stat
import urllib.request

import paramiko

# Paths the client described, in prose, for the import/export targets. Each entry is a list of
# candidate spellings, tried in order until one lists — SFTP roots are case- and separator-
# sensitive, and a path given in prose ("the Out folder under Rossum Prod") pins neither. Guessing
# a single spelling is how a folder that exists gets reported as MISSING.
NAMED_TARGETS = {
    # "<entity> export (Material)": [
    #     "/Rossum-Prod/Out/Material",
    #     "/Rossum Prod/Out/Material",
    #     "/rossum-prod/out/material",
    #     "/Out/Material",
    #     "/out/material",
    # ],
}

# Case-insensitive substrings that mark a file as belonging to the entity under investigation
# rather than to the incumbent. A wrong marker makes the entity scan report "none anywhere",
# which reads exactly like "the data is not here". Kept as a list so a second entity can be added
# without touching the walk.
ENTITY_MARKERS = ["«entity_marker»"]

# Directories that hold processed history rather than pending drops. One of them held 41,192
# files, and listing it is what pushed a prod account's walk past the hook timeout. Their contents
# cannot answer "is the entity's data waiting to be imported", so they are named and skipped
# rather than walked.
SKIP_DIR_NAMES = {"archive", "archive-img1", "archive-img2", "failed", "failed_imports",
                  "success", "errors"}

# Defaults, overridable per run from settings (max_nodes / max_depth / sample_files). Different
# SFTP accounts land in different chroots with wildly different tree sizes: the account that walks
# in 20s and the one that blows the hook timeout need different budgets, and re-editing the hook
# body between runs to find a workable budget is slower than reading it from settings.
MAX_NODES = 400          # hard budget on directories visited
MAX_DEPTH = 5
SAMPLE_FILES = 8         # newest filenames to print per directory


def public_ip():
    try:
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"<unavailable: {e}>"


def ts(epoch):
    if not epoch:
        return "?"
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def describe(entry):
    return f"{entry.filename}  ({entry.st_size}b, {ts(entry.st_mtime)})"


def walk(sftp, root, log, max_nodes=MAX_NODES, max_depth=MAX_DEPTH, sample_files=SAMPLE_FILES):
    """Breadth-first, budgeted.

    Keeps the FULL filename list per directory, not just the printed sample — the entity scan and
    the extension-case tally both need every name, and re-listing the tree to get them would double
    the runtime against a slow SFTP.
    """
    seen, queue, visited = {}, [(root, 0)], 0
    while queue and visited < max_nodes:
        path, depth = queue.pop(0)
        try:
            entries = sftp.listdir_attr(path)
        except Exception as e:
            seen[path] = {"error": str(e)}
            continue
        visited += 1
        dirs = [e.filename for e in entries if stat.S_ISDIR(e.st_mode)]
        files = [e for e in entries if not stat.S_ISDIR(e.st_mode)]
        newest = sorted(files, key=lambda e: e.st_mtime or 0, reverse=True)[:sample_files]
        seen[path] = {
            "dirs": len(dirs),
            "files": len(files),
            "sample": [describe(e) for e in newest],
            "all": [(e.filename, e.st_size, e.st_mtime) for e in files],
        }
        skipped = sorted(d for d in dirs if d.lower() in SKIP_DIR_NAMES)
        if skipped:
            seen[path]["skipped_dirs"] = skipped
        if depth < max_depth:
            for d in dirs:
                if d.lower() in SKIP_DIR_NAMES:
                    continue
                queue.append((f"{path.rstrip('/')}/{d}", depth + 1))
    if queue:
        log.append(f"NOTE walk truncated at {max_nodes} directories; {len(queue)} still queued")
    return seen


def rossum_hook_request_handler(payload: dict) -> dict:
    log = []
    s = payload.get("settings") or {}
    secrets = payload.get("secrets") or {}

    # No default host: a probe that silently points at the last customer's hostname is worse than
    # one that refuses to run.
    host = s.get("host")
    port = int(s.get("port", 22))
    user = s.get("username", "rossum")
    password = secrets.get("password")
    roots = s.get("roots") or ["/"]
    max_nodes = int(s.get("max_nodes", MAX_NODES))
    max_depth = int(s.get("max_depth", MAX_DEPTH))
    sample_files = int(s.get("sample_files", SAMPLE_FILES))
    # Runs that only need to answer "does this path exist and what is in it" can skip the walk
    # entirely — that is the difference between a run that returns and one that hits the timeout.
    extra_paths = s.get("probe_paths") or []
    # Files to read back, not just list. Listing proves an export ran; only the content proves it
    # produced the right FORMAT — which is the whole question when one annotation emits two files.
    cat_paths = s.get("cat_paths") or []
    skip_walk = bool(s.get("skip_walk"))

    log.append(f"egress IP (allowlist this if the connect fails): {public_ip()}")
    if not host:
        return {"status": "error",
                "error": "no host in settings — set settings.host to the SFTP hostname",
                "log": log}
    if not password:
        return {"status": "error",
                "error": "no password in hook secrets — set it on the hook, then re-invoke",
                "log": log}

    try:
        ip = socket.gethostbyname(host)
        log.append(f"resolved {host} -> {ip}")
    except Exception as e:
        return {"status": "error", "error": f"DNS failed for {host}: {e}", "log": log}

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=ip, port=port, username=user, password=password,
                       banner_timeout=60, auth_timeout=30, timeout=30)
    except Exception as e:
        return {"status": "error", "error": f"connect/auth failed as {user}@{host}:{port}: {e}",
                "log": log}

    try:
        sftp = client.open_sftp()
        log.append(f"connected as {user}@{host}:{port}")
        try:
            # A chrooted account lands somewhere other than "/". Without this, absolute paths read
            # off one account's map are meaningless against another's.
            log.append(f"login lands in: {sftp.normalize('.')}")
        except Exception as e:
            log.append(f"could not resolve landing directory: {e}")

        log.append(f"budget: max_nodes={max_nodes} max_depth={max_depth} "
                   f"sample_files={sample_files} skip_walk={skip_walk}")

        tree = {}
        if not skip_walk:
            for root in roots:
                tree.update(walk(sftp, root, log, max_nodes, max_depth, sample_files))

        # Explicitly probe the paths the client named, plus any named for this run. A direct
        # listdir of a known path costs one round trip; reaching the same path through the walk
        # costs every directory above it.
        probes = dict(NAMED_TARGETS)
        for p in extra_paths:
            probes[f"probe: {p}"] = [p]

        named = {}
        for label, candidates in probes.items():
            hit = None
            for cand in candidates:
                try:
                    entries = sftp.listdir_attr(cand)
                    files = [e for e in entries if not stat.S_ISDIR(e.st_mode)]
                    ent = [e for e in files
                           if any(m in e.filename.lower() for m in ENTITY_MARKERS)]
                    hit = {"path": cand, "exists": True,
                           "files": len(files),
                           "dirs": sum(1 for e in entries if stat.S_ISDIR(e.st_mode)),
                           "entity_files": [describe(e) for e in
                                            sorted(ent, key=lambda e: e.st_mtime or 0,
                                                   reverse=True)],
                           # Casing decides whether an active `.*\.CSV` importer claims the file.
                           "entity_upper_csv": sum(1 for e in ent if e.filename.endswith(".CSV")),
                           "sample": [describe(e) for e in
                                      sorted(files, key=lambda e: e.st_mtime or 0,
                                             reverse=True)[:5]]}
                    break
                except Exception:
                    continue
            named[label] = hit or {"exists": False, "tried": candidates}

        catted = {}
        for cp in cat_paths:
            try:
                with sftp.open(cp, "r") as fh:
                    catted[cp] = fh.read(4000).decode("utf-8", "replace")
            except Exception as e:
                catted[cp] = f"<unreadable: {e}>"

        # --- write side -------------------------------------------------------------------
        # A laptop cannot reach the SFTP (the host is IP-allowlisted and a local connect dies
        # during banner exchange), but the hook runs from Rossum's own egress, which is
        # allowlisted. So staging a test file, or quarantining a bad one, has to happen here.
        #
        # `move_files`  [{"from": ..., "to": ...}]   — quarantine a file an importer keeps
        #                                              retrying, without waiting for a failure
        #                                              action the import method schema does not have.
        # `write_files` [{"path": ..., "content": ...}] — stage a known-good file so the import
        #                                              pipeline can be proven end to end.
        moved, written = {}, {}
        for mv in (s.get("move_files") or []):
            src, dst = mv.get("from"), mv.get("to")
            try:
                sftp.rename(src, dst)
                moved[src] = f"-> {dst}"
            except Exception as e:
                moved[src] = f"<failed: {e}>"

        for wf in (s.get("write_files") or []):
            path, content = wf.get("path"), wf.get("content") or ""
            try:
                data = content.encode(wf.get("encoding") or "utf-8")
                with sftp.open(path, "w") as fh:
                    fh.write(data)
                written[path] = f"{len(data)} bytes"
            except Exception as e:
                written[path] = f"<failed: {e}>"

        sftp.close()
        client.close()

        # Print everything. The return value is only visible to whoever called /invoke directly;
        # anyone running this from the UI sees the hook LOG, so the report has to go to stdout or
        # the run looks like it did nothing.
        print("=" * 78)
        for line in log:
            print(line)

        print("\n--- NAMED TARGETS (from the client's spec) ---")
        for label, info in named.items():
            if info.get("exists"):
                print(f"  EXISTS   {label}")
                print(f"           {info['path']}  ({info['files']} files, {info['dirs']} dirs)")
                for f in info.get("sample", []):
                    print(f"             - {f}")
                ent = info.get("entity_files") or []
                if ent:
                    up = info.get("entity_upper_csv", 0)
                    warn = ("  <<< MATCHES .*\\.CSV — AN ACTIVE IMPORTER WILL INGEST IT"
                            if up else "  (lowercase .csv — below the .*\\.CSV importer regex)")
                    print(f"           ENTITY FILES HERE: {len(ent)}, {up} uppercase .CSV{warn}")
                    for f in ent:
                        print(f"             * {f}")
            else:
                print(f"  MISSING  {label}")
                for cand in info.get("tried", []):
                    print(f"           tried: {cand}")

        if catted:
            print("\n--- FILE CONTENTS ---")
            for cp, body in catted.items():
                lines = body.splitlines()
                # A CSV row must be ONE line. Reporting the line count and the field count per
                # line is what distinguishes a valid row from a template that emitted each field
                # on its own line - which looks fine field-by-field and is unparseable as CSV.
                print(f"  ===== {cp} =====")
                print(f"    lines={len(lines)}  "
                      f"fields_per_line={[l.count('\",\"') + 1 for l in lines[:6] if l.strip()]}")
                for line in lines[:6]:
                    print(f"    {line[:220]}")

        print("\n--- TREE ---")
        for path in sorted(tree):
            info = tree[path]
            if "error" in info:
                print(f"  {path}   <ERROR {info['error']}>")
                continue
            print(f"  {path}   [{info['dirs']} dirs, {info['files']} files]")
            for f in info["sample"]:
                print(f"      - {f}")
            if info.get("skipped_dirs"):
                print(f"      (not walked: {', '.join(info['skipped_dirs'])})")

        # The whole point of the run: is the entity's data here, and if so, is it sitting in a
        # folder an incumbent importer already watches?
        print("\n--- ENTITY FILES (full scan of every filename, not just the samples) ---")
        hits = 0
        for path in sorted(tree):
            matches = [(n, sz, mt) for (n, sz, mt) in tree[path].get("all", [])
                       if any(m in n.lower() for m in ENTITY_MARKERS)]
            if not matches:
                continue
            others = len(tree[path].get("all", [])) - len(matches)
            print(f"  {path}   ({len(matches)} entity files, {others} other files)")
            for n, sz, mt in sorted(matches, key=lambda x: x[2] or 0, reverse=True):
                print(f"      - {n}  ({sz}b, {ts(mt)})")
            hits += len(matches)
        if not hits:
            print("  none anywhere in the walked tree")

        # Extension casing decides whether an active `.*\.CSV` importer picks a file up. Report it
        # per directory so a mixed-case folder is impossible to miss.
        print("\n--- EXTENSION CASE in directories holding entity files ---")
        for path in sorted(tree):
            allf = tree[path].get("all", [])
            if not any(any(m in n.lower() for m in ENTITY_MARKERS) for (n, _, _) in allf):
                continue
            upper = sum(1 for (n, _, _) in allf if n.endswith(".CSV"))
            lower = sum(1 for (n, _, _) in allf if n.endswith(".csv"))
            ent_upper = sum(1 for (n, _, _) in allf
                            if n.endswith(".CSV") and any(m in n.lower() for m in ENTITY_MARKERS))
            flag = "  <<< ENTITY FILE MATCHES .*\\.CSV — AN ACTIVE IMPORTER WILL INGEST IT" if ent_upper else ""
            print(f"  {path}: {upper} *.CSV, {lower} *.csv, {ent_upper} entity *.CSV{flag}")
        print("=" * 78)

        # `all` is dropped from the return value: it is the largest thing here and the printed
        # report already carries every conclusion drawn from it.
        slim = {p: {k: v for k, v in i.items() if k != "all"} for p, i in tree.items()}
        if moved or written:
            print("\n--- WRITE SIDE ---")
            for k, v in moved.items():
                print(f"  moved   {k} {v}")
            for k, v in written.items():
                print(f"  wrote   {k} {v}")

        return {"status": "success", "log": log, "named_targets": named, "tree": slim,
                "entity_file_count": hits, "files": catted,
                "moved": moved, "written": written}
    except Exception as e:
        try:
            client.close()
        except Exception:
            pass
        return {"status": "error", "error": str(e), "log": log}
