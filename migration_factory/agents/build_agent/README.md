# Build Agent

Runs a Maven or Gradle Java/Spring Boot application and reports whether the app
starts successfully.

When startup, compilation, dependency resolution, command execution, or project
detection fails, the agent writes a JSON error contract under:

```text
migration_factory/contracts/build
```

## Agent call

```python
from migration_factory.agents.build_agent import run_build_agent

result = run_build_agent(
    r"C:\path\to\java-project",
    timeout_seconds=120,
    stream_output=False,
)

if not result.succeeded:
    print(result.error_contract_path)
```

## Manual CLI

```powershell
python -m migration_factory.agents.build_agent C:\path\to\java-project --timeout 120
```

When validating a unit produced by the Transformation Agent, pass the migration
ledger file:

```powershell
python -m migration_factory.agents.build_agent C:\path\to\modernized-app --ledger-file C:\path\to\modernized-app\.migration\ledger.json
```

For multi-module Maven projects:

```powershell
python -m migration_factory.agents.build_agent C:\path\to\java-project
```

By default, the agent reads the parent `pom.xml`, looks for Maven modules, finds
the module containing a Spring Boot main class, and runs Maven with:

```text
-f <module>/pom.xml -Dspring-boot.run.mainClass=<discovered.MainClass> spring-boot:run
```

You can still override this explicitly:

```powershell
python -m migration_factory.agents.build_agent C:\path\to\java-project --module app-service --main-class com.example.Application
```

## Failure Contract

Each failure JSON includes:

- project path
- detected build tool
- executed command
- failure kind and message
- matched log line when available
- exit code
- module and main class inputs
- stdout/stderr tails for downstream diagnosis

jakarta error run this command : PS C:\Users\ur_folder\shoppoc-app> C:\Tools\apache-maven-3.9.15\bin\mvn.cmd clean install -DskipTests

