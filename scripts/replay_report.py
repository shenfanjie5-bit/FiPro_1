#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from app.workflows.checkpoint import get_latest_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description='Replay latest workflow checkpoint by thread_id')
    parser.add_argument('--thread-id', required=True)
    args = parser.parse_args()

    state = get_latest_checkpoint(args.thread_id)
    if not state:
        print('No checkpoint found for thread_id:', args.thread_id)
        return

    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
