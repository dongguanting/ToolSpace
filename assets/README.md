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
| `demo/toolspace-overview.mp4` | New 60-second Remotion paper demo, 1920 × 1080 at 30 fps, H.264 + AAC. The README embeds the [native GitHub attachment](https://github.com/user-attachments/assets/23d9e0d7-d3d5-4b1c-9d9d-afecb3fda5f1), published in [issue #1](https://github.com/dongguanting/ToolSpace/issues/1). |
| `demo/toolspace-preview.gif` | Compact excerpt of the new workflow and rollback animation. |
| `demo/toolspace-poster.png` | Paper-introduction frame from the new video. |
| `demo/toolspace-overview.vtt` | English caption track for the new video. |

## Video sources and scope

The video was rebuilt on 2026-11-01 from the supplied paper and the released offline reconciliation example. The opening windows are conceptual UI reconstructions. The full tool-space network is schematic. The five-call walkthrough follows real local simulator execution and independent replay; model responses use the bundled fixtures. The run recorded one execution rejection and one backtrack. Because the failed candidate's exact call was not logged, the branch animation illustrates the mechanism without inventing an error trace.

The successful path reads Budget, Actuals and Routing, writes Actual 1500 / Variance 500 to Reconciliation, and posts to Slack channel CFINANCE01. The displayed native assertions and replay result come from that example. Scores come from Tables 1 and 2, not a new online benchmark run. The Pro image is author-supplied; its public 300-task label does not establish a rerun of the paper's reported 600-task AutomationBench Pro experiment.

The electronic score is original procedural audio. [Arbor](https://github.com/RUC-NLPIR/Arbor) and [AREX-Skill](https://github.com/VectorSpaceLab/AREX-Skill) were studied for pacing, terminal/window choreography, and graph transitions; none of their footage, audio, or artwork is included. Editable vectors contain no scripts or external font dependencies.
