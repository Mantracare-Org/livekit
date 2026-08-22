# TESTING.md

## Framework and Structure
- Currently, there is no explicit automated testing framework (such as `pytest` or `unittest`) visible in the standard locations (`tests/`, `mantra/tests`).

## Mocking and Coverage
- Since no automated test suite is present, mocking and coverage reports are not configured.
- Testing is presumably performed manually, either via:
  - Local shell scripts like `dev.sh`.
  - HTTP clients / cURL requests directed at the local `ui_server.py`.
  - The static frontend UI.
  - Live SIP trunk inbound/outbound triggers.

## Recommendations
- Introduce `pytest` as the primary testing framework.
- Implement `pytest-asyncio` for the asynchronous boundaries.
- Mock Redis using `fakeredis` and stub the LiveKit SDK API clients to prevent live outbound API calls during test runs.
