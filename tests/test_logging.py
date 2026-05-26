import logging

from saturn.utils.logging import setup_logging


def test_setup_logging_sets_level():
    setup_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING
    setup_logging("INFO")
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_bad_level_defaults_to_info():
    setup_logging("NOPE")
    assert logging.getLogger().level == logging.INFO
