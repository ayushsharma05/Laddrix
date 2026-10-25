# Laddrix documentation

Everything about how Laddrix works, how to run it and where it is going. The code is in
[`src/`](../src) (server) and [`ui/`](../ui) (screens); this folder explains it.

## Start here

| If you want to… | Read |
| --- | --- |
| run everything on your own computer, and test it | **[running-locally.md](running-locally.md)** |
| understand the whole system from zero, with pictures | [the guide](guide/README.md) (ten chapters) or [the guide on one page](guide/summary.md) |
| read the code in a sensible order | [guide/reading-order.md](guide/reading-order.md) |
| know what Laddrix is for and its rules | [design/product-vision.md](design/product-vision.md) |
| see the architecture | [design/kite-architecture.md](design/kite-architecture.md) |

## By kind

| Folder | What is in it |
| --- | --- |
| [design/](design/README.md) | How each part is built and why: orders, risk, algos, options, charts, research, security, several firms on one server, performance |
| [runbooks/](runbooks/README.md) | What the operator does: the morning, incidents, the server, releases, patching, go-live, hosting several firms |
| [test/](test/README.md) | What each layer of checks proves, and what must pass before a release |
| [roadmap/](roadmap/README.md) | Where it is going: the feature issues, and the research behind the later ones |
| [brand/](brand/README.md) | The name, the logo and its colours |
| [releases/](releases/) | Release notes, one per version (and the [changelog](../CHANGELOG.md)) |
| [archive/](archive/README.md) | The paused COMEX/CME track and project history: kept, not current |

## Decisions

Laddrix keeps no separate decision records. Each decision is written where it is used:
- **why and when:** [context/conversation-log.md](../context/conversation-log.md), dated, with
  what was asked, what was decided and why;
- **what the design is now:** the docs in [design/](design/README.md).

## How to contribute

[CONTRIBUTING.md](../CONTRIBUTING.md): the issue → PR → CI loop and the checks each change brings.

## Words

New to a term? [design/glossary.md](design/glossary.md) explains each one in plain words.
