# Transformation Agent

Applies a migration plan to the modernized target workspace one migration unit at
a time.

The first version is intentionally interactive:

1. Load the migration plan YAML.
2. Inject the OpenRewrite Maven plugin into the target root `pom.xml`.
3. Apply one migration unit.
4. Update `.migration/ledger.json` with `awaiting_build_agent`.
5. Pause and ask the user to run the Build Agent in another terminal.
6. Continue only if the Build Agent updated the ledger with a passed validation.

## Usage

```powershell
python -m migration_factory.agents.transformation_agent `
  C:\path\to\modernized-app `
  C:\path\to\rewrite-plugin.txt `
  C:\path\to\migration_plan.yaml
```

When the Transformation Agent pauses, run:

```powershell
python -m migration_factory.agents.build_agent `
  C:\path\to\modernized-app `
  --ledger-file C:\path\to\modernized-app\.migration\ledger.json
```

Then return to the Transformation Agent terminal and press Enter.

## Ledger Contract

The ledger lives at the path from the plan, typically:

```text
<modernized-app>/.migration/ledger.json
```

Important statuses:

- `unit_in_progress`: Transformation Agent is applying a unit.
- `awaiting_build_agent`: Unit finished and Build Agent must validate.
- `build_validated`: Build Agent succeeded for the current unit.
- `blocked`: Build Agent failed or a transformation command failed.
- `completed`: All migration units completed.

## Notes

- The Transformation Agent never runs tests in the legacy/source workspace.
- OpenRewrite transformations are executed with `mvn rewrite:run`.
- Custom code changes are recorded in the ledger as not executed in this first version.
- Full automation will later move into an orchestrator; the ledger format is designed for that handoff.
