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
| `demo/toolspace-overview.mp4` | New 60-second Remotion paper demo, 1920 × 1080 at 30 fps, H.264 + AAC. The README embeds the [native GitHub attachment](https://github.com/user-attachments/assets/59ea5ea4-1aea-486f-aefa-cc26240f8fa4), published in [issue #1](https://github.com/dongguanting/ToolSpace/issues/1). |
| `demo/toolspace-preview.gif` | Compact excerpt of the homepage particle reveal and graph transition. |
| `demo/toolspace-poster.png` | Paper-introduction frame from the new video. |
| `demo/toolspace-overview.vtt` | English caption track for the new video. |

## Video sources and scope

The video was rebuilt on 2026-11-01 from the supplied paper, the project homepage source, and the released offline reconciliation example. The starfield is a frame-deterministic Canvas port of the homepage's original hero: its brightness tiers, warm/cool stars, halo gradients, flares, letter sampling and curved flights are preserved. An added dispersal joins the title to the schematic tool graph.

The everyday scenes and workflow photographs were generated for this film. They illustrate the context of each action and are not screenshots or records from external services. Screen copy is limited to short action titles, key values and status changes; there is no bottom chapter progress bar.

The five-call walkthrough follows real local simulator execution and independent replay; model responses use the bundled fixtures. A fresh diagnostic trace records the unsuccessful branch: a candidate Slack message succeeds, then an append uses a messaging account as its spreadsheet destination and receives `400 INVALID_ARGUMENT`. The sampler restores that checkpoint, backtracks to Routing and undoes the candidate message before taking the successful branch. Both restored-state hashes match their checkpoints.

The successful path reads Budget, Actuals and Routing, writes Actual 1500 / Variance 500 to Reconciliation, and posts to Slack channel CFINANCE01. The saved-row and channel-message checks and replay result come from that example. Scores come from Tables 1 and 2, not a new online benchmark run. The Pro image is author-supplied; its public 300-task label does not establish a rerun of the paper's reported 600-task AutomationBench Pro experiment.

The electronic score is original procedural audio. [Arbor](https://github.com/RUC-NLPIR/Arbor) and [AREX-Skill](https://github.com/VectorSpaceLab/AREX-Skill) were studied for pacing, terminal/window choreography, and graph transitions; none of their footage, audio, or artwork is included. Editable vectors contain no scripts or external font dependencies.
