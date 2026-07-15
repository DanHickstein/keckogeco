# Configuration

The real configuration file is **`keckogeco.toml`** in this directory (or
`~/.keckogeco/keckogeco.toml`, or wherever `--config` / `$KECKOGECO_CONFIG`
points). It is **git-ignored** because it contains site-specific instrument
addresses and, potentially, references to credentials.

To set up a new machine:

```bash
cp config/instruments.example.toml config/keckogeco.toml
python -m keckogeco.discovery        # auto-discover instruments and update the [devices.*] blocks
python -m keckogeco.check       # validate the file and try connecting to each device
```

How discovery treats the file:

- It rewrites only the `[devices.*]` blocks; all other sections, comments,
  and any human-added keys inside a block (`mode`, `channel`, `note`, ...)
  are preserved. The pre-run file is kept as `keckogeco.toml.bak`.
- A device that does not answer is **never dropped** — its block is tagged
  `missing_since` and flagged in the report. Run with `--prune` to remove
  silent blocks explicitly.
- `enabled = false` means "known device, do not talk to it": the block is
  always kept, the port is claimed, and discovery only checks that the USB
  adapter is present (used for the powered-off TC-720s, the not-yet-ported
  Eaton PDUs, and the suspected Rio ORION port). Add a `note = "..."` to
  record why.

Rules:

- **Never commit `keckogeco.toml` or `secrets.toml`.** The example file is the
  only committed config, and it contains placeholder values only.
- Credentials (e.g., Eaton PDU logins) go in the OS keyring
  (Windows Credential Manager) via `keyring`; the config file then holds only
  a reference. `secrets.toml` (also git-ignored) is the fallback.
- **`site-info.txt`** (git-ignored, laptop-only) is human-readable handoff
  documentation: PDU IPs and credentials, network/firewall notes, account
  pointers. Nothing in the code reads it. Interim home until Keck decides on
  long-term credential storage.
