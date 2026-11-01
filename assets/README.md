# README media

The figures and video share a deep-blue palette with cyan/violet tool paths and rose Pro scores.

| File | Source / purpose |
| --- | --- |
| `readme/logo.svg` | Logo from [the ToolSpace project website](https://toolspace-agent.github.io/logo.svg). |
| `readme/hero.svg`, `readme/hero.gif` | Editable schematic cover and its eight-second animated loop. The README selects the static cover for reduced motion. |
| `readme/title-light.svg`, `readme/title-dark.svg` | Enlarged logo and wordmark. GitHub's `#gh-light-mode-only` / `#gh-dark-mode-only` fragments follow the active GitHub theme. |
| `readme/framework.png` | Unchanged Figure 2 from page 6 of the authors' supplied `ToolSpace_Arxiv.pdf`. |
| `readme/results.svg` | Table 1 selected-model leaderboard and all seven ToolSpace-744B-A40B scores. SFT and subsequent RL are labeled separately. |
| `readme/pro-results.png` | The authors' supplied two-panel AutomationBench Pro / Banking Pro comparison, included unchanged. The README explains the paper/public-release task-count distinction. |
| Full video · GitHub issue attachment | 60-second Remotion paper demo, 1920 × 1080 at 30 fps, H.264 + AAC. The README embeds the [native GitHub attachment](https://github.com/user-attachments/assets/20d119c4-fc19-475f-9e29-e32a90258167), published in [issue #1](https://github.com/dongguanting/ToolSpace/issues/1). |
| `demo/toolspace-preview.gif` | Compact excerpt of the ToolSpace particle opening, environment reveal and multi-environment walk. |
| `demo/toolspace-poster.png` | ToolSpace wordmark and paper title, visible from the video's first frame. |
| `demo/toolspace-overview.vtt` | English caption track for the new video. |

## Video sources and scope

The video was rebuilt on 2026-11-01 from the supplied paper and the project homepage source. It opens with ToolSpace, expands into ten environments, follows a multi-environment tool walk, illustrates replay verification, and closes with the paper's results. Short action labels, a continuous camera and cyan/rose/amber paths show selection, rejection and resampling. There is no bottom chapter progress bar.

The starfield is a frame-deterministic Canvas port of the homepage's original hero: brightness tiers, warm/cool stars, halo gradients, flares, letter sampling and curved flights are preserved. A brief title cover precedes the particle formation. The tool close-up flows directly into four tracking moves before the camera pulls back to the environment network. The first ten environments use official Gmail, Drive, Sheets, Salesforce, Slack, Calendar, HubSpot, Notion, Zendesk and Asana logo images. The wider scene adds 38 authentic Simple Icons brand vectors; tool operations use Google Material icons. No generated photographs or emoji substitutes are used in this version.

The walk uses the homepage's graph and weighted sampler: graph seed `20261028`, walk seed `4`, 160 tool nodes, ten environments, 32 accepted path nodes and one rejected candidate. Each displayed hop belongs to that graph. Repeated calls count as separate steps. This is a **project-page graph simulation**; it is not live service execution. The green replay wave illustrates the paper's verification stage rather than claiming an independent execution check of this simulated route.

The final three scenes show the seven-benchmark model comparison, ToolSpace-generated Pro benchmarks, and environment/data scaling. Scores come from Tables 1–2 and Figure 7, not a new online benchmark run. The Pro image is author-supplied; its public 300-task label does not establish a rerun of the paper's reported 600-task AutomationBench Pro experiment.

The original 128 BPM score combines tonal percussion, bass, arpeggios and pitched transition accents. Noise-based hi-hats, snares, kick clicks and transition sweeps have been removed. [Arbor](https://github.com/RUC-NLPIR/Arbor) and [AREX-Skill](https://github.com/VectorSpaceLab/AREX-Skill) were studied for sparse composition, large transformations and visual continuity. [Remotion](https://www.remotion.dev/) and [React Bits](https://reactbits.dev/) informed the animation approach. No reference-demo footage, audio or artwork is included. Editable vectors contain no scripts or external font dependencies.
