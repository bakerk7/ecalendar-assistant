import os
# Tests use a fixed sample family (tests/config.test.json), independent of any local config.
os.environ["FAMCAL_CONFIG"] = os.path.join(os.path.dirname(__file__), "config.test.json")
