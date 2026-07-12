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

Rules:

- **Never commit `keckogeco.toml` or `secrets.toml`.** The example file is the
  only committed config, and it contains placeholder values only.
- Credentials (e.g., Eaton PDU logins) go in the OS keyring
  (Windows Credential Manager) via `keyring`; the config file then holds only
  a reference. `secrets.toml` (also git-ignored) is the fallback.
