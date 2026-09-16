"""Browser regression suite against deterministic Codex protocol fixture.

Run: uv run --python 3.12 --with playwright python tests/browser_check.py
"""
from pathlib import Path
import base64
import sys
import time

from playwright.sync_api import sync_playwright
from test_server import ServerTests


def main():
    ServerTests.setUpClass()
    artifacts = Path(__file__).resolve().parents[1] / 'test-results'
    artifacts.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={'width':1440,'height':1000})
            page = context.new_page()
            errors = []
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(ServerTests.port))
            page.locator('#token').fill(ServerTests.token)
            page.locator('#login-form button').click()
            page.locator('#app').wait_for(state='visible')
            page.get_by_text('Connected · local workspace',exact=True).wait_for()
            page.screenshot(path=str(artifacts/'desktop.png'))

            page.locator('#prompt').fill('Please check the workspace.')
            page.locator('#send').click()
            page.get_by_text('Verified',exact=True).wait_for()
            page.locator('#stop').wait_for(state='hidden')
            assert page.locator('.message.user').count()==1
            assert page.locator('.message:not(.user)').count()==1
            assert page.locator('pre code').inner_text()=='print("hello")'
            page.reload()
            page.get_by_text('Verified',exact=True).wait_for()
            assert page.locator('.message.user').count()==1

            # Approval remains pending across page refresh and another browser session.
            page.locator('#prompt').fill('Please test long approval.')
            page.locator('#send').click()
            page.get_by_role('button',name='Allow once',exact=True).wait_for()
            page.reload()
            page.get_by_role('button',name='Allow once',exact=True).wait_for()
            phone = context.new_page()
            phone.set_viewport_size({'width':390,'height':844})
            phone.goto(page.url)
            phone.get_by_role('button',name='Allow once',exact=True).wait_for()
            for width, height in [(390, 844), (375, 667)]:
                phone.set_viewport_size({'width':width, 'height':height})
                assert phone.locator('.request-details').evaluate('(e) => e.scrollHeight > e.clientHeight')
                for name in ['Allow once', 'Allow for session', 'Decline', 'Stop turn']:
                    bounds = phone.get_by_role('button', name=name, exact=True).bounding_box()
                    assert bounds and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= height, (name, bounds)
                phone.locator('.request-details').evaluate('(e) => e.scrollTop = e.scrollHeight')
                assert phone.get_by_role('button', name='Allow once', exact=True).is_visible()
            phone.set_viewport_size({'width':390, 'height':844})
            phone.screenshot(path=str(artifacts/'phone-approval.png'))
            phone.get_by_role('button',name='Allow once',exact=True).click()
            page.get_by_role('button',name='Allow once',exact=True).wait_for(state='hidden')
            phone.locator('#stop').wait_for(state='hidden')
            assert phone.locator('.message:not(.user)').count()==2

            phone.locator('#prompt').fill('Ask me a question.')
            phone.locator('#send').click()
            phone.get_by_text('Which color?',exact=True).wait_for()
            phone.get_by_role('button',name='Green — Use green').click()
            phone.get_by_role('button',name='Send answers',exact=True).click()
            phone.locator('#stop').wait_for(state='hidden')
            phone.get_by_text('Which color?',exact=True).wait_for(state='hidden')

            phone.locator('#file-input').set_input_files({'name':'phone-note.txt','mimeType':'text/plain','buffer':b'Phone attachment'})
            phone.locator('.attachment').wait_for()
            assert 'phone-note.txt' in phone.locator('.attachment').inner_text()
            phone.locator('#files-open').click()
            phone.get_by_role('button',name='hello.txt',exact=True).click()
            phone.get_by_text('hello remote',exact=True).wait_for()
            phone.locator('#panel-close').click()

            phone.locator('#model-button').click()
            phone.locator('#model-select').select_option('test-model')
            phone.locator('#effort-select').select_option('high')
            phone.locator('#model-save').click()
            assert 'high' in phone.locator('#model-button').inner_text()

            # A foreground refresh must preserve attachment and next-turn settings.
            phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            phone.wait_for_timeout(500)
            assert phone.locator('.attachment').count()==1
            assert 'high' in phone.locator('#model-button').inner_text()
            phone.locator('[data-remove]').click()

            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            phone.locator('#file-input').set_input_files({'name':'phone-image.png','mimeType':'image/png','buffer':png})
            phone.locator('.attachment img').wait_for()
            phone.wait_for_function("() => document.querySelector('.attachment img')?.naturalWidth > 0")
            phone.locator('#prompt').fill('An image from my phone.')
            phone.locator('#send').click()
            phone.locator('.message.user img').wait_for()
            phone.wait_for_function("() => document.querySelector('.message.user img')?.naturalWidth > 0")
            phone.locator('#stop').wait_for(state='hidden')
            phone.reload()
            phone.locator('.message.user img').wait_for()
            phone.wait_for_function("() => document.querySelector('.message.user img')?.naturalWidth > 0")

            phone.locator('#prompt').fill('A slow turn please.')
            phone.locator('#send').click()
            phone.locator('#stop').wait_for(state='visible')
            phone.locator('#stop').click()
            phone.locator('#stop').wait_for(state='hidden')

            # Untrusted messages must remain inert.
            phone.locator('#prompt').fill('<img src=x onerror="window.xss=true"> [bad](javascript:alert(1))')
            phone.locator('#send').click()
            phone.locator('#stop').wait_for(state='hidden')
            phone.wait_for_timeout(500)
            assert phone.evaluate('window.xss') is None
            assert phone.locator('a[href^="javascript:"]').count()==0
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
            phone.screenshot(path=str(artifacts/'phone-chat.png'))

            phone.locator('#menu').click()
            phone.locator('#new-chat').click()
            phone.wait_for_timeout(250)
            phone.screenshot(path=str(artifacts/'phone-home.png'))
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
            print('PASS: desktop/mobile chat, streaming, reload, approvals across tabs, questions, uploads, previews, settings, interrupt, XSS, and responsive layout.')
            print('Screenshots: '+str(artifacts))
    finally:
        ServerTests.tearDownClass()


if __name__=='__main__': main()
