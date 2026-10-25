# Showcase: what we built (#455)

Nine posters of MockTrade as it stood on 25 October 2026, after 13 days and 143 merged pull requests. Each one is a page in this folder, and `render.cjs` turns it into a PNG in `context/images/showcase/`.

| # | Picture | What it shows |
|---|---------|---------------|
| 1 | ![](../images/showcase/01-hero.png) | **MockTrade in numbers**: 143 PRs, 84k lines, over 1,750 tests, 87 mutation scripts, 27 screens |
| 2 | ![](../images/showcase/02-architecture.png) | **Architecture**: market, then brokers behind one interface, then the engine, the server and the screens, plus stores, operations and deploy |
| 3 | ![](../images/showcase/03-order-journey.png) | **One order's journey**: a click on the ladder, then the ticket, the safety gate (8 checks), the broker and the fill, then P&L, the audit trail and the screen |
| 4 | ![](../images/showcase/04-price-path.png) | **The price path**: Kite, the simulator or a replay feeds the market data service, which fans out to the ladder, charts, alerts, spreads, risk and the recorder, then the stream carries it to the screens |
| 5 | ![](../images/showcase/05-safety.png) | **Six rings of safety** around the money: exchange rules, sign-in and firms, the order gate, live risk, the audit trail, and the code itself |
| 6 | ![](../images/showcase/06-journey.png) | **The journey**: from learning, to the switch to Kite on 14 Oct, through phases K2–K13, to spreads, operations and the review |
| 7 | ![](../images/showcase/07-screens.png) | **The screens**: real screenshots (ladder, trading page, option chain, autospreader, spread chart) and the six widget groups |
| 8 | ![](../images/showcase/08-quality.png) | **The quality machine**: issue, brainstorm, code, tests, mutants, a real browser, then PR and CI. Also explains what a mutation test is |
| 9 | ![](../images/showcase/09-algos.png) | **The algo engine**: slicer, iceberg, POV, TWAP/VWAP, autospreader, spreader and protect |

## Redraw them

```
node context/showcase/render.cjs          # all nine
node context/showcase/render.cjs 03       # only 03-order-journey
```

Each poster is plain HTML that shares `style.css`. To change one, edit its `.html` file and run the command again. The numbers on poster 1 were counted on 25 Oct (lines with `wc -l`, tests by counting `def test_` and `it(`). Re-count them when you refresh it.

## The video

[video/mocktrade-showcase.mp4](video/mocktrade-showcase.mp4) is 51 seconds at 1920×1200. It shows the nine posters with slow zooms and slide transitions, then an end card, over music. The music is computed from sine waves (a chord pad, a bass line, a soft kick), so there is nothing to license. To rebuild it after changing the posters:

```
node context/showcase/render.cjs && python3 context/showcase/video/make_video.py
```
