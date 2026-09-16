"""Exercise slash-command routing and the command picker without model usage."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from test_server import ServerTests


def main():
    ServerTests.setUpClass()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={'width':1280,'height':900})
            page = context.new_page()
            calls, errors = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: calls.append(r.post_data_json) if r.url.endswith('/api/rpc') else None)
            page.goto('http://127.0.0.1:' + str(ServerTests.port))
            page.locator('#token').fill(ServerTests.token)
            page.locator('#login-form button').click()
            page.locator('#app').wait_for(state='visible')

            def command(text):
                page.locator('#prompt').fill(text)
                page.locator('#send').click()
                expect(page.locator('#send')).to_be_enabled()

            def turns():
                return [c for c in calls if c['method'] in {'turn/start','turn/steer'}]

            page.locator('#prompt').fill('/')
            expect(page.locator('#slash-menu')).to_be_visible()
            page.locator('#prompt').press('Escape')
            expect(page.locator('#slash-menu')).to_be_hidden()
            page.locator('#prompt').fill('/m')
            expect(page.locator('#slash-menu [role=option]')).to_have_count(2)
            page.locator('#prompt').press('ArrowDown')
            expect(page.locator('#slash-menu [aria-selected=true]')).to_contain_text('/mcp')
            page.locator('#prompt').press('ArrowUp')
            page.locator('#prompt').press('Tab')
            expect(page.locator('#prompt')).to_have_value('/model')
            page.locator('#prompt').press('Enter')
            expect(page.locator('#panel-title')).to_have_text('Model & behavior')
            page.locator('#panel-close').click()
            assert not turns(), 'A client command was sent to the model'

            command('/model test-model')
            expect(page.locator('#model-button')).to_contain_text('Test model')
            command('/reasoning high')
            expect(page.locator('#model-button')).to_contain_text('high')
            command('/permissions read-only')
            command('/plan')
            expect(page.locator('#mode')).to_have_value('plan')
            command('/code')
            expect(page.locator('#mode')).to_have_value('default')
            assert not turns()

            command('/compact')
            expect(page.locator('#toast')).to_contain_text('Start or open')
            expect(page.locator('#prompt')).to_have_value('/compact')
            command('/not-a-command')
            expect(page.locator('#toast')).to_contain_text('Unsupported command')
            command('/prompts:custom')
            expect(page.locator('#toast')).to_contain_text('Unsupported command')
            assert not turns()

            command('/plan Explain the project')
            page.get_by_text('Verified',exact=True).wait_for()
            page.locator('#stop').wait_for(state='hidden')
            assert len(turns()) == 1
            request = turns()[0]['params']
            assert request['input'][0]['text'] == 'Explain the project'
            assert request['collaborationMode']['mode'] == 'plan'
            assert request['sandboxPolicy']['type'] == 'readOnly'
            assert request['effort'] == 'high'

            count = len(turns())
            command('/rename Slash commands work')
            expect(page.locator('#thread-title')).to_have_text('Slash commands work')
            command('/compact')
            assert any(c['method']=='thread/compact/start' for c in calls)
            command('/review')
            assert any(c['method']=='review/start' for c in calls)
            command('/diff')
            expect(page.locator('#panel-title')).to_have_text('Latest turn diff')
            page.locator('#panel-close').click()
            command('/goal')
            expect(page.locator('#panel-title')).to_have_text('Long-running goal')
            page.locator('#panel-close').click()
            command('/skills')
            expect(page.locator('[data-skill]')).to_have_count(1)
            page.locator('[data-skill]').click()
            expect(page.locator('.attachment')).to_contain_text('test-skill')
            page.locator('[data-remove]').click()
            command('/mcp')
            expect(page.locator('#panel-content')).to_contain_text('test-mcp')
            page.locator('#panel-close').click()
            command('/approvals')
            expect(page.locator('#panel-title')).to_have_text('Model & behavior')
            page.locator('#panel-close').click()
            command('/files')
            expect(page.locator('#file-list')).to_contain_text('hello.txt')
            page.locator('#panel-close').click()
            command('/status')
            expect(page.locator('#panel-title')).to_have_text('Your machine')
            page.locator('#panel-close').click()
            assert len(turns()) == count

            command('//model')
            page.wait_for_timeout(400)
            assert turns()[-1]['params']['input'][0]['text'] == '/model'
            page.locator('#stop').wait_for(state='hidden')
            command('/tmp/example.txt')
            page.wait_for_timeout(400)
            assert turns()[-1]['params']['input'][0]['text'] == '/tmp/example.txt'
            page.locator('#stop').wait_for(state='hidden')

            command('slow turn')
            page.locator('#stop').wait_for(state='visible')
            count = len(turns())
            command('/plan change things')
            expect(page.locator('#toast')).to_contain_text('current turn')
            assert len(turns()) == count
            page.locator('#stop').click()
            page.locator('#stop').wait_for(state='hidden')
            old_url = page.url
            command('/fork')
            assert page.url != old_url
            command('/clear')
            expect(page.locator('#thread-title')).to_have_text('New conversation')

            phone_context = browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,storage_state=context.storage_state())
            phone = phone_context.new_page()
            phone.on('pageerror',lambda e: errors.append(str(e)))
            phone.goto('http://127.0.0.1:' + str(ServerTests.port))
            phone.locator('#app').wait_for(state='visible')
            phone.locator('#prompt').fill('/')
            phone.locator('#slash-menu').wait_for(state='visible')
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
            box = phone.locator('#slash-menu').bounding_box()
            assert box['y'] >= 0 and box['x'] >= 0
            artifacts = Path('test-results'); artifacts.mkdir(exist_ok=True)
            phone.screenshot(path=str(artifacts/'slash-phone.png'))
            phone.locator('[data-slash=model]').tap()
            expect(phone.locator('#panel-title')).to_have_text('Model & behavior')
            phone.locator('#panel-close').tap()
            phone.locator('#prompt').fill('/help')
            phone.locator('#send').tap()
            expect(phone.locator('#panel-title')).to_have_text('Slash commands')
            phone.locator('[data-help-command=plan]').tap()
            expect(phone.locator('#prompt')).to_have_value('/plan')
            assert not errors, errors
            browser.close()
            print('PASS: slash routing, keyboard navigation, mobile taps, arguments, mode/permissions, thread actions, guards, unknown commands, and literal slash/path messages.')
    finally:
        ServerTests.tearDownClass()


if __name__ == '__main__': main()
