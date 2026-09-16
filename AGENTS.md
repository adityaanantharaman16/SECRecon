# Implementation context

Read `docs/PROJECT_STATE.md`, then the relevant sections of `docs/PROJECT_GUIDE.md`, before implementing changes. These describe a planned system; do not assume uncompleted milestones exist in code.

- Work on the next incomplete milestone unless the user explicitly changes scope. Implement a small, reviewable slice at a time.
- Keep the required path local and free. Do not introduce cloud dependencies to satisfy core milestones.
- Preserve raw bytes and provenance. Use decimal arithmetic. Never silently overwrite fact history or label an absent amendment fact as deleted.
- Use PostgreSQL for authoritative jobs and atomic commits; Redis delivery alone does not establish exclusive ownership or durability.
- Run the checks appropriate to the slice. A milestone is complete only when its documented gate passes and evidence is recorded.
- At the end of each implementation session, update `docs/PROJECT_STATE.md` with changes, commands and outcomes, limitations, and the next concrete task. Explain what the owner can now demonstrate and the engineering concept involved.
- Record material architecture changes in `docs/adr/` and update the guide. Respect explicit user changes to these defaults.
- Inspect the current working tree before editing. Preserve unrelated work. Do not treat this document as authorization to publish the repository or provision paid services.
- Branch names for this project must exclude `codex`. Use descriptive prefixes such as `feat/`, `fix/`, `test/` or `docs/` (for example, `feat/m4-reconciliation`).
