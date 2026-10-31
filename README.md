# ProjectMapper

A local project-context tool for AI-assisted development.

ProjectMapper scans your source tree, compiles a SQLite snapshot, and exports your project as Markdown — giving AI agents precise, structured context without manual copy-paste.

It also includes **SchemaTOUCHER**: a GUI tool for creating and browsing JSONL design-journal spines, so design decisions travel with your code.

---

## Quick install (sidecar)

Clone this repository into your project as `.project-mapper/`:

```sh
gh repo clone jacobmentalconstruct/.project-mapper .project-mapper
```

Or with plain git:

```sh
git clone https://github.com/jacobmentalconstruct/.project-mapper .project-mapper
```

Then run the one-time setup from inside your project root:

**Windows:**
```bat
.project-mapper\setup_env.bat
```

**macOS / Linux:**
```sh
chmod +x .project-mapper/setup_env.sh
.project-mapper/setup_env.sh
```

Then launch:

**Windows:** `.project-mapper\run.bat`  
**macOS / Linux:** `.project-mapper/run.sh`

The app opens and automatically targets the project it is installed in.

---

## What it does

| Feature | Description |
|---|---|
| Tree scan | Walks your project, respects `.gitignore`, applies exclusion rules |
| SQLite snapshot | Compiles tree + file contents into a queryable database |
| Markdown export | Tree, filedump, or combined — paste directly into an AI chat |
| SchemaTOUCHER | Schema-driven JSONL journal: register schemas, append entries, browse |
| MCP / CLI adapter | Agents can drive ProjectMapper over JSON-RPC or subprocess |

---

## Directory layout

```
.project-mapper/       ← this repository, cloned here
├── src/               ← application source (update with git pull)
├── assets/            ← icons and UI resources
├── run.bat / run.sh   ← launch scripts
├── setup_env.*        ← one-time setup scripts
├── examples/          ← sample spine demonstrating the JSONL format
├── project-lineage.jsonl  ← design journal for this tool's own history
│
├── .venv/             ← created by setup_env (gitignored)
├── spines/            ← your spine files live here (gitignored)
├── snapshots/         ← compiled SQLite snapshots (gitignored)
├── exports/           ← Markdown exports (gitignored)
└── backups/           ← change backups (gitignored)
```

Generated directories (`spines/`, `snapshots/`, `exports/`, `backups/`) are gitignored so `git pull` never disturbs your data.

---

## Updating

Pull the latest app files from inside `.project-mapper/`:

```sh
cd .project-mapper && git pull
```

Your spine data and snapshots are unaffected.

---

## Spines

A spine is a `.jsonl` file where every line is either a **schema definition** or a **data entry**. Use them as design journals, decision logs, or structured notes that live beside your code.

See `examples/sample-spine.jsonl` for a minimal working example.  
See `project-lineage.jsonl` for an evidence-grounded record of this tool's own design history.

Open SchemaTOUCHER from the Tools menu inside ProjectMapper.

---

## CLI and MCP

Run actions from the command line:

```sh
.project-mapper/run.sh <project-folder> --action scan
```

Start the MCP server for agent integration:

```sh
.project-mapper/run.sh --mcp
```

See the full action inventory in the app's Help menu or in `src/projectmapper/application/contracts.py`.

---

## Requirements

- Python 3.10, 3.13, or 3.14
- `py` launcher (Windows) or `python3` (macOS/Linux) on PATH
- Tkinter (included with standard Python on Windows and macOS; on Linux: `sudo apt install python3-tk`)

---

## License

MIT — see [LICENSE.md](LICENSE.md).
