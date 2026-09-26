# Heuristics

> **Layer 1 — Reusable heuristics** (methodology.md §17)
>
> Anonymised failure patterns, which feed the *error guessing* technique (§5.2) when generating cases. They only come in here **after anonymising**: no names, URLs, data, texts or screenshots of any client. The first three come from the demo's bugs (`examples/demo/`), found on public practice sites.

Format: **component / type of screen → failure pattern → how to test** (module · origin).

| Component / screen | Failure pattern | How to test | Module · origin |
|---|---|---|---|
| Creation endpoint (POST) | A missing required field answers **5xx** instead of 4xx (the invalid input breaks the server instead of being refused) | One request per required field, each one without that field, and another with an empty body; expected 400/422 naming the field (RFC 9110 for 4xx vs 5xx) | `api` · demo, BUG-1 |
| Pages of a web application (especially an SPA) | Every page has the same `<title>` (the application's name): navigation changes the content but not the title (WCAG 2.4.2 A) | In each main view, compare `document.title` with the view's heading; equal titles in different views = Fail | `accessibility` · demo, BUG-2 |
| List or grid of items with an image | Every item shows the same image (the image URL is fixed instead of coming from the item) | Compare the `src` of the images between different items of the same list, not only whether the image loads; repeat with each role, because the defect may affect only one | `functional-web` · demo, BUG-3 |
| Focusable elements (buttons, links, fields) | `outline: none` with no other focus style: the focused element looks the same as when it is not focused (WCAG 2.4.7 AA) | Tab through the page and compare each element focused and not focused; the qa-audit engine does it by pixels (`focus-not-visible`, plan 8.17) | `accessibility` · practice site, plan 8.17 |
| IBAN field | Accepts spaces when typing but fails validation on submit | Test with and without spaces, with lower case and with the country prefix | `functional-web` · example of methodology §17 |
