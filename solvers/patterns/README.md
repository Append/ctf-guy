# Learned solve patterns

> **This directory is licensed separately from the rest of the repository.**
>
> Everything under `solvers/patterns/` is licensed under
> [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE)
> (CC BY-NC 4.0) — **noncommercial use only**.
>
> The rest of the repository, including all source code, is licensed under
> Apache License 2.0. See the [LICENSE](../../LICENSE) at the repository root.

## What this is

One JSON file per challenge category, appended to by `bot/ai/learner.py` after a
solve. Each entry records the pattern, the key insight, the approach taken, tools
used, and the flag, for challenges from CTFs that have already concluded. The
corpus is fed back into later solver prompts as prior knowledge.

## Using it

You may share and adapt this corpus for noncommercial purposes with attribution.
Incorporating it into a commercial product or service is not permitted under this
license — contact the copyright holder if you need commercial terms.

Note that CC BY-NC covers this compilation and its text. It does not, and cannot,
restrict the underlying facts or techniques described.
