# Synthetic browser UI tests

From a clean checkout or Linux container:

```sh
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s ui_tests -v
```

`ui_fixture.py` generates temporary board and encrypted Call Mode pages from the shipped templates using five fabricated leads. It does not read lead caches, access codes or contact files. The fixture-only password is public test input. Dates are relative to today. Contact emails are absent, phone numbers use the reserved fictional 555-01xx range, and screenshots/generated pages are not committed.

The browser context blocks service workers and intercepts all network traffic, including popups. Only GETs for the two localhost fixture pages reach the server. Health responses are fake; writes and external requests receive a synthetic refusal. No production eligibility or suppression function is replaced.

The CI browser job is separate from `ci_suite.py` so the fast source tests continue without a browser. It covers board load, Worker buttons, Skip, lane switching, call outcomes, Start/Stop, Call Mode decryption and the wrong-number chooser. The existing `_callmodetest.py` and `_workerui.py` remain legacy real-board acceptance suites; this isolated suite covers their core flows without relying on private laptop state. Other legacy suites need individual migration rather than blanket removal from the CI skip list.

## Container

```sh
docker build -f Dockerfile.ui -t dealflow-ui .
docker run --rm --network none -v "$PWD:/app:ro" dealflow-ui
```

Use a clean checkout for the mount. The image copies only `requirements.txt`, never source caches or keys. Runtime networking is disabled; loopback remains available for the temporary fixture server. Browser-context interception additionally refuses every write and external request. The fixture clock places Worker controls within daytime; production hours checks remain unchanged. Call Mode verifies the displayed first number without assuming the real clock permits a dial.
