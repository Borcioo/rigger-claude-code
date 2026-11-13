## Plans

- Libraries, versions and tools come from the source, not memory: spike assumptions in the real
  runtime with the exact call and input form the code will use; add dependencies with the install
  command, not by editing the manifest. A command, library or plugin setting proposed as a fix or
  workaround enters a plan only with the failure it fixes named next to it, checked against these
  rules (and the superpowers skills, when installed), and spiked before you present it; say what
  one does only after observing it; a greenfield choice follows the spike rule alone. Judge an
  external tool's mechanism apart from its package: a wrong platform rejects the code, never the
  idea.
- Work past twice its estimate stops and reports before going on: code lines count against the
  code estimate and test lines against the test estimate. A fix round adds a test only for
  the finding it fixes.
