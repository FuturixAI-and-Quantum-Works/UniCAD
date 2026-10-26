# todo: component library + reference data

Playbook: Feature

- [ ] 1. `how` over the affected subsystem. skip: greenfield repo, nothing exists to read beyond PLAN.md
- [ ] 2. `architect` for parallel design exploration. skip: data shapes fixed in PLAN.md section 3; symbol registry shape decided inline below
- [ ] 3. Throughput checkpoint
  - Blocking first steps: scaffold package + data shapes (symbol registry types, catalog models), commit
  - Independent workstreams: (a) web research equipment data, (b) web research sample drawings, (c) symbol library build, (d) catalog seed build after (a)
  - Shared mutable state: each workstream writes its own directory; only I touch pyproject/PLAN
  - Smallest safe decomposition: 2 research agents + 2 code delegates
- [ ] 4. Delegate code-writing (symbols, catalog) in own worktrees
- [ ] 5. Verify on real artifacts (LibreCAD renders symbol sheet, catalog validates)
- [ ] 6. Small ordered commits
- [ ] 7. interrogate. skip: unless design contested
- [ ] 8. Opening a PR. skip: no remote; local commits only
