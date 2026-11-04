# Reproducing the integration

The only supported base is `279f89e4d4f63f17227bf877dc3d8990f22ecd58` from `TelegramMessenger/Telegram-iOS`. `BASE.json` copies that revision's tool versions. Compatibility with future `master` is not implied.

Use Python 3.10+ and Git. Shell wrappers require Bash and support paths containing spaces. The same operations can be invoked through `python scripts/package.py` on Windows.

```sh
bash scripts/bootstrap-telegram.sh "/path/to/new/telegram-ios"
bash scripts/check-integration.sh "/path/to/new/telegram-ios"
```

Bootstrap rejects an existing destination. It checks out the exact commit and initializes dependencies only when `--submodules` is supplied. No reset, clean, or deletion of a user's checkout is performed. `check-integration.sh` requires a clean base checkout and **applies** the package there, after a complete preflight. It verifies the patch and every resulting file hash. For the separate development tree:

```sh
bash scripts/check-integration.sh "/path/to/new/verification-tree" --reference "/path/to/development-tree"
```

`apply-feature.sh` also accepts an already applied package, but only after verifying the entire expected state. A partially applied package, unexpected modification, different base, conflicting new file, traversal, or escaping symlink fails. Preflight failure does not change target files. The feature overlay is copied once; existing upstream files are changed only by `telegram-ios.patch`. `files.json` is the authoritative inventory and SHA-256 record.

## Export

Develop in a separate local branch at the pinned base. Keep integration edits unstaged and list every intended new/modified path in `export-paths.json`:

```sh
bash scripts/export-feature.sh "/path/to/development-tree" --paths Integration/export-paths.json
```

Export checks the complete modified/untracked inventory against that allowlist, includes listed untracked sources, and refuses extra changes. It produces the overlay, patch, and manifest from the real development files. Do not edit the patch manually. Use LF line endings (`core.autocrlf=false`) in integration trees. Compare the reconstructed tree with the development tree after every export.

## Module boundaries

`TelegramCore` owns the account-local Postbox store, versioned records, revision checks, real codecs and engine facade. `ChatInterfaceState` retains the local canonical state and structural selection. `AccountContext` carries the selection through the composer value. `ChatTextInputPanelNode` supplies the entry point. `TelegramUI` coordinates transfers and presents the native list. `TelegramPresentationData` supplies English/Hebrew strings. The manifest lists exact files, including native test targets and BUILD changes.

No upstream Git history, full client, signing files, or Git submodule is published in this repository. The directory name `submodules` is an overlay destination mapping.

## Demo boundary

No full-client offline fixture has been validated. `--with-demo` fails explicitly. There is no empty `demo.patch` or alternate mock chat. Normal Draft Pocket integration is independent of a demo flag. See [limitations](../docs/LIMITATIONS.md) before running the source with valuable drafts.
