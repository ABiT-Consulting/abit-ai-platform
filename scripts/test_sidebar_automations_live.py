"""Read-only live browser acceptance for the ABiT sidebar Automations menu.

Requires an authorized, short-lived session in AIBIT_QA_TOKEN and optionally
AIBIT_QA_CHAT_ID. Does not create chats, send prompts, or change automations.
Never prints the session credential or private record contents.
"""
import json
import os
from pathlib import Path
import re
import sys

from playwright.sync_api import sync_playwright


def main():
    token = os.environ.get('AIBIT_QA_TOKEN', '')
    base = os.environ.get('AIBIT_QA_BASE_URL', 'http://127.0.0.1:8888').rstrip('/')
    chat_id = os.environ.get('AIBIT_QA_CHAT_ID', '')
    if not token:
        raise RuntimeError('An authorized QA session is required')
    passed = []
    blocked = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(
                headless=True,
                executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            )
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_init_script('localStorage.setItem("token", ' + json.dumps(token) + ');')
        headers = {'Authorization': 'Bearer ' + token}
        response = context.request.get(base + '/api/v1/auths/', headers=headers)
        assert response.status == 200, 'Authorized session was not accepted'
        passed.append('authorized_session_accepted')
        config = context.request.get(base + '/api/config', headers=headers)
        assert config.status == 200 and config.json()['features']['enable_automations']
        passed.append('automation_feature_enabled')
        data = context.request.get(base + '/api/v1/automations/list?page=1', headers=headers)
        assert data.status == 200 and isinstance(data.json().get('items'), list)
        passed.append('live_automation_list_responds')

        def guard(route):
            request = route.request
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and (
                '/automations/' in request.url or '/chat/completions' in request.url
            ):
                blocked.append('mutating_request_blocked')
                route.abort()
            else:
                route.continue_()

        context.route('**/*', guard)
        page = context.new_page()
        page.goto(base + '/', wait_until='domcontentloaded', timeout=60000)
        button = page.locator('#sidebar-automations-button')
        button.wait_for(state='visible', timeout=60000)
        assert button.count() == 1
        assert button.get_attribute('href') == '/automations'
        projects = page.get_by_text('Projects', exact=True).first
        projects.wait_for(state='visible')
        assert button.bounding_box()['y'] < projects.bounding_box()['y']
        workspace = page.locator('#sidebar-workspace-button')
        if workspace.count() and workspace.is_visible():
            assert workspace.bounding_box()['y'] < button.bounding_box()['y']
        passed.append('one_automations_menu_above_projects')
        with page.expect_response(lambda r: '/api/v1/automations/list' in r.url and r.status == 200):
            button.click()
        page.wait_for_url(re.compile(r'/automations(?:\?.*)?$'), timeout=30000)
        page.locator('#sidebar-automations-button').wait_for(state='visible')
        passed.append('click_opens_live_automations_page')
        page.reload(wait_until='domcontentloaded')
        page.locator('#sidebar-automations-button').wait_for(state='visible', timeout=60000)
        assert '/automations' in page.url
        passed.append('automation_page_and_menu_survive_refresh')
        if chat_id:
            chat = context.request.get(base + '/api/v1/chats/' + chat_id, headers=headers)
            assert chat.status == 200
            page.goto(base + '/c/' + chat_id, wait_until='domcontentloaded')
            page.locator('#sidebar-automations-button').wait_for(state='visible', timeout=60000)
            page.locator('#chat-container').wait_for(state='visible', timeout=60000)
            passed.append('existing_chat_opens_with_menu')
            link_button = page.get_by_text('Open in ChatGPT', exact=False)
            if link_button.count():
                passed.append('previous_chatgpt_link_feature_visible')
        page.goto(base + '/', wait_until='domcontentloaded')
        page.locator('#sidebar-automations-button').wait_for(state='visible', timeout=60000)
        page.locator('#chat-container').wait_for(state='visible', timeout=60000)
        passed.append('new_chat_screen_opens')
        folders = context.request.get(base + '/api/v1/folders/', headers=headers)
        assert folders.status == 200 and isinstance(folders.json(), list)
        passed.append('existing_projects_accessible')
        models = context.request.get(base + '/api/models', headers=headers)
        assert models.status == 200
        passed.append('configured_models_list_accessible')
        tools = context.request.get(base + '/api/v1/tools/', headers=headers)
        assert tools.status == 200
        passed.append('integrations_list_accessible')
        assert not blocked, 'Unexpected automation mutation or message submission attempted'
        page.set_viewport_size({'width': 390, 'height': 844})
        # The menu uses the same permission-filtered list on mobile; opening
        # the sidebar is exercised only when its toggle is visible.
        mobile_toggle = page.locator('#sidebar-toggle-button').first
        if not page.locator('#sidebar-automations-button').is_visible() and mobile_toggle.is_visible():
            mobile_toggle.click()
        if page.locator('#sidebar-automations-button').is_visible():
            passed.append('mobile_automations_menu_visible')
        context.close()
        browser.close()
    print(json.dumps({'live_browser_checks_passed': len(passed), 'checks': passed,
                      'prompts_sent': 0, 'automations_changed': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never expose request headers, session tokens or private page text.
        print(json.dumps({'live_browser_test_failed': type(error).__name__,
                          'reason': str(error).split('Call log:')[0][:400]}))
        sys.exit(1)
