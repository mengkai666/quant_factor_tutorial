from __future__ import annotations


class DeliveryPipeline:
    def __init__(self, runner=None):
        self.runner = runner

    def run(self, context: dict) -> dict:
        if self.runner is not None:
            context["delivery"] = self.runner(context)
        return context
