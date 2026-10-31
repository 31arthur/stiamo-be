"""Running totals of what runs used."""

from typing import Any

from app.application.ports import UsageRow


class Bucket:
    """A running total."""

    def __init__(self) -> None:
        self.runs = self.errors = self.input = self.output = self.items = self.priced = 0
        self.cost = 0.0

    def add(self, r: UsageRow) -> None:
        self.runs += 1
        self.errors += r.status != "ok"
        self.input += r.input_tokens
        self.output += r.output_tokens
        self.items += r.items
        if r.cost is not None:
            self.priced += 1
            self.cost += r.cost

    def json(self) -> dict[str, Any]:
        # No cost at all is not the same as a cost of nothing: it means the provider doesn't say.
        return {"runs": self.runs, "errors": self.errors, "inputTokens": self.input, "outputTokens": self.output, "items": self.items, "cost": round(self.cost, 6) if self.priced else None, "pricedRuns": self.priced}
