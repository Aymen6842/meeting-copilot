"""Root pytest configuration: makes the shared fixtures available to every package's tests."""

pytest_plugins = ["tests.fixtures.containers"]
