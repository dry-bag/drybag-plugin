# Skills

Every skill here is written in **action language**: what to do, never which harness tool does
it. Claude Code, Codex, Cursor, Gemini CLI, Copilot CLI and OpenCode all read the Agent Skills
format, and each names its tools differently — a skill that says "use the Grep tool" is wrong
in five of the six. `tests/skills/test_skills.py` holds every `SKILL.md` to this rule.

| Action in a skill | Claude Code | Codex |
|---|---|---|
| open / read a file | `Read` | shell-routed `cat`, reported as `Bash` |
| search the tree for text | `Grep` | shell-routed `rg`, reported as `Bash` |
| list files matching a pattern | `Glob` | shell-routed `rg --files`, reported as `Bash` |
| run a command | `Bash` | `Bash` |
| edit / create a file | `Edit`, `Write`, `NotebookEdit` | `apply_patch` (matches an `Edit\|Write` matcher) |
| fetch a page / search the web | `WebFetch`, `WebSearch` | the harness's browsing tool, when enabled |
| ask the user a question | `AskUserQuestion` | `request_user_input` in the modes that offer it (Plan mode by default): up to three questions of two or three options, no multi-select. Otherwise one plain question in the reply. |
| delegate to a sub-agent | `Agent` | not available; do the work inline |
| resolve a symbol precisely | `LSP` (when a language-server plugin is installed) | not available; fall back to searching |
| keep a running checklist | `TodoWrite` | a checklist in the reply |

Two columns because those are the two harnesses this plugin ships a manifest for. A third
harness adds a **column**, never a second vocabulary in the skills: fill each row with that
harness's own tool, and where it has none write what to do instead. The Codex column already
models both cases — a tool under another name (`apply_patch`), and a capability that is simply
absent ("do the work inline").

Skills reference the CLI by name (`drybag …`): the plugin root placeholder is not substituted
in skill content under Codex, so a path to the launcher would break there. Detail beyond a
short procedure goes in `<skill>/references/`.

`memory-sweep` wraps a procedure rather than a command: it names the commands it runs and
the reference that carries its detail.
