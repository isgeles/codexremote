"""Real-browser Markdown/math regression checks using the fake Codex server."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_server import ServerTests

SAMPLE = r'''# Markdown and mathematics

**Bold**, *italic*, ~~removed~~, and [a **formatted** link](https://example.com).

1. First
   - Nested item
2. Second

- [x] Finished
- [ ] Pending

> A quotation with $a_i^2$.

| Formula | Meaning |
| --- | --- |
| $E=mc^2$ | Energy |

Inline $x_1 + x_2$ and \(\frac{a}{b}\).

$$
\begin{aligned}
f(x) &= \int_0^x t^2\,dt \\
     &= \frac{x^3}{3}
\end{aligned}
$$

\[\begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix}\]

Code stays literal: `$not_math$` and `\(also_not_math\)`.

```latex
$$literal$$
\[literal\]
```

Cost: $5 and $10. Escaped: \$20. Incomplete: $x +

[Local file](hello.txt)

<img src=x onerror="window.injected=1">
[bad](javascript:alert(1))
$\href{javascript:alert(1)}{bad}$
$\htmlData{file=/etc/passwd}{bad}$
$\includegraphics{https://example.com/tracker.png}$

Malformed: $\frac{a}{$.
'''


def main():
    ServerTests.setUpClass()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors, external, violations = [], [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: external.append(r.url) if not r.url.startswith('http://127.0.0.1:') else None)
            page.on('console', lambda m: violations.append(m.text) if 'Content Security Policy' in m.text else None)
            page.goto(f'http://127.0.0.1:{ServerTests.port}')
            page.locator('#token').fill(ServerTests.token)
            page.locator('#login-form button').click()
            page.locator('#app').wait_for(state='visible')
            page.locator('#prompt').fill(SAMPLE)
            page.locator('#send').click()
            page.get_by_text('Verified', exact=True).wait_for()
            body = page.locator('.message.user .message-body')
            assert body.locator('h1').inner_text() == 'Markdown and mathematics'
            assert body.locator('ol ul li').inner_text() == 'Nested item'
            assert body.locator('s').inner_text() == 'removed'
            assert body.locator('a strong').inner_text() == 'formatted'
            assert body.locator('input[type=checkbox]:checked').count() == 1
            assert body.locator('table .katex').count() == 1
            assert body.locator('blockquote .katex').count() == 1
            assert body.locator('.katex-display').count() == 2
            assert body.locator('.katex').count() >= 9
            assert body.locator('code .katex').count() == 0
            assert body.locator('pre code').inner_text() == '$$literal$$\n\\[literal\\]'
            assert 'Cost: $5 and $10. Escaped: $20. Incomplete: $x +' in body.inner_text()
            assert body.locator('img, [onclick], [onerror], a[href^="javascript:"]').count() == 0
            assert body.locator('[data-file]').count() == 1
            assert not page.evaluate('window.injected || false')
            assert body.locator('.katex-error').count() >= 1
            body.get_by_role('link', name='Local file').click()
            page.get_by_text('hello remote', exact=True).wait_for()
            page.locator('#panel-close').click()
            page.reload()
            page.locator('.message.user .katex-display').first.wait_for()
            assert body.locator('.katex-display').count() == 2
            await_fonts = page.evaluate('async () => { await document.fonts.ready; return document.fonts.check("16px KaTeX_Main"); }')
            assert await_fonts
            page.locator('#prompt').fill('Wide equation:\n\n$$' + '+'.join(['x_i^2'] * 60) + '=0$$')
            page.locator('#send').click()
            page.wait_for_function('() => document.querySelectorAll(".message.user .katex-display").length === 3')
            wide = page.locator('.message.user .katex-display').last
            assert wide.evaluate('(e) => e.scrollWidth > e.clientWidth')
            assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
            page.evaluate('() => { document.querySelector("#messages").scrollTop = 0; }')
            artifacts = Path(__file__).resolve().parents[1] / 'test-results'
            artifacts.mkdir(exist_ok=True)
            page.screenshot(path=str(artifacts / 'markdown-phone.png'))
            assert not errors, errors
            assert not external, external
            assert not violations, violations
            browser.close()
            print('PASS: Markdown, math, mobile overflow, local links, persisted history, fonts, CSP and injection checks')
    finally:
        ServerTests.tearDownClass()


if __name__ == '__main__':
    main()
