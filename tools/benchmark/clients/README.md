# Clients

`ofinbench run --client module:function` loads one callable. That is the only thing the harness knows about a model.

```python
def ask(question: str, document: str) -> str:
    """Answer `question` about `document`, and return the answer as text."""
```

The harness deliberately holds no vendor SDK, no endpoint and no default. Comparing a base model against a
fine-tuned adapter against a hosted API is the whole point of the exercise, and a harness that could only talk to
one of them would decide the answer before the run started. It is also the product's rule: nothing binds it to a
single model vendor.

Write your client wherever you keep the model, and put it on `PYTHONPATH`:

```bash
PYTHONPATH=~/models python -m ofinbench.cli run --client my_local_adapter:ask --label cfai-full
PYTHONPATH=~/models python -m ofinbench.cli run --client my_base_model:ask  --label base
python -m ofinbench.cli compare base cfai-full
```

Two rules for whatever you write:

- **No key in the source.** Read credentials from the environment. A client file with a token in it will end up in a
  repository eventually.
- **No network for a local adapter.** The corpus is de-identified, but a de-identified client statement is still
  client work product. If the model runs locally, keep the run local.

`echo_client.py` beside this file answers nothing and exists only to prove the wiring before a real model is
pointed at it.
