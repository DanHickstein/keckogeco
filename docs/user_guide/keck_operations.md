# Operating the comb from the Keck side

This page covers running the comb through the KTL keyword service
`comb` — the workflow for operators on the observatory's Linux hosts
(`irastrocomb`). It replaces the ICE-server procedure from the original
manual; the keyword commands are unchanged, because the rewrite
deliberately preserved the keyword surface (including the `icesta` /
`iceclk` names).

```{note}
The HTTP dispatcher backend (the piece that connects the Keck-side
`comb` service to the `keckogeco` server) is being deployed as part of
Phase 3. Until it is live at the summit, the legacy ICE-based
procedure applies — it differs only in how the laptop-side server is
started.
```

## Architecture in one paragraph

The **keckogeco server** runs on the LFC laptop and owns all the
instruments. The **dispatcher** runs on `irastrocomb` and translates KTL
keyword reads/writes into HTTP calls to that server. The Menlo
flattener is a third, independent element (see {doc}`menlo_flattener`).
`icesta` reflects the dispatcher's connection to the laptop server, and
`iceclk` is the server's heartbeat.

## Starting the pieces

**On the LFC laptop** — the control server should normally be running
around the clock:

```powershell
python -m keckogeco.server.app
```

**On `irastrocomb`** — start the dispatcher:

```console
$ ssh combbld@irastrocomb
$ astrocomb start comb
```

**Connect the dispatcher to the server:**

```console
$ modify -s comb icesta = 3
# wait ~20 seconds
$ show -s comb icesta
```

`icesta = 3` requests a (re)connect; the `show` should then report the
connected state. If this step is skipped and you send commands anyway,
you will see:

```text
ERR_WRITE_SW_ERROR (-5401): There was an error in the device-specific
write routine for this keyword: check the log files.
```

which almost always means the dispatcher is not connected to the laptop
server, or the server is not running.

## Bringing the comb up and down

Turn on the full comb:

```console
$ modify -s comb lfc_set_full_comb = 1
```

Wait about a minute for the power to ramp. On the Menlo OSA display you
should see the full comb spectrum appear; you can then flatten or
filter it ({doc}`menlo_flattener`).

Return the comb to standby when finished:

```console
$ modify -s comb lfc_set_standby = 1
```

The full comb decays to zero on the Menlo spectrum.

```{admonition} Transition writes return immediately
:class: important

In the rewrite, `lfc_set_full_comb`, `lfc_set_standby`, and
`lfc_set_off` **enqueue** the transition sequence and return right
away (the old ICE call blocked until the sequence finished). While a
transition runs, writes to other keywords are rejected until it
completes; reads work throughout. Progress is visible in the
engineering GUI and on the server's `/api/v1/actions/current`
endpoint.
```

## Checking comb status

The comb state can be read directly:

```console
$ show -s comb lfc_check_fullcomb    # 1 = full comb, 0 = not
$ show -s comb lfc_check_status      # legacy prime-product code
```

`lfc_check_status` reports the legacy encoded status (30030 = FULL
COMB, 15015 = STANDBY, 1 = OFF).

```{note}
Under the old system, `lfc_check_fullcomb` had to be *written* with the
value 2 and then read back a few seconds later. In the rewrite it is a
plain read — the write step is no longer needed.
```

## HK shutter

```console
$ modify -s comb lfc_hk_shutter = 1   # pass light
$ modify -s comb lfc_hk_shutter = 0   # shut light
# wait ~5 seconds
$ show -s comb lfc_hk_shutter
```

## Shutting down the session

Once the comb is in standby:

```console
$ modify -s comb icesta = 2      # disconnect the dispatcher
```

The keckogeco server on the laptop keeps running — unlike the old ICE
server, it is designed to stay up permanently, so there is nothing to
stop on the laptop side at the end of a session.

## Troubleshooting

### `ERR_WRITE_SW_ERROR (-5401)` on any write

The dispatcher is not connected to the laptop server, or the server is
down:

1. Verify the server is running on the LFC laptop (and reachable —
   check `show -s comb iceclk` updates once connected).
2. Reconnect: `modify -s comb icesta = 3`, wait ~20 s, then
   `show -s comb icesta`.
3. Reissue the command.

### Writes rejected while a transition is running

Keyword writes during a running transition are refused by design (the
server answers HTTP 409). Wait for the sequence to finish — a full-comb
ramp takes on the order of a minute — and retry.

### Instrument (VISA) errors reported by the server

If a specific instrument stops responding, the server logs the failure
and marks the device offline in its health report; it reconnects
automatically once. Persistent failures usually mean the instrument is
off, its USB/serial cable moved, or another program has grabbed the
port. On the laptop:

```powershell
python -m keckogeco.check --device <key>
```

connects to just that device and prints its status. A full server
restart is rarely needed — but if it is, restart it and reconnect the
dispatcher (`icesta = 3`).

### Accidentally closed a terminal

Nothing is lost. Reopen it and repeat the corresponding procedure —
SSH back into `irastrocomb` for the dispatcher, or restart the server
on the laptop if you closed that (it keeps no session state).
