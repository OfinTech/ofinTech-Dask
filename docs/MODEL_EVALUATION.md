# Evaluating the model on real data

The model's recorded scores — 97.3 on the CFAI set and 96.6 on the hidden set, against 95.1 and 93.8 for the base
model — were measured on examples produced by the same generator the model was trained on. That makes them a measure
of fit to that generator rather than of ability on a real Saudi bank statement.

Two results inside the same run make the point without needing an outside benchmark to prove it. After fine-tuning,
safety fell from 92.2 to 89.1, and ranking the largest overdue customer fell from 91.2 to 83.8. A headline that
rises while specific skills fall is what overfitting to a distribution looks like.

So the first job for real client documents is measurement, not training. The kit for that lives in
[`tools/benchmark/`](../tools/benchmark/README.md), which covers the consent and de-identification procedure, how
many documents to collect and in what spread, the ground-truth format, and how grading works.

Two things from it are worth repeating here, because they are product rules rather than tool details.

**Client data never enters this repository.** The benchmark's corpus root is resolved through a check that refuses
any directory inside a git work tree, and the de-identification step deletes its own output if that output still
carries a value found in the original. The procedure around it — written client consent or a documented basis,
originals kept outside the corpus, the re-identification key kept away from the output — is in the kit's README.

**The harness holds no vendor SDK.** A model is reached through a callable named on the command line. Comparing a
base model against a fine-tuned adapter against a hosted API is the whole point of the exercise, and a harness that
could only talk to one of them would decide the answer before the run started.

If the benchmark shows the fine-tune helping on documents neither model was trained on, collecting more real data
for training is worth the work. If it shows the fine-tune hurting, the fix is better tools and prompts rather than
more weights — the arithmetic the model is weakest at is already computed in code.
