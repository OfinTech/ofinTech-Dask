# Token use and cost

Model providers charge per token, and an agent sends the whole context again at every step. OfinTech Desk keeps
each request small and shaped for the providers' prompt caches.

## What is done for every request

| Measure | Effect |
|---|---|
| **Tools chosen per request.** Tool definitions are the largest part of a request (about 32,000 characters for all built-in tools). A request carries a core set (calculators, document search, memory, skills, MCP tools) plus only the groups the conversation needs: files, Google, data, accounting, workflows, collaboration. Groups come in when words in the recent messages call for them (in English, Arabic, Spanish or French), when datasets exist, when an attached Excel/Word file is to be edited, or when the thread used them before. | In the test suite, a simple calculation question went from **32,783 to 14,973 characters (54% smaller)**, 27 tools to 16. |
| **`more_tools`.** If the model needs a group that was not sent, it asks for it and gets it on the next step; a tool that was not sent still runs if the model names it. | Selection never blocks work. |
| **Stable prefix.** The system prompt is split: rules, workspace profile, connections, datasets, skills index and pinned skills first (the same on every call), then the short part that changes (today's date, run instructions, language, the memories picked for this message). Tools are sent in a fixed order. | Providers can serve the prefix from their cache. |
| **Anthropic prompt caching.** Cache breakpoints after the tools, after the stable system block and on the latest message, so each step of a run and the next turn re-read the shared prefix from the cache. | Cached input is billed at a fraction of the normal input price (see Anthropic's pricing page). |
| **Automatic caching elsewhere.** OpenAI-compatible providers that cache repeated prefixes automatically (such as OpenAI and DeepSeek) benefit from the same stable order; the cached tokens they report are recorded. | — |
| **Older tool results shortened.** The two most recent turns are sent in full; tool results from earlier turns are cut to 600 characters with a note that the tool can be called again. Any single tool result is capped at 16,000 characters. The full results stay stored and still count as sources for the figure check and for files. | Long conversations stop growing with every report the assistant has read. |
| **A model for short jobs.** Settings → Models → *Model for short jobs*: a smaller, cheaper model for search answers. | Use a premium model only where reasoning matters. |
| **Search answers without tools.** The *Answer* button on the Search page makes one model call with the top results and no tool definitions. | A few hundred tokens of context. |

## Seeing the effect

Settings → Usage lists calls, input and output tokens per model for the last 30 days, and the share of input tokens
served **from cache**.

## Settings

- `DESK_TOOL_SELECTION=off` sends every tool with every request (for troubleshooting a model that misses tools).
- Per conversation: choose a model in *Model and skills*; per workflow step: choose a model on assistant steps.

The measurement is reproduced by `engine/tests/test_tokens.py` (it prints the sizes when run).
