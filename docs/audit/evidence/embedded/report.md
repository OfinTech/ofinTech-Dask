# Embedded gateway — browser verification

Run on 2026-11-01 18:17 UTC.

| # | Check | Result | Detail |
|---|---|---|---|
| E1 | widget renders RTL for Arabic | PASS | dir=rtl |
| E2 | streamed answer arrives | PASS | Revenue grew from 881,000 to 5,920,200: a 159% compound annual growth rate over two years.  |
| E3 | tool call shown (calc_growth) | PASS | ['تم الاطلاع على calc_growth'] |
| E4 | generative-UI component rendered from a validated payload | PASS | 1 component(s) |
| E5 | no token in web storage | PASS | 0 keys |
| E8 | no console errors during the run | PASS | none |
| E6 | origin not on the allow-list is refused | PASS | status=blocked:Failed to fetch |
| E7 | session token is useless on the Desk API | PASS | browser=blocked:Failed to fetch direct=401 |
| E9 | run audited and metered | PASS | actions=['embedded.integration.create', 'embedded.session.issue', 'embedded.run.start', 'embedded.run.finish'] usage=[('done', 1)] |

**9 passed, 0 failed.**

Screenshot: `E2-E4-answer-rtl.png`. Partner page and 'evil' page served from two different local origins; the engine ran as a
separate process in server mode against a fake OpenAI-compatible model.
