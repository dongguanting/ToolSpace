# README media

These assets support the root README and share the project's dark blue, violet, and teal visual palette.

| File | Source / purpose |
| --- | --- |
| `readme/logo.svg` | Logo from [the ToolSpace project website](https://toolspace-agent.github.io/logo.svg). |
| `readme/hero.svg` | Editable vector cover, with a schematic tool-composition graph. |
| `readme/hero.gif` | Eight-second seamless cover loop, 1280 × 480 at 16 fps: drifting graph nodes, flowing path lights, and subtle star pulses. Text stays still; the README uses the static cover when reduced motion is requested. |
| `readme/title-light.svg`, `readme/title-dark.svg` | Enlarged ToolSpace wordmark with the website logo aligned in a single vector, for light and dark themes. |
| `readme/framework.png` | Figure 2 extracted from page 6 of the authors' supplied `ToolSpace_Arxiv.pdf`; figure content is unchanged. |
| `readme/results.svg` | All seven benchmark scores, comparing backbones with ToolSpace at three model scales; transcribed from Table 1. |
| `demo/toolspace-overview.mp4` | Edited 60-second overview, 1920 × 1080, H.264, with on-screen English captions. Silent. The root README embeds its [GitHub-hosted attachment](https://github.com/user-attachments/assets/05d377d9-8247-4a8a-92b0-70aa658579af) as a native player. Published in [issue #1](https://github.com/dongguanting/ToolSpace/issues/1); this file is the source copy. |
| `demo/toolspace-preview.gif` | A compact loop showing the graph walkthrough and all seven benchmark results. |
| `demo/toolspace-poster.png` | Still frame for video previews. |
| `demo/toolspace-overview.vtt` | English caption track for accessible playback. |

The video reframes the [website's interactive graph](https://toolspace-agent.github.io/#demo), then summarizes the method and paper-reported results. The graph is an **illustrative JavaScript simulation**, not a recording of live MCP services or the released Python replay verifier. It is labeled accordingly in the video and README. Only presentation, framing, and captions were adjusted; the simulation's transitions and outcomes were retained.

The website and paper were consulted on 2026-10-31. The results table shows all seven benchmark scores and the reported average for each backbone (8B and 35B with RL; 744B with SFT). The video pauses on a real failure event in the website simulation, then highlights each scale in the complete results table. The editable SVG files contain no scripts or external font dependencies.
