# Installation and verification

## 1 — Clone

```sh
gh repo clone jacobmentalconstruct/.project-mapper .project-mapper
```

Or:

```sh
git clone https://github.com/jacobmentalconstruct/.project-mapper .project-mapper
```

## 2 — Set up the environment

**Windows** (run from your project root):
```bat
.project-mapper\setup_env.bat
```

**macOS / Linux:**
```sh
chmod +x .project-mapper/setup_env.sh && .project-mapper/setup_env.sh
```

This creates `.project-mapper/.venv/` and installs all dependencies. Takes 30–60 seconds on a fresh machine.

## 3 — Launch

**Windows:** `.project-mapper\run.bat`  
**macOS / Linux:** `.project-mapper/run.sh`

The app auto-detects that it is installed as a sidecar and opens your project.

## 4 — Verify the sidecar

After launching:

1. The project tree should show your project files (not the `.project-mapper/` sidecar itself).
2. Run **Scan** — the snapshot writes to `_projectmapper/` in your project root.
3. Run **Export → Tree** — Markdown lands in `_projectmapper/`.
4. Open **Tools → SchemaTOUCHER**, create a spine in `.project-mapper/spines/`, register a schema, append an entry, browse it.
5. From a terminal: `.project-mapper/run.sh <path-to-project>` — explicit target works from any directory.
6. Check that `.project-mapper/` does **not** appear in the project tree scan.

## Updating

```sh
cd .project-mapper && git pull
```

Your spines, snapshots, exports, and backups are gitignored and unaffected.

## Uninstall

Delete `.project-mapper/`. Generated files in `_projectmapper/` (project root) are separate and can be kept or deleted independently.

## Verified platforms

| Platform | Status |
|---|---|
| Windows 10/11, Python 3.13 | ✓ Verified |
| macOS, Python 3.13 | Covered by CI matrix (not hands-on verified in this release) |
| Ubuntu 22.04, Python 3.10/3.14 | Covered by CI matrix (not hands-on verified in this release) |

For platform-specific issues, open an issue in the source repository.
