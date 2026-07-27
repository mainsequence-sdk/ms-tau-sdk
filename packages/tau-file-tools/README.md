# tau-file-tools

Independent `grep`, `find`, and `ls` tools for
[Tau](https://github.com/huggingface/tau) agents.

This distribution lives at `packages/tau-file-tools` inside the Astro
monorepo. “Independent” describes its Python package boundary; it is not a
separate repository.

The package only imports Tau's public `tau_agent` contracts. It has no dependency
on Astro and is structured so each tool can be proposed upstream independently.

```python
from tau_file_tools import create_file_tools

tools = create_file_tools(cwd="/workspace")
```

`grep` requires `rg` (ripgrep) on `PATH`. `find` and `ls` use Python's standard
library and exclude `.git` and `node_modules` while traversing.
