class BaseGameConfig:
    uri: str = ""  # The base URI for the game
    code_input_xpath: str = ""  # The XPath for the game code input field
    submit_code_button_selector: str = ""  # The selector for the submit code button
    nickname_input_xpath: str = ""  # The XPath for the nickname input field
    submit_nickname_button_selector: str = (
        ""  # The selector for the submit nickname button
    )
    require_secondary_code: bool = False  # Whether a secondary code is required
    secondary_code_input_xpath: str = ""  # The XPath for the secondary code input field
    secondary_code_submit_button_selector: str = (
        ""  # The selector for the secondary code submit button
    )
    excute_additional_js: bool = (
        False  # Whether to execute additional JavaScript after joining
    )
    excute_additional_js_wait_xpath: str = (
        ""  # The XPath to wait to be visible for before executing additional JS
    )
    excute_additional_js_code: str = (
        ""  # Additional JavaScript to execute after joining
    )
    use_custom_run_client: bool = False
    custom_run_client_custom_kargs: list[
        dict
    ] = []  # [{"prompt": "prompt to ask user when running", "key": "key"},...]

    @classmethod
    async def run_client(cls, code, browser, **kargs):
        pass


class Kahoot(BaseGameConfig):
    uri = "https://kahoot.it"
    code_input_xpath = '//*[@data-functional-selector="game-pin-input"]'
    submit_code_button_selector = '[data-functional-selector="join-game-pin"]'
    nickname_input_xpath = '//*[@data-functional-selector="username-input"]'
    submit_nickname_button_selector = '[data-functional-selector="join-button-username"]'


class MyShortAnswer(BaseGameConfig):
    uri = "https://app.myshortanswer.com/join"
    code_input_xpath = '//*[@id=":r1:"]'
    submit_code_button_selector = "xpath=/html/body/div/div/div/form/button"
    nickname_input_xpath = '//*[@id="text-input-go-input"]'
    submit_nickname_button_selector = "xpath=/html/body/div/form/div/div/div/button"


class Blooket(BaseGameConfig):
    uri = "https://play.blooket.com/play"
    code_input_xpath = "/html/body/main/div/form/div[1]/div/div/input"
    submit_code_button_selector = "xpath=/html/body/main/div/form/div[1]/div/button"
    nickname_input_xpath = "/html/body/div/div/div/div[2]/div/form/div[2]/div[1]/input"
    submit_nickname_button_selector = (
        "xpath=/html/body/div/div/div/div[2]/div/form/div[2]/div[2]"
    )


class MagicSchoolAI(BaseGameConfig):
    uri = "https://student.magicschool.ai/s/join"
    code_input_xpath = '//*[@id="field2"]'
    submit_code_button_selector = 'xpath=//*[@id="field1"]'
    nickname_input_xpath = '//*[@id="field1"]'
    submit_nickname_button_selector = "xpath=/html/body/div[2]/div/button"


class GoogleForms(BaseGameConfig):
    uri = ""
    use_custom_run_client = True
    custom_run_client_custom_kargs = [
        {"prompt": "Enter Google Forms URL: ", "key": "form_url"}
    ]

    @classmethod
    async def run_client(cls, code, browser, **kwargs):
        import random
        import string
        import asyncio

        form_url = kwargs.get("form_url", code)
        if not form_url.startswith("http"):
            form_url = "https://" + form_url

        def rand_text(min_len=5, max_len=50):
            n = random.randint(min_len, max_len)
            return "".join(random.choices(string.ascii_letters + string.digits + " ", k=n))

        async def click_update(page):
            # logged-in forms pop "Saved progress" over the page; hit Update so clicks work again
            for _ in range(3):
                dlg = page.locator("div[role='alertdialog']:visible, div[role='dialog']:visible")
                if not await dlg.count():
                    return False
                try:
                    xp = page.locator("xpath=/html/body/div[3]/div/div[2]/div[3]/div/span/span")
                    if await xp.count() and await xp.first.is_visible():
                        await xp.first.click(timeout=3000)
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(0.5)
                        continue
                    btn = dlg.first.get_by_text("Update", exact=True)
                    if not await btn.count():
                        btn = dlg.first.locator("div[role='button']:visible, button:visible").last
                    await btn.first.click(timeout=3000)
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(0.5)
                except Exception:
                    await page.keyboard.press("Escape")
            return True

        async def fill_page(page):
            await click_update(page)
            # multiple choice + grid rows
            groups = page.locator("div[role='radiogroup']")
            for i in range(await groups.count()):
                try:
                    opts = groups.nth(i).locator("div[role='radio']")
                    n = await opts.count()
                    if n:
                        await opts.nth(random.randrange(n)).click()
                        await asyncio.sleep(0.05)
                except Exception:
                    pass

            # checkboxes, at least one per question so required ones pass
            for item in await page.locator("div[role='listitem']").all():
                try:
                    cbs = item.locator("div[role='checkbox']")
                    n = await cbs.count()
                    if not n:
                        continue
                    picks = [j for j in range(n) if random.random() > 0.5] or [random.randrange(n)]
                    for j in picks:
                        if await cbs.nth(j).get_attribute("aria-checked") == "false":
                            await cbs.nth(j).click()
                            await asyncio.sleep(0.05)
                except Exception:
                    pass

            # dropdowns
            boxes = page.locator("div[role='listbox']")
            for i in range(await boxes.count()):
                try:
                    box = boxes.nth(i)
                    await box.click()
                    await asyncio.sleep(0.3)
                    opts = page.locator("div[role='option'][data-value]:visible")
                    vals = [o for o in await opts.all() if (await o.get_attribute("data-value"))]
                    if vals:
                        await random.choice(vals).click()
                    else:
                        await page.keyboard.press("Escape")
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

            # paragraph + short answer + email fields
            for el in await page.locator("textarea").all():
                try:
                    await el.fill(rand_text())
                except Exception:
                    pass
            for el in await page.locator("input[type='text']:visible, input[type='email']:visible, input[type='number']:visible, input[type='url']:visible").all():
                try:
                    t = await el.get_attribute("type")
                    if t == "email":
                        v = rand_text(6, 10).replace(" ", "x").lower() + "@gmail.com"
                    elif t == "number":
                        v = str(random.randint(1, 100))
                    elif t == "url":
                        v = "https://example.com"
                    else:
                        v = rand_text(3, 20)
                    await el.fill(v)
                except Exception:
                    pass

        page = None
        try:
            page = await browser.new_page()

            # the draft popup can show up at any point; auto-click Update before any action it blocks
            async def _on_draft(dlg):
                try:
                    xp = page.locator("xpath=/html/body/div[3]/div/div[2]/div[3]/div/span/span")
                    if await xp.count() and await xp.first.is_visible():
                        await xp.first.click(timeout=3000)
                    else:
                        await dlg.get_by_text("Update", exact=True).first.click(timeout=3000)
                except Exception:
                    await page.keyboard.press("Escape")

            try:
                await page.add_locator_handler(
                    page.locator("div[role='alertdialog'], div[role='dialog']").filter(has_text="Update"),
                    _on_draft,
                )
            except Exception:
                pass
            await page.goto(form_url)
            await page.wait_for_load_state("networkidle")

            for _ in range(30):  # walk through every section
                await fill_page(page)
                await asyncio.sleep(0.3)
                submit_btn = page.locator("div[jsname='M2UYVd'][role='button']").first
                next_btn = page.locator("div[jsname='OCpkoe'][role='button']").first
                if await click_update(page):
                    continue  # draft reloaded, fill this section again
                if await submit_btn.count() and await submit_btn.is_visible():
                    await submit_btn.click()
                    try:
                        await page.wait_for_url("**/formResponse**", timeout=10000)
                        print("Form submitted")
                    except Exception:
                        print("Submit clicked but no confirmation page (required field missed?)")
                    break
                if await next_btn.count() and await next_btn.is_visible():
                    before = await page.locator("div[role='list']").first.inner_text()
                    await next_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(0.5)
                    if "formResponse" not in page.url and await page.locator("div[role='list']").first.inner_text() == before:
                        print("Stuck on a section, a required field probably isn't filled")
                        break
                    continue
                print("No Next or Submit button found")
                break
        except Exception as e:
            print(f"Error: {e}")
        # leave the tab open so Send Screen works; the session's 10 min auto close cleans it up


supported_games = {
    "kahoot": Kahoot,
    "myshortanswer": MyShortAnswer,
    "blooket": Blooket,
    "magicschoolai": MagicSchoolAI,
    "googleforms": GoogleForms,
}
