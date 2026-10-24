#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：保留旧文件名，实际走 fetch_subs.py（B 站 + YouTube）。"""

from fetch_subs import main

if __name__ == "__main__":
    raise SystemExit(main())
