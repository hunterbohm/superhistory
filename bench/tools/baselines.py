from tools import Retrieved, Tool


class Nothing(Tool):
    label = "No lookup"

    def retrieve(self, question):
        return Retrieved("", None, 0.0)


class Oracle(Tool):
    """The full text of the items that hold the answer: the most any lookup could hand the agent."""
    label = "Gold records"

    def load(self, items, workdir):
        self.items = {i["source_id"]: i for i in items}
        return {}

    def retrieve(self, question):
        records = [self.items[sid] for sid in question["evidence"]]
        if not records:
            return Retrieved("", [], 0.0)
        text = "Records from the knowledge base:\n\n" + "\n\n".join(
            f"[{r['ts'][:10]} {r['source']}] {r['title']}\n{r['content']}" for r in records)
        return Retrieved(text, list(question["evidence"]), 0.0)
