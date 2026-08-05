from __future__ import annotations


class AnalysisPipeline:
    def __init__(self, runner=None):
        self.runner = runner

    def run(self, context: dict) -> dict:
        if self.runner is not None:
            context["analysis"] = self.runner(context)
        return context
