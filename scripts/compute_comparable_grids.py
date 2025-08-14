#!/usr/bin/env python3
import os
import sys

# Ensure project root is on sys.path so 'app' can be imported when running from scripts/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import compute_comparable_grids

if __name__ == '__main__':
    date = '20250814'
    hour = 20
    res = compute_comparable_grids(date, hour)
    print('Wrote comparable grids for', date, hour)
    print('Count:', len(res.get('comparisons', [])))
