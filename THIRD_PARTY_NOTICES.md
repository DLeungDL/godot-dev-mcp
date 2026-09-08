# Third-party notices

Version 0.2.0 remains a clean-room implementation and contains no copied or
adapted source from the projects below. Their public GitHub interfaces were
reviewed on 2026-09-08 to define portable adapter boundaries:

- https://github.com/hi-godot/godot-ai — MIT. Its separate scene, node,
  resource, script, and signal handlers support keeping AUTHOR operations
  modular. godot-dev-mcp does not import or vendor those handlers.
- https://github.com/beckettlab/beckett-godot-mcp — MIT. Only the public
  read-only observation concepts were considered; Beckett Full remains out of
  scope and no Beckett source is included.
- https://github.com/mrf/godot-stagehand — MIT. Stagehand remains an external
  process adapter. godot-dev-mcp consumes exit status, JUnit, screenshot, and
  visual-diff artifacts without vendoring its addon or CLI.

The reviewed repositories change independently; compatibility is by artifact
and process boundaries rather than private implementation details. Before any
future source copy or adaptation, preserve the exact upstream copyright,
license text, commit provenance, and modification notice here.

