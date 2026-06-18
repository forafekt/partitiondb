# partitiondb Roadmap

This document describes the intended direction for `partitiondb`. It is aspirational and strategic, not a commitment that every idea listed here will be implemented exactly as written.

The project is currently a Django-first extraction from a larger application. The roadmap is about turning that working history into a cleaner, better documented, and more portable open-source package.

## Phase 1 - Open Source Extraction

The first phase is about making the repository understandable and safe to evaluate as an independent project.

- Cleanly extract the package from the original Django application.
- Preserve existing behaviour while removing assumptions that only made sense in the original host project.
- Stabilize the repository layout and packaging metadata.
- Improve the README and usage documentation.
- Identify missing runtime dependencies and development dependencies.
- Add an explicit license.
- Remove generated artifacts that do not belong in source control.
- Document the current Django and PostgreSQL assumptions.
- Create a small example Django project or test fixture that demonstrates the supported setup.

## Phase 2 - Harden Django Support

Django should remain a first-class integration. Before introducing broader abstractions, the existing Django behaviour should become easier to trust and easier to adopt.

- Add focused tests for tenant model mixins, domain handling, middleware, routers, and utilities.
- Add integration tests for PostgreSQL schema switching and migration flows.
- Document recommended Django settings for shared apps, tenant apps, domain models, middleware order, and database routers.
- Define which Django APIs are public and which are internal.
- Clarify the relationship between `PartitionDbMixin` and the older schema-focused mixins.
- Strengthen tenant creation, deletion, schema cloning, and schema renaming workflows.
- Review migration executors and management commands for predictable behaviour.
- Improve error messages around missing settings and invalid tenant state.
- Document best practices for tenant-aware static files, media files, templates, Celery tasks, and admin usage.
- Make compatibility targets explicit, including supported Python, Django, PostgreSQL, and psycopg versions.

## Phase 3 - Framework-Agnostic Core

Once the Django integration is better understood, the next step is to explore which concepts can live outside Django.

Possible core concepts include:

- tenant identity,
- partition identity,
- partition activation and deactivation,
- schema and database naming,
- tenant resolution,
- lifecycle hooks,
- partition provisioning,
- migration orchestration,
- storage path resolution,
- context propagation,
- schema/database abstractions.

This phase should be exploratory. The goal is to find clean boundaries, not to force a rewrite.

Potential outcomes:

- A small core package with no Django dependency.
- A Django adapter that maps core concepts onto Django models, middleware, management commands, and database backends.
- Clear interfaces for resolving tenants and activating partitions.
- Explicit separation between policy, framework integration, and database-specific operations.
- Better portability for applications that want partitioning concepts without adopting the full Django integration.

## Phase 4 - Adapter Ecosystem

After the core boundaries are clearer, `partitiondb` may support additional adapters and integrations.

Possible adapter ideas include:

- Django,
- SQLAlchemy,
- FastAPI,
- Flask,
- Starlette,
- Celery,
- command-line or worker-only runtimes.

These are ideas, not commitments. The Django adapter should mature first, and new adapters should be driven by real use cases rather than added for completeness.

## Design Principles

- Prefer incremental evolution over rewrites.
- Preserve existing behaviour where practical.
- Make breaking changes explicit and documented.
- Keep Django support first-class.
- Move reusable concepts into framework-neutral layers only when the boundary is clear.
- Prefer explicit behaviour over hidden magic.
- Keep tenant resolution, partition activation, and lifecycle hooks composable.
- Keep database-specific behaviour behind clear interfaces.
- Avoid coupling core concepts to HTTP, Django models, or a single database backend.
- Treat operational safety as part of the design, especially for tenant deletion, schema drops, migrations, and database creation.
- Document assumptions instead of relying on inherited project knowledge.
- Add tests around behaviour before large internal restructuring.

## Near-Term Priorities

- Finalize licensing.
- Declare runtime and development dependencies.
- Add a minimal test suite.
- Document a supported Django configuration from a fresh project.
- Decide which tenant mixin should be the primary public API.
- Audit management commands for naming consistency and non-interactive use.
- Clarify how database-based and mixed tenancy should be supported in practice.
- Publish examples that demonstrate current behaviour without implying future features already exist.
