# Roadmap

What comes next is tracked as GitHub issues, one per feature, each with its scope and when it is
done. This folder holds the thinking behind them.

| Where | What |
| --- | --- |
| [Open feature issues](https://github.com/ayushsharma05/MockTrade/issues) | Every planned feature. Titles starting "Later:" are the researched expansion; "Post-v1" are the professional-trading features; "Decision:" need a choice before work starts |
| [feature-research-2026-10-16.md](feature-research-2026-10-16.md) | The research behind the "Later:" issues: which feature families exist, what MockTrade already has, the dependencies, and the limits of the evidence |
| [../design/product-vision.md](../design/product-vision.md) | The feature catalogue and principles every feature must keep (replaceable parts; the backend provides, the frontend displays; no vendor named or copied) |

**Order of work:**
- The steps only Ayush can do come first. They are in the issue "Ayush's to-do list": go-live,
  Zerodha's answers and the security review.
- Then the features, smallest dependency first. Each goes issue → branch → pull request with
  `Closes #N` → green CI.
