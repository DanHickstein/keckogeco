# Keck Integration

```{toctree}
:maxdepth: 1

keywords
```

```{note}
The DFW dispatcher deployment procedure and the keyword change list land
here in Phase 3; the change list currently lives at
`ktl/keyword-changes.md` in the repository.
```

Architecture: the Windows laptop runs `python -m keckogeco.server.app` (HTTP/REST). On the
Keck Linux host, the `comb` KTL service is provided by a DFW dispatcher whose
backend makes HTTP calls to the laptop — replacing the previous ZeroC ICE
transport while preserving the existing keyword names and the standard Keck
build/deploy workflow.
