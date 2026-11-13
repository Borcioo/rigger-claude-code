---
name: web-ui-traps
description: >-
  Load BEFORE building or changing UI components: dialogs and confirmations, optimistic updates,
  per-item action buttons, ARIA and accessible names, form controls (consent or newsletter
  opt-ins, WooCommerce checkout fields), translated labels, UI tests (jsdom, Testing Library,
  browser), phone-width checks, sliders, third-party embeds, modal scroll lock; and when a button
  "does nothing".
---

# Web UI traps

Each entry: what happens, then what to do.

1. **Native `alert`/`confirm`/`prompt` are suppressed in embedded browsers** (the Claude app pane,
   many webviews): `confirm()` returns false with no dialog, so a delete silently did nothing. Product
   flows use an in-page accessible dialog. When someone reports "the button does nothing", read the console in their browser context before touching the server.
2. **CSS generated content joins the accessible name.** A pressed-state tick added with
   `::before { content: '✓ ' }` became part of the button's name in Chromium, so
   `getByRole({ name, exact: true })` stopped matching once pressed. Decorative content gets empty
   alternative text — `content: '✓';` then `content: '✓' / '';` on the next line as the override —
   and state lives in `aria-pressed`, not in the name.
3. **One source for the visible label and the accessible name.** A separate `aria-label` msgid
   drifted in translation ("Ausblenden" on screen, "%s schliessen" as the name), failing WCAG 2.5.3
   only in German. Build the name from the visible label's own string (`sprintf( '%1$s: %2$s', label,
   context )`), or keep a test per pair checking the translated name starts with the translated
   label. A button with visible extra text (key cap, count) is named by that text: drop the
   `aria-label`.
4. **Absence checks are scoped to the component.** "The error is gone" matched the same text still
   sitting in the app-wide live region, so a correct fix looked red. Query inside the component
   (`within(table)`, a locator under it), never screen-wide, for any message that is also announced.
5. **Opt-in controls render unticked on every visit.** A new optional consent checkbox (a
   WooCommerce additional checkout field) came back ticked on the next checkout: WooCommerce restores
   the last value from the customer session and the draft order, and Firefox restores form state on
   a plain reload. Test the return visit — same session, after a placed order, after reload — not only the
   first render.
6. **Optimistic UI keeps the old data on failure.** Lock the controls while a request is in flight;
   when it fails, keep the previous data and say so. Never show "empty" because a request failed.
   After an async answer, move focus only if focus is still where the action started.
7. **The UI offers only actions the server will accept.** Drive each item's buttons from per-item
   flags the server computes with its own check, and prove a claimed server answer with a test
   before changing the UI to match it.
8. **A UI test identifies its target before the action.** Capture the element's identity (id, text)
   before clicking or typing; after a re-render, never re-query it by position, which can land on a
   different item.
9. **Visibility is not box size.** Chrome reports layout boxes (`getBoundingClientRect`) for the
   content of a closed `<details>` (`content-visibility: hidden`), so a "hidden links have zero
   height" check fails on correct markup. Test with `checkVisibility()` or Playwright's
   `isVisible()`, never with a rect.
10. **On a phone, "on screen" means inside `window.visualViewport`.** A page wider than the device
    (CSS zoom, an overflowing iframe) stretches the layout viewport while the visual viewport keeps
    the device size; fixed elements anchor to the layout viewport, so `innerWidth` /
    `innerHeight` accept an element the user cannot reach. Compare rects with
    `visualViewport.offsetLeft/offsetTop + width/height`. Under Playwright `isMobile` the layout
    widens too: compare overflow with `page.viewportSize().width`.
11. **A hold flag is cleared by the DOM, not by a leave event.** A removed node fires no `blur` and
    no `mouseleave`, so a remembered focus or press state stays set. Read the state from the
    DOM (`:hover`, `document.activeElement`) or clear it from a document-level listener, and give
    every hold flag a test that removes the node under the pointer or focus and expects the hold to
    end.
12. **A positive control must survive the layout it is inserted into.** A `height: 600px` probe
    shrank to nothing in a flex container (`flex-shrink: 1`) and the overflow detector stayed green:
    give the insert `flex-shrink: 0`.
13. **Count text lines from text nodes, never from element rects.** Use a few px of tolerance; an
    inline chip with a different font size reads as its own line when you count rectangles.
14. **Port the prototype's (or live site's) render, not its documentation.** Measure the computed
    style and box of the prototype element before porting: a later stylesheet can win the cascade
    over the documented values, and a pixel-tolerance comparison then fails on the docs. When the
    render contradicts the docs, port the render and report the conflict. Diff rebuilt sections
    against the source by screenshot at the same width.
15. **An axe gate proves only what axe sees.** A "no new violations" rule ships with a per-task axe
    spec; allowlist a failing node by your own selector, never axe's `target` string (it changes
    with the page); after content moves layer (shadow root, top layer) a known failing node proves
    axe still sees it.
16. **A sliver of the next slide means a fractional frame width.** Set the slider viewport to whole
    pixels (`width: round(down, 100%, 1px)`), never pad or clip slides; check every slide's edges.
17. **Third-party embeds (captcha, payment, reviews, chat):** a stub proves only your logic, so
    watch the real embed for 5 s after load (inserted, removed, resized). Never hide one you still
    need by clipping or zero size (use opacity 0 + absolute), never lock scroll by shifting `body`
    under one (use `overflow: hidden` on `html`), and open a modal from its real, scrolled trigger.

## Tools on demand

- **Accessibility scan** (axe through Playwright: `scan_page`, `audit_site`, `audit_keyboard`): in
  the project that needs it, run this in a terminal, then start a new session (`cmd /c` is for
  Windows; drop it on macOS and Linux):
  `claude mcp add accessibility-scanner -s local -- cmd /c npx -y mcp-accessibility-scanner`
