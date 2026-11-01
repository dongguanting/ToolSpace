# README media

The figures and video share a deep-blue palette with cyan/violet tool paths and rose Pro scores.

| File | Source / purpose |
| --- | --- |
| `readme/logo.svg` | Logo from [the ToolSpace project website](https://toolspace-agent.github.io/logo.svg). |
| `readme/hero.svg`, `readme/hero.gif` | Editable schematic cover and its eight-second animated loop. The README selects the static cover for reduced motion. |
| `readme/title-light.svg`, `readme/title-dark.svg` | Enlarged logo and wordmark. GitHub's `#gh-light-mode-only` / `#gh-dark-mode-only` fragments follow the active GitHub theme. |
| `readme/framework.png` | Unchanged Figure 2 from page 6 of the authors' supplied `ToolSpace_Arxiv.pdf`. |
| `readme/results.svg` | Table 1 selected-model leaderboard and all seven ToolSpace-744B-A40B scores. SFT and subsequent RL are labeled separately. |
| `readme/pro-results.svg`, `readme/pro-results-data.json` | Updated Table 2: seven frontier models and 28 standard/Pro values, checked against the homepage and the authors' revised table. |
| Full video · GitHub issue attachment | 60-second Remotion paper demo, 1920 × 1080 at 30 fps, H.264 + AAC. The README embeds the [native GitHub attachment](https://github.com/user-attachments/assets/2fbcfd47-ff94-4aa4-a6d2-697a27aa94b2), published in [issue #1](https://github.com/dongguanting/ToolSpace/issues/1). |
| `demo/toolspace-preview.gif` | Compact excerpt of the ToolSpace particle opening, environment reveal and multi-environment walk. |
| `demo/toolspace-poster.png` | ToolSpace wordmark and paper title, visible from the video's first frame. |
| `demo/toolspace-overview.vtt` | English caption track for the new video. |

## Video sources and scope

The video was rebuilt on 2026-11-01 from the supplied paper and the project homepage source. It opens with ToolSpace, expands into ten environments, follows a multi-environment tool walk, illustrates replay verification, and closes with the paper's results. Short action labels, a continuous camera and cyan/rose/amber paths show selection, rejection and resampling. There is no bottom chapter progress bar.

The starfield is a frame-deterministic Canvas port of the homepage's original hero: brightness tiers, warm/cool stars, halo gradients, flares, letter sampling and curved flights are preserved. The title cover and opening particle page include the full paper title, all 25 homepage authors and both institutions. The three opening captions introduce isolated environments, real tools and cross-environment walks. The camera zooms from ten environments into named Google Sheets tools, then follows four tool calls with the current tool and environment counts visible. The first ten environments use official Gmail, Drive, Sheets, Salesforce, Slack, Calendar, HubSpot, Notion, Zendesk and Asana logo images. The wider scene adds 38 authentic Simple Icons brand vectors; tool operations use Google Material icons. No generated photographs or emoji substitutes are used in this version.

The walk uses the homepage's graph and weighted sampler: graph seed `20261028`, walk seed `4`, 160 tool nodes, ten environments, 32 accepted path nodes and one rejected candidate. Each displayed hop belongs to that graph. Repeated calls count as separate steps. This is a **project-page graph simulation**; it is not live service execution. The green replay wave illustrates the paper's verification stage rather than claiming an independent execution check of this simulated route.

The full 32-call red chain expands across the screen, then folds into a labelled seven-call excerpt. Two animated branches reveal a natural-language task and assertion scripts, following the paper figure. Training shows all three backbones and outputs, with Agent RL for 8B / 35B and a direct SFT path for 744B. The final three scenes show the seven-benchmark model comparison, ToolSpace-generated Pro benchmarks, and environment/data scaling. Scores come from Tables 1–2 and Figure 7, not a new online benchmark run. Pro values use the authors' updated Table 2 and homepage, including DeepSeek-V4.1-Flash-1017. Both Pro panels animate every model from its standard score to its Pro score and show the resulting drop. The README distinguishes the public 300-task AutomationBench Pro package from the paper's 600-task experiment.

The current soundtrack is [Dolce Vita by Peyruis](https://www.youtube.com/watch?v=x0x8pHeFAgw), supplied by the authors and edited to 60 seconds with a short fade-in and a 1.5-second fade-out. It replaces the original synthetic score. A smooth time remap aligns visual cues to the measured 122 BPM grid; individual cues move by at most 0.3 seconds. Captions follow the revised timing. [Arbor](https://github.com/RUC-NLPIR/Arbor) and [AREX-Skill](https://github.com/VectorSpaceLab/AREX-Skill) were studied for sparse composition, large transformations and visual continuity. [Remotion](https://www.remotion.dev/) and [React Bits](https://reactbits.dev/) informed the animation approach. No reference-demo footage, audio or artwork is included. Editable vectors contain no scripts or external font dependencies.
