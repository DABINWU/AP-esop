import argparse
import logging
from logging.handlers import RotatingFileHandler

import uvicorn

from .app import create_app
from .config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Absolute path to protected relay .env')
    parser.add_argument('--check', action='store_true', help='Validate config without printing secrets')
    args = parser.parse_args()
    try: settings = Settings.from_env(args.config)
    except Exception:
        parser.exit(2, 'Relay configuration invalid; check required keys, paths, URL and numeric limits.\n')
    if args.check:
        print('Relay configuration OK (secrets omitted)')
        return
    log_dir = settings.data_dir/'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir/'relay.log', maxBytes=10*1024**2, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    logging.getLogger('esop.relay').setLevel(logging.INFO)
    # No access logs: URLs, query strings and bearer headers must never reach logs.
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('httpcore').setLevel(logging.CRITICAL)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1,
                log_config=None, access_log=False, timeout_graceful_shutdown=30)


if __name__ == '__main__': main()
