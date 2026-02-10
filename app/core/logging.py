import logging
import sys


LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
