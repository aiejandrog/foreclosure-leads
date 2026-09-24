import records_liens as r


class Locator:
    def __init__(self, page, selector): self.page, self.selector = page, selector
    @property
    def first(self): return self
    def count(self): return 1
    def click(self, **_): pass
    def fill(self, value): self.page.fills[self.selector] = value


class Page:
    def __init__(self): self.fills = {}; self.listeners = {}
    def on(self, event, callback): self.listeners[event] = callback
    def goto(self, *_a, **_k): pass
    def wait_for_load_state(self, **_): pass
    def wait_for_timeout(self, _):
        if 'request' in self.listeners:
            request = type('Request', (), {'url': 'https://x/getStandardRecords?qs=token%2Bvalue&x=1'})()
            self.listeners['request'](request)
    def locator(self, selector): return Locator(self, selector)
    def remove_listener(self, *_): pass
    def close(self): pass


class Browser:
    def __init__(self): self.page = Page()
    def new_page(self): return self.page


def test_camoufox_search_fills_last_and_first_name():
    browser = Browser()
    assert r.camoufox_qs(browser, ('VIGIL', 'MAURICIO'), settle=750) == 'token+value'
    assert next(v for k, v in browser.page.fills.items() if '#lastName' in k) == 'VIGIL'
    assert next(v for k, v in browser.page.fills.items() if '#firstName' in k) == 'MAURICIO'


def test_camoufox_company_search_does_not_fill_first_name():
    browser = Browser()
    assert r.camoufox_qs(browser, ('AMWEST FUNDING CORP', ''), settle=750)
    assert next(v for k, v in browser.page.fills.items() if '#lastName' in k) == 'AMWEST FUNDING CORP'
    assert not any('#firstName' in k for k in browser.page.fills)
